"""diagnose.py — mock Hermes Agent + Bedrock Claude 雲端診斷層。

依 CONTRACTS.md 契約提供：

    generate_diagnosis(interactions: list[dict], prev: dict | None) -> dict

規則式（mock）為 demo 主線：
  1. 四維分數：fluency / vocabulary / grammar 取近期互動三維平均映射，
     pronunciation 由 asr_confidence 平均映射到 0-100；再與 prev 分數做
     0.6（新）/ 0.4（舊）平滑，避免曲線跳動。
  2. 樣式偵測：
     - AI 回應含「a/an」修正 → weaknesses 加入冠詞條目
     - student_text 中文字比例 > 40% → 「中英夾雜比例偏高」
     - 平均句長 < 4 個英文詞 → 「句子偏短」
  3. strengths 依高分維度與互動次數（願意開口）動態產生。
  4. emotional_status 依分數趨勢（升 → 自信提升；降 → 遇挫要鼓勵）。
  5. instructions 三欄（classroom / device / peer）依偵測到的弱點組合出
     具體可執行的老師建議，不寫死同一句。

若環境變數 ANTHROPIC_API_KEY 存在，會嘗試以 urllib 直呼 Anthropic
Messages API（model="claude-sonnet-5"）產生診斷 JSON；任何失敗（網路、
逾時、格式錯誤）一律 fallback 回上述規則式結果。純標準函式庫，import
期絕不載入重依賴、絕不對外連線。
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone

# Asia/Taipei 固定 UTC+8（無日光節約，直接用 timedelta 最穩）
_TAIPEI_TZ = timezone(timedelta(hours=8))

# 無任何互動、也無 prev 時的保底分數
_DEFAULT_SCORES = {"pronunciation": 60, "fluency": 55, "vocabulary": 60, "grammar": 55}

# 四維鍵順序（輸出 schema 固定）
_DIM_KEYS = ("pronunciation", "fluency", "vocabulary", "grammar")

# 微階梯：avg 分數 → level 字串（(上界, 名稱)，由低到高）
_LEVEL_BANDS = ((50, "L1"), (65, "L2"), (80, "L3"), (999, "L4"))

# 四維的中文名稱（供 strengths / 建議文案使用）
_DIM_ZH = {
    "pronunciation": "發音",
    "fluency": "口說流暢度",
    "vocabulary": "詞彙量",
    "grammar": "文法",
}

# Anthropic Messages API 參數
_API_URL = "https://api.anthropic.com/v1/messages"
_API_MODEL = "claude-sonnet-5"
_API_TIMEOUT_SEC = 12


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def available() -> bool:
    """診斷層永遠可用（規則式 mock 零依賴）。"""
    return True


def _today() -> str:
    """回傳台北時區的今日日期字串 YYYY-MM-DD。"""
    return datetime.now(_TAIPEI_TZ).strftime("%Y-%m-%d")


def _clamp(value: float, lo: int = 0, hi: int = 100) -> int:
    """夾住分數到 [0, 100] 並取整數。"""
    return int(max(lo, min(hi, round(value))))


def _chinese_ratio(text: str) -> float:
    """計算文字中「中文字元 / 非空白字元」比例，無字元回 0。"""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    cjk = sum(1 for c in chars if "一" <= c <= "鿿")
    return cjk / len(chars)


def _english_word_count(text: str) -> int:
    """粗略計算英文詞數（連續字母序列視為一個詞）。"""
    return len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", text))


def _has_giveup_signal(student_text: str) -> bool:
    """偵測學生放棄語（§3.3-A，低侵入、只看有寫進 DB 的字面）。"""
    t = student_text.strip().lower()
    if any(kw in student_text for kw in ("我不會", "不知道", "不會啦", "放棄", "不想")):
        return True
    if re.search(r"\b(i\s*don'?t\s*know|dunno|no\s*idea|give\s*up)\b", t):
        return True
    return False


def _has_article_correction(response_text: str) -> bool:
    """偵測 AI 回應是否含「a/an」冠詞修正。

    命中條件（任一）：
      - 直接出現字面 "a/an" 或「冠詞」
      - 修正提示語（應該／試試／試著／正確／要用／改成／記得）附近
        出現獨立的 a 或 an
    """
    if "a/an" in response_text or "冠詞" in response_text:
        return True
    if re.search(r"(應該|試試|試著|正確|要用|改成|記得).{0,40}\ban\b", response_text):
        return True
    if re.search(r"(應該|試試|試著|正確|要用|改成|記得).{0,40}\ba\b", response_text):
        return True
    return False


# ---------------------------------------------------------------------------
# 規則式診斷（mock Hermes Agent，demo 主線）
# ---------------------------------------------------------------------------

def _compute_scores(interactions: list[dict], prev: dict | None) -> dict:
    """由互動計算四維分數，並與 prev 做 0.6/0.4 平滑。"""
    prev_scores = (prev or {}).get("scores") or {}

    if interactions:
        # 互動三維平均 → fluency / vocabulary / grammar
        raw: dict[str, float] = {}
        for dim in ("fluency", "vocabulary", "grammar"):
            vals = [
                float(it.get("scores", {}).get(dim, _DEFAULT_SCORES[dim]))
                for it in interactions
            ]
            raw[dim] = sum(vals) / len(vals)
        # asr_confidence 平均（0-1）映射到 0-100 → pronunciation
        confs = [float(it.get("asr_confidence", 0.6)) for it in interactions]
        raw["pronunciation"] = (sum(confs) / len(confs)) * 100.0
    else:
        # 沒有新互動：沿用 prev；連 prev 都沒有就用保底值
        raw = {
            dim: float(prev_scores.get(dim, _DEFAULT_SCORES[dim]))
            for dim in _DIM_KEYS
        }

    smoothed = {}
    for dim in _DIM_KEYS:
        if dim in prev_scores:
            # 0.6 新 / 0.4 舊 平滑，避免每日分數跳動
            smoothed[dim] = _clamp(0.6 * raw[dim] + 0.4 * float(prev_scores[dim]))
        else:
            smoothed[dim] = _clamp(raw[dim])
    return smoothed


def _detect_patterns(interactions: list[dict]) -> dict:
    """規則式偵測互動樣式，回傳旗標與統計。"""
    flags = {
        "article_correction": False,   # 冠詞 a/an 曾被修正
        "high_chinese_ratio": False,   # 中文比例 > 40%
        "short_sentences": False,      # 平均句長 < 4 詞
        "article_hits": 0,
        "giveup_signal": 0,            # B3：近窗放棄語計數（沉默訊號選項 A）
    }
    if not interactions:
        return flags

    ratios: list[float] = []
    word_counts: list[int] = []
    for it in interactions:
        student = str(it.get("student_text", ""))
        response = str(it.get("ai_response_text", ""))
        ratios.append(_chinese_ratio(student))
        word_counts.append(_english_word_count(student))
        if _has_article_correction(response):
            flags["article_hits"] += 1
        if _has_giveup_signal(student):
            flags["giveup_signal"] += 1

    flags["article_correction"] = flags["article_hits"] > 0
    flags["high_chinese_ratio"] = (sum(ratios) / len(ratios)) > 0.40
    flags["short_sentences"] = (sum(word_counts) / len(word_counts)) < 4.0
    return flags


def _build_weaknesses(flags: dict, scores: dict) -> list[str]:
    """依偵測旗標與低分維度組出 weaknesses 條目。"""
    weaknesses: list[str] = []
    if flags["article_correction"]:
        weaknesses.append(
            f"冠詞 a/an 使用仍不穩定（近期互動中被修正 {flags['article_hits']} 次），"
            "常在母音開頭名詞前誤用 a"
        )
    if flags["high_chinese_ratio"]:
        weaknesses.append("中英夾雜比例偏高，超過四成內容以中文表達，尚未習慣整句輸出英文")
    if flags["short_sentences"]:
        weaknesses.append("句子偏短，平均少於 4 個英文詞，多為單詞或片語式回答")

    # 若規則都沒命中，退而以最低分維度提示，避免空清單
    if not weaknesses:
        low_dim = min(_DIM_KEYS, key=lambda d: scores[d])
        weaknesses.append(f"{_DIM_ZH[low_dim]}相對較弱（{scores[low_dim]} 分），建議持續加強")
    return weaknesses


def _build_strengths(scores: dict, interactions: list[dict]) -> list[str]:
    """依高分維度與互動次數組出 strengths 條目。"""
    strengths: list[str] = []
    # 高分維度（>= 65 視為亮點），最多取兩項
    high_dims = sorted(
        (d for d in _DIM_KEYS if scores[d] >= 65),
        key=lambda d: scores[d],
        reverse=True,
    )
    for dim in high_dims[:2]:
        strengths.append(f"{_DIM_ZH[dim]}表現穩定（{scores[dim]} 分），是目前的相對強項")

    n = len(interactions)
    if n >= 5:
        strengths.append(f"近期主動開口 {n} 次，練習意願高，願意反覆嘗試表達")
    elif n >= 1:
        strengths.append(f"近期有 {n} 次口說互動，願意開口嘗試，是進步的好起點")

    if not strengths:
        strengths.append("持續參與練習，學習態度值得肯定")
    return strengths


def _build_emotional_status(scores: dict, prev: dict | None, n_interactions: int) -> str:
    """依分數趨勢產生 emotional_status（升=自信提升／降=遇挫要鼓勵）。"""
    prev_scores = (prev or {}).get("scores") or {}
    if prev_scores:
        new_avg = sum(scores[d] for d in _DIM_KEYS) / len(_DIM_KEYS)
        old_avg = sum(float(prev_scores.get(d, scores[d])) for d in _DIM_KEYS) / len(_DIM_KEYS)
        delta = new_avg - old_avg
        if delta >= 1.5:
            return "整體分數較前次上升，開口意願與自信明顯提升，可適度增加挑戰"
        if delta <= -1.5:
            return "整體分數較前次下滑，可能在較難的句型上遇挫，需要多一點鼓勵與成功經驗"
        return "情緒與表現穩定，維持目前的練習節奏即可"
    # 無 prev：以互動量與絕對分數概估
    avg = sum(scores[d] for d in _DIM_KEYS) / len(_DIM_KEYS)
    if n_interactions >= 3 and avg >= 60:
        return "初期練習投入且表現不錯，正在建立開口說英文的自信"
    return "剛開始累積練習資料，建議先以鼓勵為主建立安全感"


def _build_instructions(flags: dict, scores: dict) -> dict:
    """依偵測到的弱點組出老師可執行的三欄建議（內容隨弱點變化）。"""
    # --- classroom：課堂活動 ---
    if flags["article_correction"]:
        classroom = (
            "課堂上安排 10 分鐘「a / an 分類賽」：把 apple、orange、banana、egg 等"
            "字卡讓學生分進 a / an 兩個籃子並大聲唸出完整句（I want an apple.），"
            "以遊戲方式固化母音開頭用 an 的規則"
        )
    elif flags["high_chinese_ratio"]:
        classroom = (
            "課堂採「英文優先 30 秒」規則：學生先用英文講 30 秒（講不出的詞可比手勢），"
            "之後才允許用中文補充，逐步降低中文依賴"
        )
    elif flags["short_sentences"]:
        classroom = (
            "課堂進行「句子加長」活動：老師給短句（I eat.），學生輪流各加一個詞"
            "（I eat an apple. → I eat an apple at school.），練習把句子講完整"
        )
    else:
        classroom = (
            "課堂安排 5 分鐘自由對話時間，讓學生用學過的句型描述今天的心情或午餐，"
            "維持開口頻率並累積成功經驗"
        )

    # --- device：裝置端練習主題 ---
    device_topics: list[str] = []
    if flags["article_correction"]:
        device_topics.append("冠詞句型跟讀（I want an apple. / I see a dog.）")
    if flags["short_sentences"]:
        device_topics.append("長句仿說（把 5 詞以上的句子完整說出）")
    if flags["high_chinese_ratio"]:
        device_topics.append("看圖說整句英文（減少中文替代）")
    if not device_topics:
        low_dim = min(_DIM_KEYS, key=lambda d: scores[d])
        device_topics.append(f"{_DIM_ZH[low_dim]}主題的每日跟讀")
    device = (
        "請學伴裝置本週推送練習主題："
        + "、".join(device_topics)
        + "；每日 2 回合、每回合 3 句，完成後給予稱讚回饋"
    )

    # --- peer：同儕配對方式 ---
    if flags["high_chinese_ratio"] or flags["short_sentences"]:
        peer = (
            "與口語較流暢的同學配對做「問答接龍」：由同伴用英文提問"
            "（What do you want to eat?），本生必須以完整英文句回答，"
            "同伴負責在句子太短或講中文時提醒重說一次"
        )
    elif flags["article_correction"]:
        peer = (
            "與同學兩人一組玩「找錯遊戲」：輪流唸含 a / an 的句子並故意唸錯，"
            "由對方抓錯並說出正確版本，互相強化冠詞語感"
        )
    else:
        peer = (
            "採混合程度兩人一組輪流當「小老師」，互相示範本週目標句型並給對方一個讚美，"
            "維持練習動機"
        )

    return {"classroom": classroom, "device": device, "peer": peer}


# ---------------------------------------------------------------------------
# B1 雙 Agent 閉環：companion_directive（陪聊下一輪策略指令）
# ---------------------------------------------------------------------------

def _avg(scores: dict) -> float:
    """四維平均。"""
    return sum(float(scores[d]) for d in _DIM_KEYS) / len(_DIM_KEYS)


def _level_for(scores: dict) -> str:
    """由四維平均映射微階梯字串（展示用）。"""
    a = _avg(scores)
    for hi, name in _LEVEL_BANDS:
        if a < hi:
            return name
    return "L4"


def _build_companion_directive(flags: dict, scores: dict, prev: dict | None) -> dict:
    """依既有 flags/scores/prev 組出陪聊下一輪策略指令（規則式，零依賴）。

    - difficulty 重用趨勢 delta 邏輯（與 _build_emotional_status 一致）。
    - goal/topic/questions 由最高優先旗標決定（與 _build_instructions 分流一致）。
    """
    # difficulty：趨勢 delta（avg 上升且達標 → up；下滑 → down；否則 hold）
    prev_scores = (prev or {}).get("scores") or {}
    if prev_scores:
        old_avg = sum(float(prev_scores.get(d, scores[d])) for d in _DIM_KEYS) / len(_DIM_KEYS)
        delta = _avg(scores) - old_avg
    else:
        delta = 0.0
    if delta >= 1.5 and _avg(scores) >= 60:
        difficulty = "up"
    elif delta <= -1.5:
        difficulty = "down"
    else:
        difficulty = "hold"

    # goal / topic / questions 由最高優先旗標決定
    if flags["article_correction"]:
        goal = "冠詞 a/an 穩定使用（母音開頭名詞前用 an）"
        topic = "食物與水果"
        questions = ["Do you want an apple?", "Is it an egg or a banana?"]
        fb = "若學生困惑，退回二選一：Is it a or an?"
    elif flags["high_chinese_ratio"]:
        goal = "整句英文輸出、減少中文替代"
        topic = "今天的一天"
        questions = ["What did you eat today?", "How are you today?"]
        fb = "若學生說中文，退回單字引導：先說一個英文單字就好。"
    elif flags["short_sentences"]:
        goal = "把句子講長、加上形容詞或地點"
        topic = "顏色與數量"
        questions = ["What color is it?", "How many do you have?"]
        fb = "若學生只說單字，退回二選一：Is it big or small?"
    elif difficulty == "up":
        goal = "升級句型：用 and / because 造複合句"
        topic = "喜歡的事物與原因"
        questions = ["What do you like and why?"]
        fb = "若學生卡住，退回單句：What do you like?"
    else:
        low_dim = min(_DIM_KEYS, key=lambda d: scores[d])
        goal = f"鞏固{_DIM_ZH[low_dim]}：多做成功經驗"
        topic = "日常問候與自我介紹"
        questions = ["What is your name?", "How old are you?"]
        fb = "若學生困惑，退回二選一。"

    return {
        "level": _level_for(scores),
        "difficulty": difficulty,
        "next_goal": goal,
        "topic": topic,
        "example_questions": questions,
        "fallback_hint": fb,
    }


def _normalize_directive(cd) -> dict | None:
    """正規化 companion_directive；格式不符回 None（呼叫端補規則式）。"""
    if not isinstance(cd, dict):
        return None
    try:
        qs = cd.get("example_questions") or []
        qs = [str(q) for q in qs if isinstance(q, str) and q.strip()][:3]
        out = {
            "level": str(cd.get("level", "")) or "L1",
            "difficulty": cd.get("difficulty") if cd.get("difficulty") in ("up", "hold", "down") else "hold",
            "next_goal": str(cd.get("next_goal", "")).strip(),
            "topic": str(cd.get("topic", "")).strip(),
            "example_questions": qs,
            "fallback_hint": str(cd.get("fallback_hint", "")).strip(),
        }
        if not (out["next_goal"] and out["topic"]):
            return None
        return out
    except Exception:
        return None


def format_directive_for_prompt(cd: dict | None) -> str | None:
    """把 companion_directive dict 壓成注入 prompt 的短中文區塊；None → None。"""
    if not cd:
        return None
    qs = " / ".join(cd.get("example_questions") or [])
    diff_zh = {"up": "可升句型", "down": "請降到二選一/單字", "hold": "維持難度"}.get(
        cd.get("difficulty"), "維持難度")
    return (
        "【本輪教學策略】"
        f"目標：{cd.get('next_goal', '')}；話題：{cd.get('topic', '')}；"
        f"難度：{diff_zh}；可用引導問句：{qs}；"
        f"困惑時：{cd.get('fallback_hint', '')}。"
        "請在稱讚後自然帶入此話題與問句；"
        "但「跟我說一遍：」後面仍必須逐字使用目標英文句，不可改寫。"
    )


def _rule_based_diagnosis(interactions: list[dict], prev: dict | None) -> dict:
    """規則式（mock Hermes Agent + Bedrock Claude）診斷主流程。"""
    interactions = interactions or []
    scores = _compute_scores(interactions, prev)
    flags = _detect_patterns(interactions)
    return {
        "date": _today(),
        "scores": scores,
        "strengths": _build_strengths(scores, interactions),
        "weaknesses": _build_weaknesses(flags, scores),
        "emotional_status": _build_emotional_status(scores, prev, len(interactions)),
        "instructions": _build_instructions(flags, scores),
        "companion_directive": _build_companion_directive(flags, scores, prev),
    }


# ---------------------------------------------------------------------------
# 真 API 路徑（有 ANTHROPIC_API_KEY 才嘗試；任何失敗 fallback 到 mock）
# ---------------------------------------------------------------------------

def _validate_diagnosis(d: dict) -> dict:
    """驗證並正規化 API 回傳的診斷，schema 不符即丟例外。"""
    if not isinstance(d, dict):
        raise ValueError("diagnosis 不是 dict")
    scores = d.get("scores")
    if not isinstance(scores, dict):
        raise ValueError("scores 缺失")
    norm_scores = {}
    for dim in _DIM_KEYS:
        if dim not in scores:
            raise ValueError(f"scores 缺 {dim}")
        norm_scores[dim] = _clamp(float(scores[dim]))
    strengths = d.get("strengths")
    weaknesses = d.get("weaknesses")
    if not (isinstance(strengths, list) and strengths and all(isinstance(s, str) for s in strengths)):
        raise ValueError("strengths 格式錯誤")
    if not (isinstance(weaknesses, list) and weaknesses and all(isinstance(s, str) for s in weaknesses)):
        raise ValueError("weaknesses 格式錯誤")
    emotional = d.get("emotional_status")
    if not (isinstance(emotional, str) and emotional.strip()):
        raise ValueError("emotional_status 格式錯誤")
    instructions = d.get("instructions")
    if not isinstance(instructions, dict):
        raise ValueError("instructions 缺失")
    norm_instr = {}
    for key in ("classroom", "device", "peer"):
        val = instructions.get(key)
        if not (isinstance(val, str) and val.strip()):
            raise ValueError(f"instructions.{key} 格式錯誤")
        norm_instr[key] = val
    return {
        "date": _today(),  # 日期一律以本地（台北）今日為準
        "scores": norm_scores,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "emotional_status": emotional,
        "instructions": norm_instr,
        # 可選欄位：軟性正規化，值可能為 None（由 generate_diagnosis 保底補上）
        "companion_directive": _normalize_directive(d.get("companion_directive")),
    }


def _call_anthropic_api(interactions: list[dict], prev: dict | None, api_key: str) -> dict:
    """以 urllib 直呼 Anthropic Messages API，要求輸出診斷 JSON。

    失敗（HTTP 錯誤、逾時、JSON 解析失敗、schema 不符）皆丟例外，
    由呼叫端 fallback 到規則式 mock。
    """
    # 只帶必要欄位，控制 prompt 長度
    brief = [
        {
            "student_text": str(it.get("student_text", ""))[:200],
            "ai_response_text": str(it.get("ai_response_text", ""))[:200],
            "asr_confidence": it.get("asr_confidence"),
            "scores": it.get("scores"),
        }
        for it in (interactions or [])[-10:]
    ]
    schema_example = {
        "date": _today(),
        "scores": {"pronunciation": 62, "fluency": 58, "vocabulary": 65, "grammar": 54},
        "strengths": ["..."],
        "weaknesses": ["..."],
        "emotional_status": "...",
        "instructions": {"classroom": "...", "device": "...", "peer": "..."},
        "companion_directive": {
            "level": "L2",
            "difficulty": "up | hold | down",
            "next_goal": "下一步教學目標（繁體中文）",
            "topic": "本輪要帶的話題",
            "example_questions": ["What color is the pig?", "Do you see a cat?"],
            "fallback_hint": "學生沉默或困惑時的降級指引",
        },
    }
    prompt = (
        "你是台灣國小英語學習診斷專家。以下是一位學生近期的口說互動紀錄"
        "（student_text 為學生原話、ai_response_text 為 AI 學伴回應、"
        "asr_confidence 為語音辨識信心 0-1、scores 為單次互動三維評分 0-100）：\n"
        + json.dumps(brief, ensure_ascii=False)
        + "\n\n前次診斷（可能為 null）：\n"
        + json.dumps(prev, ensure_ascii=False)
        + "\n\n請產出今日學習診斷，僅輸出一個 JSON 物件（不得有 markdown 圍欄或其他文字），"
        "所有文字內容用繁體中文（台灣用語），schema 與此範例完全一致：\n"
        + json.dumps(schema_example, ensure_ascii=False)
        + "\n\n注意：weaknesses 需具體（例如冠詞 a/an 誤用、中英夾雜比例、句長）；"
        "instructions 三欄分別給老師課堂活動、裝置端練習主題、同儕配對方式的具體建議；"
        "companion_directive 是給即時陪聊玩偶的下一輪策略（difficulty 依趨勢、"
        "next_goal/topic/example_questions 具體可帶入對話），example_questions 用英文、其餘用繁體中文。"
    )
    body = json.dumps(
        {
            "model": _API_MODEL,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        _API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_API_TIMEOUT_SEC) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    # 取第一個 text block；容忍模型仍包了 ```json 圍欄
    text = ""
    for block in payload.get("content", []):
        if block.get("type") == "text":
            text = block.get("text", "")
            break
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    return _validate_diagnosis(json.loads(text))


# ---------------------------------------------------------------------------
# 對外主函式（契約入口）
# ---------------------------------------------------------------------------

def generate_diagnosis(interactions: list[dict], prev: dict | None,
                       profile: dict | None = None) -> dict:
    """產生今日學習診斷 dict（欄位契約見 CONTRACTS.md）。

    有 ANTHROPIC_API_KEY 時先嘗試真 API（claude-sonnet-5），
    任何失敗即靜默 fallback 到規則式 mock —— mock 是 demo 主線。
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    result = None
    if api_key:
        try:
            result = _call_anthropic_api(interactions, prev, api_key)
        except Exception:
            # 雲端層失敗：不拋錯、不阻塞，改用邊緣端規則式診斷
            result = None
    if result is None:
        result = _rule_based_diagnosis(interactions, prev)
    # 收斂保底：不論走 API 或規則式，最終一定有合法 companion_directive
    flags = _detect_patterns(interactions or [])
    if not result.get("companion_directive"):
        result["companion_directive"] = _build_companion_directive(flags, result["scores"], prev)
    # B3：併入 CEFR level_state（遲滯用 prev.level_state；失敗不影響診斷）
    try:
        from server import curriculum
        result["level_state"] = curriculum.compute_level_state(
            result, profile, (prev or {}).get("level_state"), flags=flags)
    except Exception:
        result["level_state"] = None
    return result


