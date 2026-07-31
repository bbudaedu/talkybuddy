# -*- coding: utf-8 -*-
"""orchestrator.py — 決策判斷／中央編排 agent（子專案 E）。

公開契約：
    decide_next_actions(
        profile: dict,
        diagnosis: dict,
        history: list[dict],
        turn_count: int,
        *,
        allow_cloud: bool = True,
    ) -> dict

回傳固定 schema（雲端與離線格式完全一致）：
    {
        "actions": [str],     # 子集合 of ["homework", "report"]；可以是空 list
        "reason": str,        # 為什麼這樣決定，一到兩句完整中文
        "priority": str,      # "low" | "normal" | "high"
        "source": "cloud" | "rule",
    }

設計原則（與 homework / report 一致）：
- E 只做決策，不執行。不可以呼叫 homework.generate_homework 或 report.generate_report。
- 雲端路徑走 bedrock_converse.converse_text，cfg 用 resolve_config(role="diag")。
- allow_cloud=False 完全不觸雲端，連 resolve_config 都不呼叫。
- 上雲前對 profile / diagnosis / history 自由文字經 guardrails.deidentify。
- 雲端回覆整體 JSON 字串經 guardrails.passes_guardrail；不通過降級回規則式。
- 任何例外不往外拋，一律靜默降級回規則式；規則式永遠能產出合法結果。
- 決策考慮：弱項嚴重度、趨勢（連續退步 > 單次低分）、頻率控制（節流）、資料不足誠實回報。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from server import agent_backends, agentcore, bedrock_converse, guardrails, store
from server.agents import privacy

_log = logging.getLogger(__name__)

# 雲端呼叫逾時（秒）。決策判斷是非同步路徑，沿用 homework / report 的 12s。
_TIMEOUT_S = 12.0

# 最多傳給雲端的自由文字長度（字元）
_MAX_TEXT_LEN = 200

# 四維鍵（與 diagnose / homework / report 一致）
_DIM_KEYS = ("pronunciation", "fluency", "vocabulary", "grammar")

# 四維中文名稱
_DIM_ZH = {
    "pronunciation": "發音",
    "fluency": "口說流暢度",
    "vocabulary": "詞彙量",
    "grammar": "文法",
}

# 分數嚴重度分級（決定是否需要派作業）
_SCORE_SERIOUS = 60  # < 60 視為需要加強
_SCORE_WEAK = 70     # < 70 視為偏弱

# 趨勢判定閾值（首尾分差）
_TREND_THRESHOLD = 5

# 節流用的時區。必須與 store.add_agent_output 寫入時所用的一致（UTC+8），
# 否則比較會失準或拋型別錯誤。
_TZ_TAIPEI = timezone(timedelta(hours=8))

# 節流間隔（秒）：同一 kind 的 agent 產出在此時間內不重複派發。
# 決賽現場一場 demo 可能只有幾分鐘，牆鐘節流窗若比 demo 還短就形同不存在，
# 故刻意設得比單場 demo 長；正式營運要放寬則改這兩個常數即可。
_THROTTLE_HOMEWORK_S = 1800  # 作業：30 分鐘內不重複
_THROTTLE_REPORT_S = 7200    # 報告：2 小時內不重複

# 定期回報保底：週報超過這個時間沒產出就補派一份，不論趨勢好壞。
# 七天＝「週」報字面上的承諾。與節流不衝突（7 天 >> 2 小時），補派後
# 下一次刷新就不再過期，自然自我限制。見 _apply_periodic_report_floor。
_STALE_REPORT_S = 7 * 86400

# 最少需要的 history 筆數來計算趨勢
_MIN_HISTORY_FOR_TREND = 2


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

def _find_lowest_dim(scores: dict) -> str:
    """找出四維中得分最低的維度；全部缺失則預設 grammar。"""
    if not scores:
        return "grammar"
    valid = {d: float(scores.get(d, 100)) for d in _DIM_KEYS if d in scores}
    if not valid:
        return "grammar"
    return min(valid, key=lambda d: valid[d])


def _trend(scores_list: list[float]) -> str:
    """判定一組分數序列的趨勢：'improving' / 'declining' / 'stable'。"""
    if len(scores_list) < _MIN_HISTORY_FOR_TREND:
        return "stable"
    delta = scores_list[-1] - scores_list[0]
    if delta >= _TREND_THRESHOLD:
        return "improving"
    if delta <= -_TREND_THRESHOLD:
        return "declining"
    return "stable"


def _extract_dim_scores(diagnoses: list[dict]) -> dict[str, list[float]]:
    """從 diagnoses 列表提取每個維度的分數序列（保留時序，最舊在前）。"""
    result: dict[str, list[float]] = {d: [] for d in _DIM_KEYS}
    for diag in diagnoses:
        scores = (diag or {}).get("scores") or {}
        for dim in _DIM_KEYS:
            val = scores.get(dim)
            if val is not None:
                try:
                    result[dim].append(float(val))
                except (TypeError, ValueError):
                    pass
    return result


def _latest_scores(dim_scores: dict[str, list[float]]) -> dict[str, float]:
    """回傳每個維度的最新分數；若無任何紀錄，填入中性預設值 65。"""
    return {
        dim: (dim_scores[dim][-1] if dim_scores[dim] else 65.0)
        for dim in _DIM_KEYS
    }


_NO_OUTPUT_YET = float("inf")


def _last_output_age_s(kind: str, student_id: str | None = None) -> float | None:
    """該 kind 最近一筆產出距今幾秒。三種回傳值代表三件不同的事：

    - 秒數：確實查到了時間
    - ``_NO_OUTPUT_YET``（``inf``）：查得到，但**從來沒產出過**
    - ``None``：**查不到**（DB 讀取失敗／時間戳壞掉）

    `inf` 與 `None` 必須分開，不能都當成「很久以前」：從沒產出過應該要補派
    第一份週報，但 DB 壞掉時我們**根本沒有證據**，此時每輪都補派一次只是
    在錯誤狀態上疊加動作。沒有證據就不動作。

    節流（`_should_throttle`）與過期補派（`_report_is_stale`）共用這一份查詢，
    刻意不各寫一份——這段時間戳解析已經出過一次真實缺陷：

    **時區必須對齊**：store.add_agent_output 寫出的是 aware 時間戳
    （`2026-07-26T19:42:37+08:00`）。第一版用 naive 的 `datetime.now()` 相減，
    拋 `TypeError: can't subtract offset-naive and offset-aware datetimes`，
    節流靜默失效；例外被寬鬆的 `except Exception` 吞掉，連日誌都沒有，
    測試又只驗自己 mock 的 naive 資料，所以一路綠燈到真機才現形。

    例外處理刻意分兩層：store 讀取失敗（DB 壞掉、表不存在）是環境問題；
    時間戳解析錯誤是程式 bug，必須留下 warning，否則下次還是查不到。
    """
    # student_id 缺失時**不可以**原樣傳 None：store.list_agent_outputs(None)
    # 是「所有學生」（不加 WHERE），於是任何一個孩子剛拿到作業，就會把其他
    # 所有孩子一起擋住。寫入端（store.add_agent_output）在 student_id 省略時
    # 用的是 config.STUDENT_ID，讀取端必須對齊同一個預設值。
    sid = student_id if student_id else store.default_student_id()
    try:
        recent = store.list_agent_outputs(kind=kind, limit=1, student_id=sid)
    except Exception:
        _log.warning("agent 產出時間查詢失敗（kind=%s）", kind, exc_info=True)
        return None
    if not recent:
        return _NO_OUTPUT_YET
    ts_str = recent[0].get("ts", "")
    if not ts_str:
        _log.warning("agent 產出 kind=%s 缺 ts 欄位", kind)
        return None
    try:
        ts = datetime.fromisoformat(ts_str)
        # store 一律寫 aware 時間戳；萬一遇到 naive（舊資料／人工寫入），
        # 補上與 store 相同的 +08:00 而非把 ts 轉成 naive，避免丟失資訊。
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_TZ_TAIPEI)
        return (datetime.now(_TZ_TAIPEI) - ts).total_seconds()
    except (TypeError, ValueError):
        # 時間戳格式問題屬程式/資料 bug，明確記錄，不要靜默吞掉
        _log.warning("agent 產出時間戳無法判讀：%r", ts_str, exc_info=True)
        return None


def _should_throttle(kind: str, student_id: str | None = None) -> bool:
    """檢查該 kind 的產出是否在節流時間內已產出過（避免騷擾）。

    讀不到時間（DB 壞掉／沒產出過）→ 不節流，保守放行（與改版前語意一致）。
    """
    threshold_s = _THROTTLE_HOMEWORK_S if kind == "homework" else _THROTTLE_REPORT_S
    age = _last_output_age_s(kind, student_id)
    return age is not None and age < threshold_s


def _report_is_stale(student_id: str | None = None) -> bool:
    """距離上次學習週報是否已超過 `_STALE_REPORT_S`（含從未產出過）。

    查不到時間（`None`）→ False：沒有證據就不補派，別在 DB 出問題時
    每輪都多送一份出去。
    """
    age = _last_output_age_s("report", student_id)
    return age is not None and age >= _STALE_REPORT_S


def _apply_periodic_report_floor(decision: dict, student_id: str | None) -> dict:
    """定期回報保底：週報過期就補派一份，不論趨勢好壞。

    為什麼需要這道 floor（這是產品缺陷，不只是 demo 佈景）：
    `_rule_based_decision` 只在 `overall_trend == "declining"` 時派 report，
    於是**一個持續進步的孩子，家長永遠收不到週報**。而「週報」這個名字
    承諾的是定期回報，不是壞消息通知。2026-07-30 端到端驗收就撞到這個形狀：
    demo 學生四維 89/67/67/64、趨勢 improving，跑完六輪對話 actions 仍是 []，
    教師儀表板上只有四天前的舊卡片。

    刻意放在 `_decide_next_actions` 的**共同出口**而非只放進規則式分支：
    雲端 LLM 同樣可能連續多輪都不派，定期回報不該取決於模型當下的判斷。

    三個克制：
    1. 只補 `report`。作業是需求驅動（弱項／到期詞），不是週期性的，
       無條件補作業等於騷擾。
    2. 不動 `priority`。定期回報本來就不是高優先事件。
    3. 不覆寫 `reason`，只追加一句——原本那句說明的是趨勢判斷，仍然成立。

    `source` 一律維持 base decision 的值：這份週報的**決策**確實是規則保底，
    但欄位語意是「這次決策由誰產生」，改掉會讓儀表板的雲端/離線徽章說謊。
    """
    actions = list(decision.get("actions") or [])
    if "report" in actions:
        return decision
    if not _report_is_stale(student_id):
        return decision
    actions.append("report")
    days = int(_STALE_REPORT_S // 86400)
    reason = (decision.get("reason") or "").rstrip("。")
    extra = f"另外，距離上次學習週報已超過 {days} 天，依定期回報原則產生一份給家長參考"
    decision["actions"] = actions
    decision["reason"] = f"{reason}；{extra}。" if reason else f"{extra}。"
    return decision


def _deidentify_profile(profile: dict) -> dict:
    """profile 上雲前投影（白名單，見 agents/privacy.py）。"""
    return privacy.safe_profile(profile)


def _deidentify_diagnosis(diagnosis: dict) -> dict:
    """diagnosis 上雲前投影（白名單，見 agents/privacy.py）。"""
    return privacy.safe_diagnosis(diagnosis)


def _deidentify_history(history: list[dict]) -> list[dict]:
    """批次投影 history 列表。"""
    return privacy.safe_diagnoses(history)


# ---------------------------------------------------------------------------
# 規則式決策邏輯（離線 fallback，永遠可用）
# ---------------------------------------------------------------------------

def _rule_based_decision(
    profile: dict,
    diagnosis: dict,
    history: list[dict],
    turn_count: int,
) -> dict:
    """規則式決策（完全離線，零外部依賴）。

    決策邏輯：
    1. 資料不足（history 為空或只有一筆）→ 傾向空 actions，reason 誠實說明觀察中。
    2. 弱項嚴重（最低維度 < 60）且未節流 → 派 homework。
    3. 趨勢連續退步（任一維度 declining）且未節流 → 派 report。
    4. 優異穩定（全部 > 80 且 stable）→ 空 actions，低優先級。
    5. priority：退步/嚴重弱項 → high，偏弱/趨勢波動 → normal，優異穩定 → low。
    """
    profile = profile or {}
    diagnosis = diagnosis or {}
    history = list(history or [])

    student_id = profile.get("student_id")
    # 當前診斷的分數（最新的診斷，優先級最高）
    current_scores = diagnosis.get("scores") or {}
    # 從 history 提取歷史分數序列
    dim_scores = _extract_dim_scores(history)
    # 將當前分數加入序列尾部（作為最新一筆）
    for dim in _DIM_KEYS:
        val = current_scores.get(dim)
        if val is not None:
            try:
                dim_scores[dim].append(float(val))
            except (TypeError, ValueError):
                pass
    # 取最新分數（已包含當前 diagnosis）
    latest = _latest_scores(dim_scores)
    weakest_dim = _find_lowest_dim(latest)
    weakest_score = latest.get(weakest_dim, 65.0)
    weakest_zh = _DIM_ZH.get(weakest_dim, weakest_dim)

    actions: list[str] = []
    reason_parts: list[str] = []
    priority = "normal"

    # ── 情境 1：資料不足（無法計算趨勢）─────────────────────────────────
    if len(history) < _MIN_HISTORY_FOR_TREND:
        reason = (
            f"目前練習次數較少（累計 {turn_count} 回合），"
            f"尚無足夠資料進行學習分析。系統將繼續觀察學習狀況。"
        )
        return {
            "actions": [],
            "reason": reason,
            "priority": "low",
            "source": "rule",
        }

    # ── 情境 2+：有足夠資料，分析弱項與趨勢 ──────────────────────────
    # 計算每個維度的趨勢
    dim_trends: dict[str, str] = {}
    for dim in _DIM_KEYS:
        dim_trends[dim] = _trend(dim_scores[dim])

    # 整體趨勢：多數決（declining > stable > improving 同票時偏保守）
    trend_counts = {"improving": 0, "stable": 0, "declining": 0}
    for t in dim_trends.values():
        trend_counts[t] += 1
    if trend_counts["declining"] > trend_counts["improving"]:
        overall_trend = "declining"
    elif trend_counts["improving"] >= trend_counts["declining"] and trend_counts["improving"] > 0:
        overall_trend = "improving"
    else:
        overall_trend = "stable"

    # ── 決策邏輯：嚴重弱項 → homework ──────────────────────────────────
    if weakest_score < _SCORE_SERIOUS:
        if not _should_throttle("homework", student_id):
            actions.append("homework")
            reason_parts.append(
                f"最新練習中{weakest_zh}為 {int(weakest_score)} 分，低於基礎水準，"
                f"建議派發針對性作業加強練習"
            )
            priority = "high"
        else:
            reason_parts.append(
                f"{weakest_zh}仍需加強（{int(weakest_score)} 分），"
                f"但最近已派發過作業，暫不重複派發"
            )
            priority = "normal"
    elif weakest_score < _SCORE_WEAK:
        # 偏弱但未嚴重，僅在趨勢退步時派作業
        if overall_trend == "declining" and not _should_throttle("homework", student_id):
            actions.append("homework")
            reason_parts.append(
                f"{weakest_zh}近期有退步跡象（目前 {int(weakest_score)} 分），"
                f"建議派發作業以穩定學習狀態"
            )
            priority = "high"

    # ── 決策邏輯：連續退步 → report ─────────────────────────────────────
    if overall_trend == "declining":
        if not _should_throttle("report", student_id):
            actions.append("report")
            reason_parts.append(
                f"整體學習狀態出現退步趨勢，建議產生學習報告供家長與教師參考"
            )
            priority = "high"
        else:
            if not reason_parts:
                reason_parts.append(
                    f"學習狀態有下滑跡象，但最近已產生過報告，暫不重複通知"
                )
    elif overall_trend == "improving":
        # 進步情境：優先級降低，鼓勵為主
        if not reason_parts:
            reason_parts.append(
                f"學習狀態持續進步（{weakest_zh} {int(weakest_score)} 分），"
                f"建議維持目前的練習節奏"
            )
        priority = "low"
    else:
        # 穩定情境
        if not reason_parts:
            if all(s >= 80 for s in latest.values()):
                reason_parts.append(
                    f"四個學習面向表現均優異且穩定，建議持續鞏固現有能力"
                )
                priority = "low"
            else:
                reason_parts.append(
                    f"學習狀態維持穩定，{weakest_zh}是相對偏弱的面向（{int(weakest_score)} 分），"
                    f"可考慮在後續練習中加強"
                )
                priority = "normal"

    # actions 去重（理論上不會重複，但防禦性去重）
    actions = list(dict.fromkeys(actions))

    # 組合 reason（確保通順完整）
    reason = "；".join(reason_parts) + "。" if reason_parts else "維持穩定觀察。"

    return {
        "actions": actions,
        "reason": reason,
        "priority": priority,
        "source": "rule",
    }


# ---------------------------------------------------------------------------
# 雲端 prompt 組裝
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "你是台灣國小英語學習「決策判斷」專家。根據學生的 profile、最新診斷、歷史診斷資料，"
    "決定下一步要執行哪些 agent 行動（從 'homework'（派作業）、'report'（產生報告）中選擇，"
    "可選多個或不選）。"
    "同時說明為什麼這樣決定（reason，一到兩句完整中文），以及優先級（priority: low / normal / high）。"
    "決策考慮：弱項嚴重度、趨勢（連續退步 > 單次低分）、頻率控制（不可每次都派）。"
    "資料不足時誠實回報「觀察中」，不虛構趨勢。"
    "只輸出一個 JSON 物件，不得有 markdown 圍欄或額外文字。"
    "所有 reason 使用繁體中文（台灣用語）。"
)


def _build_user_prompt(
    profile_safe: dict,
    diag_safe: dict,
    history_safe: list[dict],
    turn_count: int,
) -> str:
    """組裝送給雲端的 user prompt（已去識別化）。"""
    schema_example = {
        "actions": ["homework"],
        "reason": "學生在文法面向表現偏弱且近期有退步趨勢，建議派發針對性作業加強練習",
        "priority": "high",
        "source": "cloud",
    }
    # 只傳最近 5 筆診斷（避免 prompt 過大）
    recent_history = history_safe[-5:] if len(history_safe) > 5 else history_safe
    return (
        f"累計成功回合數：{turn_count}\n"
        f"學生 profile（已去識別化）：{json.dumps(profile_safe, ensure_ascii=False)[:300]}\n"
        f"最新診斷：{json.dumps(diag_safe, ensure_ascii=False)[:500]}\n"
        f"歷史診斷（時序，最舊在前，最近 5 筆）：\n{json.dumps(recent_history, ensure_ascii=False)[:1000]}\n\n"
        "請根據上述資料，決定下一步要執行哪些 agent 行動。"
        "actions 只能從 ['homework', 'report'] 中選擇（可選多個或空 list）。"
        "reason 必須是完整的中文句子，說明為什麼這樣決定。"
        "priority 從 'low' / 'normal' / 'high' 中選擇。"
        "僅輸出符合以下 schema 的 JSON 物件（source 固定為 \"cloud\"）：\n"
        + json.dumps(schema_example, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# 雲端回應解析與驗證
# ---------------------------------------------------------------------------

def _validate_cloud_response(data: dict) -> bool:
    """驗證雲端回傳的 dict 符合決策 schema；不符回 False。"""
    if not isinstance(data, dict):
        return False
    # actions
    actions = data.get("actions")
    if not isinstance(actions, list):
        return False
    for a in actions:
        if a not in ["homework", "report"]:
            return False
    # actions 不得重複
    if len(actions) != len(set(actions)):
        return False
    # reason
    if not (isinstance(data.get("reason"), str) and str(data["reason"]).strip()):
        return False
    # priority
    if data.get("priority") not in ("low", "normal", "high"):
        return False
    return True


def _parse_cloud_response(text: str) -> dict | None:
    """解析雲端回傳的 JSON 字串並驗證 schema；任何問題回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    # 容忍 ```json 圍欄
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not _validate_cloud_response(data):
        return None
    return data


