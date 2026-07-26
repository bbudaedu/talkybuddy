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

from server import bedrock_converse, guardrails, store

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


def _should_throttle(kind: str, student_id: str | None = None) -> bool:
    """檢查該 kind 的產出是否在節流時間內已產出過（避免騷擾）。

    **時區必須對齊**：store.add_agent_output 寫出的是 aware 時間戳
    （`2026-07-26T19:42:37+08:00`）。若這裡用 naive 的 `datetime.now()` 相減，
    會拋 `TypeError: can't subtract offset-naive and offset-aware datetimes`，
    節流靜默失效——這是本函式第一版的實際缺陷，因為例外被寬鬆的
    `except Exception` 吞掉，連日誌都沒有，測試又只驗自己 mock 的 naive 資料，
    所以一路綠燈到真機才會現形。

    例外處理刻意分兩層：store 讀取失敗（DB 壞掉、表不存在）是環境問題，
    容錯後不節流即可；但時間戳解析錯誤是程式 bug，必須留下 warning，
    否則下次還是查不到。
    """
    threshold_s = _THROTTLE_HOMEWORK_S if kind == "homework" else _THROTTLE_REPORT_S
    try:
        recent = store.list_agent_outputs(kind=kind, limit=1, student_id=student_id)
    except Exception:
        # 環境問題（DB 不可讀）：不影響決策，保守起見不節流
        _log.warning("節流查詢 store 失敗，本次不節流", exc_info=True)
        return False
    if not recent:
        return False
    ts_str = recent[0].get("ts", "")
    if not ts_str:
        return False
    try:
        ts = datetime.fromisoformat(ts_str)
        # store 一律寫 aware 時間戳；萬一遇到 naive（舊資料／人工寫入），
        # 補上與 store 相同的 +08:00 而非把 ts 轉成 naive，避免丟失資訊。
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_TZ_TAIPEI)
        return datetime.now(_TZ_TAIPEI) - ts < timedelta(seconds=threshold_s)
    except (TypeError, ValueError):
        # 時間戳格式問題屬程式/資料 bug，明確記錄，不要靜默吞掉
        _log.warning("節流時間戳無法判讀：%r，本次不節流", ts_str, exc_info=True)
        return False


def _deidentify_profile(profile: dict) -> dict:
    """對 profile 所有字串值去識別化；回傳新 dict。"""
    if not profile:
        return {}
    result: dict = {}
    for k, v in profile.items():
        if isinstance(v, str):
            result[k] = guardrails.deidentify(v[:_MAX_TEXT_LEN])
        elif isinstance(v, list):
            result[k] = [
                guardrails.deidentify(str(item)[:_MAX_TEXT_LEN])
                if isinstance(item, str) else item
                for item in v
            ]
        else:
            result[k] = v
    return result


def _deidentify_diagnosis(diagnosis: dict) -> dict:
    """對 diagnosis 自由文字欄位去識別化；回傳新 dict。"""
    if not diagnosis:
        return {}
    result: dict = dict(diagnosis)
    for key in ("emotional_status",):
        if isinstance(result.get(key), str):
            result[key] = guardrails.deidentify(result[key][:_MAX_TEXT_LEN])
    for key in ("strengths", "weaknesses"):
        if isinstance(result.get(key), list):
            result[key] = [
                guardrails.deidentify(str(s)[:_MAX_TEXT_LEN])
                if isinstance(s, str) else s
                for s in result[key]
            ]
    return result


def _deidentify_history(history: list[dict]) -> list[dict]:
    """批次去識別化 history 列表（只處理 scores 以外的自由文字欄位）。"""
    return [_deidentify_diagnosis(d) for d in (history or [])]


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
    1. allow_cloud=False → 直接走規則式，不碰任何雲端呼叫。
    2. allow_cloud=True → 嘗試 Bedrock Converse：
       a. 去識別化 profile / diagnosis / history 自由文字欄位。
       b. 呼叫 bedrock_converse.converse_text（cfg=resolve_config(role="diag")）。
       c. 對整體回傳字串做 passes_guardrail；不通過 → 降級。
       d. 解析並驗證回傳 JSON schema；不合法 → 降級。
       e. 任何例外 → 靜默 log 後降級。
    3. 規則式（離線 fallback）永遠保底，一定能產出合法結果。

    任何情況都不往外拋例外。
    """
    # 防禦性正規化
    profile = profile or {}
    diagnosis = diagnosis or {}
    history = list(history or [])
    turn_count = int(turn_count) if turn_count else 0

    # allow_cloud=False：最高優先閘門，連 resolve_config 都不呼叫
    if not allow_cloud:
        return _rule_based_decision(profile, diagnosis, history, turn_count)

    # 雲端路徑
    try:
        cfg = bedrock_converse.resolve_config(role="diag")
        if cfg is None:
            # 未啟用 Bedrock provider → 直接走規則式
            return _rule_based_decision(profile, diagnosis, history, turn_count)

        # 去識別化（上雲前對自由文字遮罩個資）
        profile_safe = _deidentify_profile(profile)
        diag_safe = _deidentify_diagnosis(diagnosis)
        history_safe = _deidentify_history(history)

        user_prompt = _build_user_prompt(profile_safe, diag_safe, history_safe, turn_count)

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
        return parsed

    except Exception:
        # 任何例外（網路、逾時、boto3 未安裝…）都不往外拋
        _log.exception("decide_next_actions 雲端路徑失敗，降級回規則式")
        return _rule_based_decision(profile, diagnosis, history, turn_count)