# ---------------------------------------------------------------------------
# 直接執行時的自我驗證（python3 server/diagnose.py）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 5 筆假互動，其中兩筆含 a/an 修正
    fake_interactions = [
        {
            "student_text": "我想要吃 apple",
            "asr_confidence": 0.82,
            "ai_response_text": "你說得很棒！記得母音開頭要用 an 喔，a/an 要分清楚。試試看說：I want to eat an apple.",
            "scores": {"fluency": 55, "vocabulary": 60, "grammar": 50},
        },
        {
            "student_text": "我看到一個 orange",
            "asr_confidence": 0.88,
            "ai_response_text": "很接近了！orange 是母音開頭，應該用 an：I see an orange.（a/an 修正）",
            "scores": {"fluency": 60, "vocabulary": 62, "grammar": 48},
        },
        {
            "student_text": "我喜歡 dog",
            "asr_confidence": 0.75,
            "ai_response_text": "很好！整句可以說：I like dogs.",
            "scores": {"fluency": 52, "vocabulary": 58, "grammar": 45},
        },
        {
            "student_text": "我要 book",
            "asr_confidence": 0.9,
            "ai_response_text": "對，book 是書！可以說：This is my book.",
            "scores": {"fluency": 50, "vocabulary": 55, "grammar": 50},
        },
        {
            "student_text": "今天我很開心 happy",
            "asr_confidence": 0.7,
            "ai_response_text": "太棒了！可以說：I am happy today.",
            "scores": {"fluency": 58, "vocabulary": 60, "grammar": 52},
        },
    ]
    fake_prev = {
        "date": "2026-07-02",
        "scores": {"pronunciation": 62, "fluency": 58, "vocabulary": 65, "grammar": 54},
        "strengths": ["願意開口"],
        "weaknesses": ["句子偏短"],
        "emotional_status": "穩定",
        "instructions": {"classroom": "…", "device": "…", "peer": "…"},
    }

    result = generate_diagnosis(fake_interactions, fake_prev)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # --- schema 逐欄檢查 ---
    assert isinstance(result["date"], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", result["date"])
    assert set(result["scores"].keys()) == set(_DIM_KEYS)
    for _dim, _v in result["scores"].items():
        assert isinstance(_v, int) and 0 <= _v <= 100, (_dim, _v)
    assert isinstance(result["strengths"], list) and result["strengths"]
    assert all(isinstance(s, str) for s in result["strengths"])
    assert isinstance(result["weaknesses"], list) and result["weaknesses"]
    assert all(isinstance(s, str) for s in result["weaknesses"])
    assert isinstance(result["emotional_status"], str) and result["emotional_status"]
    assert set(result["instructions"].keys()) == {"classroom", "device", "peer"}
    assert all(isinstance(v, str) and v for v in result["instructions"].values())
    # weaknesses 必須含冠詞條目（兩筆互動含 a/an 修正）
    assert any("冠詞" in w for w in result["weaknesses"]), result["weaknesses"]
    # 中文比例偏高（假資料多為中英夾雜）
    assert any("夾雜" in w for w in result["weaknesses"]), result["weaknesses"]
    # 平滑檢查：pronunciation = 0.6*81 + 0.4*62 ≈ 73
    assert 70 <= result["scores"]["pronunciation"] <= 76, result["scores"]

    # 無 prev、無互動的極端情況也不可炸
    empty = generate_diagnosis([], None)
    assert set(empty["scores"].keys()) == set(_DIM_KEYS)
    assert empty["strengths"] and empty["weaknesses"]

    # 趨勢：分數大幅下滑 → 應出現鼓勵語
    low_interactions = [
        {
            "student_text": "no",
            "asr_confidence": 0.3,
            "ai_response_text": "沒關係，慢慢來！",
            "scores": {"fluency": 30, "vocabulary": 30, "grammar": 30},
        }
    ]
    down = generate_diagnosis(low_interactions, fake_prev)
    assert "鼓勵" in down["emotional_status"], down["emotional_status"]

    print("\n[OK] diagnose.py schema 逐欄驗證通過")