# ---------------------------------------------------------------------------
# 公開契約入口
# ---------------------------------------------------------------------------

def decide_next_actions(
    profile: dict,
    diagnosis: dict,
    history: list[dict],
    turn_count: int,
    *,
    allow_cloud: bool = True,
) -> dict:
    """決策判斷 agent 主入口。

    流程：
    1. allow_cloud=False 或未取得家長同意 → 直接走規則式，不碰任何雲端呼叫。
    2. allow_cloud=True → 嘗試 Bedrock Converse：
       a. 去識別化 profile / diagnosis / history 自由文字欄位。
       b. 呼叫 bedrock_converse.converse_text（cfg=resolve_config(role="diag")）。
       c. 對整體回傳字串做 passes_guardrail；不通過 → 降級。
       d. 解析並驗證回傳 JSON schema；不合法 → 降級。
       e. 任何例外 → 靜默 log 後降級。
    3. 規則式（離線 fallback）永遠保底，一定能產出合法結果。

    任何情況都不往外拋例外——**包含規則式路徑自己爆掉**。
    """
    try:
        decision = _decide_next_actions(
            profile, diagnosis, history, turn_count, allow_cloud=allow_cloud
        )
        # 定期回報保底。放在**共同出口**，雲端與規則式兩條路徑一視同仁。
        # 刻意在 try 之內：若上面整段炸掉走 _minimal_decision()，那代表決策層
        # 失效，此時寧可什麼都不派，也不該由 floor 自行補一份出去。
        sid = (profile or {}).get("student_id") if isinstance(profile, dict) else None
        return _apply_periodic_report_floor(decision, sid)
    except Exception:
        _log.exception("decide_next_actions 全數路徑失敗，本輪不派發任何行動")
        return _minimal_decision()


def _minimal_decision() -> dict:
    """最小合法決策：什麼都不派。決策層失效時，寧可不派也不要亂派。"""
    return {
        "actions": [],
        "reason": "決策資料暫時無法判讀，本輪維持觀察，不派發新的作業或報告。",
        "priority": "low",
        "source": "rule",
    }


def _decide_next_actions(profile, diagnosis, history, turn_count, *, allow_cloud: bool) -> dict:
    # 防禦性正規化
    profile = profile if isinstance(profile, dict) else {}
    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
    history = list(history) if isinstance(history, (list, tuple)) else []
    try:
        turn_count = int(turn_count) if turn_count else 0
    except (TypeError, ValueError):
        turn_count = 0

    # allow_cloud=False：最高優先閘門，連 resolve_config 都不呼叫。
    # consent 同級：家長同意是資料出境的 chokepoint（見 diagnose.py）。
    if not allow_cloud or not guardrails.consent_granted():
        return _rule_based_decision(profile, diagnosis, history, turn_count)

    # 雲端路徑。後端優先序：AgentCore Harness → Bedrock Converse → 規則式。
    try:
        # 兩個後端**都要**解析；理由見 server/agent_backends.py 的模組說明。
        ac_cfg, cfg = agent_backends.resolve("orchestrator")
        if ac_cfg is None and cfg is None:
            # 兩個雲端後端都沒設定 → 直接走規則式
            return _rule_based_decision(profile, diagnosis, history, turn_count)

        # 去識別化（上雲前對自由文字遮罩個資）
        profile_safe = _deidentify_profile(profile)
        diag_safe = _deidentify_diagnosis(diagnosis)
        history_safe = _deidentify_history(history)

        user_prompt = _build_user_prompt(profile_safe, diag_safe, history_safe, turn_count)

        raw_text = None
        if ac_cfg is not None:
            # AgentCore：system prompt 在 Harness 建立時已宣告，這裡只送訊息。
            # session_id 用回合數綁定，同一個教學循環的多次呼叫落在同一 session。
            try:
                raw_text = agentcore.invoke(
                    ac_cfg, user_prompt,
                    actor_id=(profile or {}).get("student_id"),
                    session_id=f"orch-turn-{turn_count}",
                )
            except Exception:
                # 只降一級：還有 Bedrock 可打就打 Bedrock，不要一路摔到規則式。
                _log.exception("decide_next_actions AgentCore 失敗，改試 Bedrock Converse")
                raw_text = None

        if raw_text is None:
            if cfg is None:
                # 第二層沒設定，鏈到底了
                return _rule_based_decision(profile, diagnosis, history, turn_count)
            raw_text = bedrock_converse.converse_text(
                _SYSTEM_PROMPT,
                user_prompt,
                cfg=cfg,
                max_tokens=256,
                timeout_s=_TIMEOUT_S,
            )

        # 護欄：整體回傳字串過安全過濾
        if not guardrails.passes_guardrail(raw_text):
            _log.warning("decide_next_actions 雲端回覆未通過護欄，降級回規則式")
            return _rule_based_decision(profile, diagnosis, history, turn_count)

        # 解析與 schema 驗證
        parsed = _parse_cloud_response(raw_text)
        if parsed is None:
            _log.warning("decide_next_actions 雲端回覆 schema 不合法，降級回規則式")
            return _rule_based_decision(profile, diagnosis, history, turn_count)

        # 強制標記 source 為 cloud
        parsed["source"] = "cloud"
        # 節流必須對雲端決策同樣生效。先前只有規則式分支過這道閘，
        # 雲端模式下每次背景刷新都可能派新作業——把頻率控制寫在 system
        # prompt 裡「請 LLM 自律」不是控制。兩條路徑共用同一道閘。
        sid = (profile or {}).get("student_id")
        kept = [a for a in parsed["actions"] if not _should_throttle(a, sid)]
        if kept != parsed["actions"]:
            _log.info("雲端決策經節流過濾：%s → %s", parsed["actions"], kept)
            parsed["actions"] = kept
        return parsed

    except Exception:
        # 任何例外（網路、逾時、boto3 未安裝…）都不往外拋
        _log.exception("decide_next_actions 雲端路徑失敗，降級回規則式")
        return _rule_based_decision(profile, diagnosis, history, turn_count)
