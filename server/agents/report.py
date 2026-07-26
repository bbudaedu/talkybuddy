# -*- coding: utf-8 -*-
"""report.py — 家長／教師週報 agent（子專案 C）。

公開契約：
    generate_report(profile: dict, diagnoses: list[dict], *, allow_cloud: bool = True) -> dict

回傳固定 schema（雲端與離線格式完全一致）：
    {
        "period":      str,        # 涵蓋期間的人話描述，例如「最近 5 次練習」
        "summary":     str,        # 2-3 句總結，給家長看的第一段
        "highlights":  [str],      # 1-3 條進步或亮點
        "concerns":    [str],      # 1-3 條需要留意的地方
        "suggestions": [str],      # 1-3 條家長在家可以怎麼陪
        "source":      "cloud" | "rule"
    }

設計原則（與 homework.py / cloud_llm / diagnose 一致）：
- 讀者是家長與老師，用成人看的完整敘述，不用對小孩說話的語氣。
- 雲端路徑走 bedrock_converse.converse_text，cfg 用 resolve_config(role="diag")。
- allow_cloud=False 完全不觸雲端，連 resolve_config 都不呼叫。
- 上雲前 profile / diagnoses 自由文字經 guardrails.deidentify。
- 雲端回覆整體 JSON 字串經 guardrails.passes_guardrail；不通過降級回規則式。
- 任何例外不往外拋，一律靜默降級回規則式；規則式永遠能產出合法結果。
- 規則式 fallback 是真的敘事：從分數算趨勢，用完整句子描述，不是數字傾印。
"""

from __future__ import annotations

import json
import logging
import re

from server import agentcore, bedrock_converse, guardrails
from server.agents import privacy

_log = logging.getLogger(__name__)

# 雲端呼叫逾時（秒）。週報是非同步路徑，品質優先，沿用 homework 的 12s。
_TIMEOUT_S = 12.0

# 四維鍵（與 diagnose 一致）
_DIM_KEYS = ("pronunciation", "fluency", "vocabulary", "grammar")

# 四維中文名稱（報告文字用）
_DIM_ZH = {
    "pronunciation": "發音",
    "fluency": "口說流暢度",
    "vocabulary": "詞彙量",
    "grammar": "文法",
}

# 分數描述帶（與 homework 一致的微階梯，用於把數字轉成文字描述）
_SCORE_BANDS = (
    (50, "仍需加強"),
    (65, "達到基礎水準"),
    (80, "表現良好"),
    (101, "表現優秀"),
)


def _score_desc(score: float) -> str:
    """把 0-100 分映射到人話描述，供週報中把數字說成意義。"""
    for ceiling, label in _SCORE_BANDS:
        if score < ceiling:
            return label
    return "表現優秀"


# 趨勢判定閾值（首尾分差超過此值視為進步/退步）
_TREND_THRESHOLD = 5


def _trend(scores_list: list[float]) -> str:
    """判定一組分數序列的趨勢：'improving' / 'declining' / 'stable'。

    至少需要 2 筆分數，否則判定為 stable。
    用首筆與尾筆的差值，超過閾值才判定方向，避免雜訊造成誤判。
    """
    if len(scores_list) < 2:
        return "stable"
    delta = scores_list[-1] - scores_list[0]
    if delta >= _TREND_THRESHOLD:
        return "improving"
    if delta <= -_TREND_THRESHOLD:
        return "declining"
    return "stable"


def _extract_dim_scores(diagnoses: list[dict]) -> dict[str, list[float]]:
    """從 diagnoses 列表提取每個維度的分數序列（保留原始時序）。

    missing / 非數字的分數會被略過，不影響整體——確保單筆或空情境也安全。
    """
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
    """回傳每個維度的最新分數；若無任何紀錄，填入中性預設值 60。"""
    return {
        dim: (dim_scores[dim][-1] if dim_scores[dim] else 60.0)
        for dim in _DIM_KEYS
    }


def _weakest_dim(latest: dict[str, float]) -> str:
    """找出分數最低的維度，全部缺失預設 grammar。"""
    if not latest:
        return "grammar"
    return min(latest, key=lambda d: latest[d])


def _period_desc(diagnoses: list[dict]) -> str:
    """根據診斷筆數產生涵蓋期間描述。"""
    n = len(diagnoses)
    if n == 0:
        return "尚無練習紀錄"
    if n == 1:
        return "最近 1 次練習"
    # 嘗試用首尾日期描述期間
    dates = []
    for d in diagnoses:
        dt = (d or {}).get("date")
        if dt:
            dates.append(str(dt))
    if len(dates) >= 2:
        return f"最近 {n} 次練習（{dates[0]} 至 {dates[-1]}）"
    return f"最近 {n} 次練習"

# ---------------------------------------------------------------------------
# 規則式 fallback：核心敘事邏輯
# ---------------------------------------------------------------------------

# 趨勢 → summary 首句範本
# 每個趨勢有多個變體；依 weakest_dim 選用不同模板，確保不同輸入不同輸出。
_SUMMARY_TEMPLATES: dict[str, dict[str, str]] = {
    "improving": {
        "pronunciation": (
            "這段期間孩子的英語口說表現有明顯進步，發音清晰度持續提升。"
            "整體學習狀態積極，練習意願高，值得肯定。"
        ),
        "fluency": (
            "孩子的口說流暢度在這段期間持續進步，能夠更自然地開口說英文。"
            "這樣的成長幅度顯示孩子正建立起表達信心。"
        ),
        "vocabulary": (
            "孩子的詞彙量在這段練習期間穩定增加，能認識並運用更多單字。"
            "豐富的詞彙基礎是日後流暢表達的重要根基。"
        ),
        "grammar": (
            "孩子的文法掌握度在這段期間有明顯進步，句型結構更加完整。"
            "整體學習曲線向上，繼續維持目前的練習節奏即可。"
        ),
    },
    "declining": {
        "pronunciation": (
            "這段期間孩子的發音表現出現一些起伏，清晰度有所下降。"
            "建議多留意孩子的學習狀態，適時給予鼓勵，幫助他重新找回信心。"
        ),
        "fluency": (
            "孩子最近的口說流暢度有些退步，開口說英文的意願似乎也略有降低。"
            "建議先找出影響學習意願的原因，再調整練習方式。"
        ),
        "vocabulary": (
            "孩子的詞彙運用在這段期間有些退步，使用的英文詞彙範圍變窄。"
            "可以搭配生活情境補充詞彙，讓學習更有趣、更實用。"
        ),
        "grammar": (
            "孩子的文法表現這段期間有所下滑，句型錯誤的頻率略有上升。"
            "建議放慢速度，先鞏固基本句型，不急於擴充難度。"
        ),
    },
    "stable": {
        "pronunciation": (
            "孩子的發音表現這段期間維持穩定，沒有明顯起伏。"
            "目前的學習狀態屬於鞏固期，可考慮逐步增加練習難度。"
        ),
        "fluency": (
            "孩子的口說流暢度這段期間表現平穩，整體維持在相近水準。"
            "穩定的練習習慣已經建立，可以嘗試更多元的說話情境。"
        ),
        "vocabulary": (
            "孩子的詞彙量這段期間維持穩定，表現沒有大幅波動。"
            "持續的練習有助於鞏固已學詞彙，建議增加生活化詞彙的接觸。"
        ),
        "grammar": (
            "孩子的整體表現這段期間維持穩定，四個學習面向均無明顯起伏。"
            "維持規律練習是最重要的，不需要太多急於求成的壓力。"
        ),
    },
}


def _build_summary(overall_trend: str, weakest_dim: str, latest: dict[str, float]) -> str:
    """產生 summary：結合趨勢與最弱維度，加上最弱分數的意義說明。

    appendix 設計原則（R1 修正）：
    - 分數 < 65（仍需加強）：說「是本週最需要持續關注的面向」，語氣一致無矛盾。
    - 分數 65-79（達到基礎/表現良好）：說「建議持續留意」，中性表述。
    - 分數 ≥ 80（表現良好/優秀）：說「值得繼續保持這個水準」，正向收尾，不矛盾。
    """
    trend_map = _SUMMARY_TEMPLATES.get(overall_trend, _SUMMARY_TEMPLATES["stable"])
    base = trend_map.get(weakest_dim, trend_map.get("grammar", ""))
    # 附加最弱維度的分數意義說明（讓數字有脈絡，不只丟數字）
    weak_score = latest.get(weakest_dim, 60.0)
    weak_zh = _DIM_ZH.get(weakest_dim, weakest_dim)
    desc = _score_desc(weak_score)
    if weak_score >= 80:
        appendix = f"目前{weak_zh}為 {int(weak_score)} 分，{desc}，值得繼續保持這個水準。"
    elif weak_score >= 65:
        appendix = f"目前{weak_zh}為 {int(weak_score)} 分，{desc}，建議持續留意並穩定提升。"
    else:
        appendix = f"目前{weak_zh}為 {int(weak_score)} 分，{desc}，是本週最需要持續關注的面向。"
    return base + appendix


# highlights：進步面向的條目（不同趨勢選不同條目，確保輸出差異）
_HIGHLIGHTS_BY_TREND: dict[str, dict[str, list[str]]] = {
    "improving": {
        "pronunciation": [
            "發音清晰度持續進步，讓聽者更容易理解孩子說的英文",
            "願意主動開口說英文，學習積極性明顯提升",
        ],
        "fluency": [
            "口說流暢度提升，說話時停頓和猶豫的次數減少",
            "連續說完整句子的能力有所成長",
        ],
        "vocabulary": [
            "詞彙量穩定增加，能運用更多單字來表達想法",
            "認識並使用多個新詞彙，詞彙廣度有明顯擴充",
        ],
        "grammar": [
            "句型結構更加完整，文法正確率有明顯提升",
            "冠詞 a/an 的使用準確度增加，基礎文法逐漸鞏固",
        ],
    },
    "declining": {
        "pronunciation": [
            "練習過程中仍願意開口嘗試，沒有完全放棄",
            "對學過的單字發音仍保有基礎記憶",
        ],
        "fluency": [
            "對熟悉的話題仍能開口回應",
            "短句表達能力維持穩定",
        ],
        "vocabulary": [
            "常用詞彙的記憶仍穩固",
            "在有引導的情況下能說出較多詞彙",
        ],
        "grammar": [
            "對話時有嘗試說完整句子的意願",
            "在老師引導下能修正句型錯誤",
        ],
    },
    "stable": {
        "pronunciation": [
            "發音表現維持穩定，基礎發音習慣已建立",
            "能持續維持練習節奏，值得肯定",
        ],
        "fluency": [
            "口說流暢度維持穩定，表現一致",
            "練習習慣規律，有助於鞏固現有能力",
        ],
        "vocabulary": [
            "詞彙記憶穩固，已學詞彙的正確率維持良好",
            "能穩定運用熟悉的詞彙範圍",
        ],
        "grammar": [
            "整體表現維持穩定，基本句型有一定掌握度",
            "學習態度持續穩定，練習意願不減",
        ],
    },
}

# concerns：需要留意的面向（依最弱維度客製化）
_CONCERNS_TEMPLATES: dict[str, list[str]] = {
    "pronunciation": [
        "發音是目前最需要加強的面向，部分字音的清晰度仍有進步空間",
        "建議特別注意母音（如 a/e/i/o/u）和常見子音的正確發音方式",
    ],
    "fluency": [
        "口說流暢度仍有進步空間，說話時較常出現停頓或換回中文的情況",
        "練習時建議鼓勵孩子用英文完整說完，不急著糾正，先建立開口習慣",
    ],
    "vocabulary": [
        "詞彙量仍有擴充空間，表達時常因為不知道怎麼說而停頓",
        "建議從生活中常見事物開始，每天多認識 1-2 個新單字",
    ],
    "grammar": [
        "文法是目前最需要加強的面向，冠詞（a/an）和句型結構仍需持續練習",
        "建議以正確示範取代直接糾錯，讓孩子在自然情境中修正句型",
    ],
}

# declining 趨勢下額外加入的 concern
_DECLINING_EXTRA_CONCERN: dict[str, str] = {
    "pronunciation": "這段期間發音表現有些退步，需要特別留意是否有疲勞或情緒影響",
    "fluency": "口說流暢度近期有下滑趨勢，建議觀察孩子的學習狀態與意願",
    "vocabulary": "詞彙運用近期有退步跡象，可能需要重新複習之前學過的單字",
    "grammar": "文法表現近期有所下滑，建議回到基礎句型加以鞏固",
}

# suggestions：家長在家可以怎麼陪（依最弱維度 × 趨勢客製化）
# improving：強化現有動力、讓進步感持續
# declining：找回學習動機、降低挫折感
# stable：突破舒適圈、增加刺激多樣性
_SUGGESTIONS_BY_DIM_TREND: dict[str, dict[str, list[str]]] = {
    "pronunciation": {
        "improving": [
            "趁現在進步的好勢頭，睡前陪孩子挑一首英文歌，跟著唱出每個字的發音",
            "用手機錄下孩子說英文的聲音，讓他自己聽聽看，鼓勵他分享覺得進步的地方",
            "遇到新單字，可以一起查字典的發音示範，培養自主查詢的好習慣",
        ],
        "declining": [
            "先暫停糾正發音，多讓孩子跟著英文卡通模仿角色說話，把樂趣找回來",
            "選一首孩子喜歡的英文歌，每天只練一句，讓他有「我做到了」的成就感",
            "遇到孩子不確定的發音，示範一次就好，不重複糾正，保護開口的意願",
        ],
        "stable": [
            "試試看讓孩子用英文描述一張圖片或一段影片，挑戰新的說話情境",
            "和孩子一起玩「說英文繞口令」小遊戲，在笑聲中練習清晰發音",
            "鼓勵孩子對家人「教」一個英文單字怎麼念，用教學來深化發音記憶",
        ],
    },
    "fluency": {
        "improving": [
            "每天安排 5 分鐘的「英文說話時間」，讓孩子用英文說說今天最有趣的一件事",
            "陪孩子玩英文問答，問他喜歡的食物或動物，鼓勵用完整句子回答",
            "看英文卡通時暫停，請孩子描述畫面發生了什麼，趁進步期鞏固流暢度",
        ],
        "declining": [
            "讓孩子選一個他最喜歡的話題，只用中英夾雜也沒關係，先讓他開口說話",
            "安排輕鬆的英文繪本共讀時間，孩子朗讀一頁就好，不強求完整表達",
            "遇到孩子想換回中文說時，不打斷，等他說完再溫和示範英文說法一次",
        ],
        "stable": [
            "給孩子一個新挑戰：用英文說一個完整的小故事（三句話就好）",
            "試試「1 分鐘英文挑戰」，限時說說某個主題，不在意文法，只求說完",
            "讓孩子用英文介紹家裡的東西給玩具熊「聽」，降低心理壓力，多說多練",
        ],
    },
    "vocabulary": {
        "improving": [
            "在家常用物品上貼英文標籤，每天念出來，讓詞彙量持續自然累積",
            "和孩子一起逛超市時，練習說看到的食物或物品的英文，把進步連結到生活",
            "睡前聊聊今天學到的英文單字，聊聊在哪裡可以用到，加深記憶連結",
        ],
        "declining": [
            "回頭複習之前孩子說「我會了」的詞彙，從已知的成功經驗重建信心",
            "改用圖卡或繪本方式接觸詞彙，降低背單字的壓力，讓詞彙有圖像支撐",
            "每天只認識一個新單字，配合生活場景說給孩子聽，不強求記住",
        ],
        "stable": [
            "嘗試主題式詞彙挑戰，例如「廚房裡的英文」或「天氣的英文」，開拓新領域",
            "陪孩子玩英文分類遊戲，把動物、食物、顏色等詞彙分組說出來",
            "讓孩子試著用英文詞彙自己造句，哪怕只是短句，也能突破詞彙停滯期",
        ],
    },
    "grammar": {
        "improving": [
            "陪孩子說英文時，用正確句型重複他說的話，讓他自然聽到完整版本",
            "趁文法進步的機會，一起讀簡單的英文繪本，感受完整句型的節奏",
            "可以和孩子一起玩句子接龍，每人一句，練習說完整的英文句子",
        ],
        "declining": [
            "回到最基本的問答句型（如 What is this? It is a…），讓孩子找回句型的感覺",
            "暫時不強調文法正確，以完整開口說完一句為優先目標",
            "遇到孩子說漏冠詞（a/an）時，溫和示範一次正確說法，不反覆糾正",
        ],
        "stable": [
            "嘗試請孩子用英文描述一件事的「前後過程」，練習連接詞與時態的運用",
            "可以和孩子玩問答遊戲，要求對方用完整句子回答，不接受只說一個字",
            "選一個孩子熟悉的句型，鼓勵他加上更多細節，拉長句子複雜度",
        ],
    },
}


def _build_highlights(dim_trends: dict[str, str], weakest: str, latest: dict[str, float]) -> list[str]:
    """產生 1-3 條 highlights（依整體趨勢與最強維度選取）。

    策略：先找分數最高的維度作為「亮點維度」，依其趨勢取對應 highlights；
    若整體趨勢為 improving，補上一條通用進步說明（與 concerns 不重疊）。
    """
    # 找最強維度（分數最高）
    strongest = max(latest, key=lambda d: latest[d]) if latest else "vocabulary"
    overall = dim_trends.get("_overall", "stable")

    pool = _HIGHLIGHTS_BY_TREND.get(overall, _HIGHLIGHTS_BY_TREND["stable"])
    items = pool.get(weakest, pool.get("grammar", []))

    # 確保最多 3 條，且不重複
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
        if len(result) >= 3:
            break

    # 若 improving 且最強維度與 weakest 不同，補一條最強維度的亮點
    if overall == "improving" and strongest != weakest and len(result) < 3:
        strong_score = latest.get(strongest, 60.0)
        strong_zh = _DIM_ZH.get(strongest, strongest)
        extra = f"{strong_zh}在這段期間達到 {int(strong_score)} 分，{_score_desc(strong_score)}，是本週最突出的亮點"
        if extra not in seen:
            result.append(extra)

    return result[:3] if result else ["孩子維持規律練習，態度積極值得肯定"]


def _build_concerns(dim_trends: dict[str, str], weakest: str, overall_trend: str) -> list[str]:
    """產生 1-3 條 concerns（依最弱維度與趨勢差異化）。

    improving：只取 1 條基礎 concern，加一條「保持節奏」鼓勵（與 stable/declining 區分）。
    stable：取 2 條基礎 concern。
    declining：取 2 條基礎 concern，再加 1 條退步專屬說明。
    去重確保不重複。
    """
    base_pool = _CONCERNS_TEMPLATES.get(weakest, _CONCERNS_TEMPLATES["grammar"])
    seen: set[str] = set()
    result: list[str] = []

    if overall_trend == "improving":
        # 進步中：只取一條基礎提醒，再加一條正向激勵性的提醒，與 stable 明顯區分
        if base_pool:
            result.append(base_pool[0])
            seen.add(base_pool[0])
        dim_zh = _DIM_ZH.get(weakest, weakest)
        extra_improve = f"目前進步動能良好，建議持續鞏固{dim_zh}，不讓這股成長勢頭中斷"
        if extra_improve not in seen:
            result.append(extra_improve)
    else:
        # stable / declining：取前 2 條基礎 concern
        for item in base_pool:
            if item not in seen and len(result) < 2:
                seen.add(item)
                result.append(item)

        # 退步趨勢加入額外說明
        if overall_trend == "declining":
            extra = _DECLINING_EXTRA_CONCERN.get(weakest, "")
            if extra and extra not in seen:
                result.append(extra)

    return result[:3] if result else [f"{_DIM_ZH.get(weakest, weakest)}仍有進步空間，需要持續練習"]


def _build_suggestions(weakest: str, overall_trend: str) -> list[str]:
    """產生 1-3 條 suggestions（依最弱維度 × 趨勢選取，確保三種趨勢輸出不同）。"""
    dim_pool = _SUGGESTIONS_BY_DIM_TREND.get(weakest, _SUGGESTIONS_BY_DIM_TREND["grammar"])
    pool = dim_pool.get(overall_trend, dim_pool.get("stable", []))
    # 取前 3 條，去重（理論上 pool 內已不重複，但防禦性去重）
    seen: set[str] = set()
    result: list[str] = []
    for item in pool:
        if item not in seen:
            seen.add(item)
            result.append(item)
        if len(result) >= 3:
            break
    return result if result else ["每天撥出 5 分鐘，陪孩子說說英文，讓練習成為日常習慣"]


def _dedupe_across(*fields: list[str]) -> tuple[list[str], ...]:
    """跨欄位去重：先出現的欄位保留，後面的欄位剔除重複句。

    只在真的重複時才會動到內容；欄位可能因此變空，那是正確的——
    同一句話在兩個欄位裡出現，本來就只該算一次。
    """
    seen: set[str] = set()
    out: list[list[str]] = []
    for field in fields:
        kept: list[str] = []
        for item in field:
            key = str(item).strip()
            if key and key not in seen:
                seen.add(key)
                kept.append(item)
        out.append(kept)
    return tuple(out)


def _minimal_report() -> dict:
    """最小合法週報：所有其他路徑都失敗時的保底，永遠符合公開 schema。"""
    return {
        "period": "本次練習",
        "summary": "系統暫時無法整理完整的學習週報，以下僅供參考；"
                   "詳細的學習狀況請以教師端的診斷紀錄為準。",
        "highlights": [],
        "concerns": [],
        "suggestions": ["每天撥出 5 分鐘，陪孩子說說英文，讓練習成為日常習慣"],
        "source": "rule",
    }


def _rule_based_report(profile: dict, diagnoses: list[dict]) -> dict:
    """規則式週報（完全離線，零外部依賴）。

    流程：
    1. diagnoses 為空 → 誠實回報「尚無資料」，highlights/concerns 空 list，
       suggestions 給一般性鼓勵，不虛構任何孩子表現（R4 修正）。
    2. diagnoses 只有一筆 → 描述當次表現，不宣稱任何趨勢（R4 修正）。
    3. 兩筆以上 → 計算趨勢，用趨勢 + 最弱維度組裝完整週報。
    4. 所有條目去重後取前 N 條（N ∈ [1, 3]），確保週報不含重複句子。
    """
    diagnoses = diagnoses or []

    # ── 空資料：誠實說明，不捏造 ─────────────────────────────────────────
    if len(diagnoses) == 0:
        return {
            "period": _period_desc(diagnoses),
            "summary": (
                "目前尚無任何練習紀錄，無法評估孩子的學習狀況。"
                "建議先開始幾次練習，系統就能提供有根據的學習分析。"
            ),
            "highlights": [],
            "concerns": [],
            "suggestions": [
                "每天撥出 5 分鐘，陪孩子開口說幾句英文，讓練習成為日常習慣",
                "從孩子感興趣的話題入手，降低開口的心理門檻，先求敢說再求說對",
                "定期讓孩子使用說說學伴練習，累積幾次紀錄後就能看到具體的成長軌跡",
            ],
            "source": "rule",
        }

    # ── 單筆資料：只描述當次，不宣稱趨勢 ───────────────────────────────
    if len(diagnoses) == 1:
        dim_scores = _extract_dim_scores(diagnoses)
        latest = _latest_scores(dim_scores)
        weakest = _weakest_dim(latest)
        weak_score = latest.get(weakest, 60.0)
        weak_zh = _DIM_ZH.get(weakest, weakest)
        desc = _score_desc(weak_score)
        # 單筆 summary：只說「這次」，不說趨勢
        summary = (
            f"這是孩子的第一次練習紀錄。"
            f"本次{weak_zh}為 {int(weak_score)} 分，{desc}。"
            f"需要累積更多練習次數，才能進行學習分析。"
        )
        return {
            "period": _period_desc(diagnoses),
            "summary": summary,
            "highlights": _build_highlights({"_overall": "stable"}, weakest, latest),
            "concerns": _build_concerns({}, weakest, "stable"),
            "suggestions": _build_suggestions(weakest, "stable"),
            "source": "rule",
        }

    # ── 兩筆以上：計算趨勢，產出完整分析 ───────────────────────────────
    dim_scores = _extract_dim_scores(diagnoses)
    latest = _latest_scores(dim_scores)
    weakest = _weakest_dim(latest)

    # 每個維度的趨勢
    dim_trends: dict[str, str] = {}
    for dim in _DIM_KEYS:
        dim_trends[dim] = _trend(dim_scores[dim])

    # 整體趨勢 = 多數決（improving > stable > declining 同票時偏保守）
    trend_counts: dict[str, int] = {"improving": 0, "stable": 0, "declining": 0}
    for t in dim_trends.values():
        trend_counts[t] = trend_counts.get(t, 0) + 1
    if trend_counts["improving"] >= trend_counts["declining"] and trend_counts["improving"] > 0:
        overall_trend = "improving"
    elif trend_counts["declining"] > trend_counts["improving"]:
        overall_trend = "declining"
    else:
        overall_trend = "stable"
    dim_trends["_overall"] = overall_trend

    period = _period_desc(diagnoses)
    summary = _build_summary(overall_trend, weakest, latest)
    highlights = _build_highlights(dim_trends, weakest, latest)
    concerns = _build_concerns(dim_trends, weakest, overall_trend)
    suggestions = _build_suggestions(weakest, overall_trend)

    # 跨欄位去重：各欄位內部去重已在各 _build_* 完成，這裡確保同一句話不會
    # 同時出現在 highlights 與 concerns。原本這裡只算了一個沒人用的集合，
    # 註解卻宣稱有防禦——那比沒有防禦更糟，因為讀的人以為有。
    highlights, concerns, suggestions = _dedupe_across(highlights, concerns, suggestions)

    return {
        "period": period,
        "summary": summary,
        "highlights": highlights,
        "concerns": concerns,
        "suggestions": suggestions,
        "source": "rule",
    }

# ---------------------------------------------------------------------------
# 雲端 prompt 組裝
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "你是台灣國小英語學習週報撰寫專家，讀者是家長與老師（不是小孩）。"
    "根據學生的多筆診斷資料，撰寫一份客觀、有意義的學習週報。"
    "用成人看的完整敘述，不要用對小孩說話的語氣，不要堆砌英文教學術語。"
    "提到分數時要說明分數代表什麼意義，不能只丟數字。"
    "只輸出一個 JSON 物件，不得有 markdown 圍欄或額外文字。"
    "所有文字使用繁體中文（台灣用語）。"
)


def _build_user_prompt(profile_safe: dict, diags_safe: list[dict], period: str) -> str:
    """組裝送給雲端的 user prompt（已去識別化）。"""
    # 只傳最近 5 筆診斷（避免 prompt 過大）
    recent_diags = diags_safe[-5:]
    schema_example = {
        "period": "最近 5 次練習",
        "summary": "孩子這段期間在口說流暢度方面有明顯進步，能更自然地開口表達。",
        "highlights": ["發音清晰度持續提升", "詞彙量增加，能運用更多單字"],
        "concerns": ["文法仍需加強，冠詞偶有遺漏（如 a/an）"],
        "suggestions": ["睡前陪孩子說說今天發生的事，用英文說說看"],
        "source": "cloud",
    }
    return (
        f"學習期間摘要：{period}\n"
        f"學生 profile（已去識別化）：{json.dumps(profile_safe, ensure_ascii=False)[:300]}\n"
        f"最近診斷資料（時序，最舊在前）：\n"
        f"{json.dumps(recent_diags, ensure_ascii=False)[:1500]}\n\n"
        "請根據上述資料，產生一份讓家長看得懂的中文學習週報。"
        "提到分數時一定要說明分數代表什麼。"
        "僅輸出符合以下 schema 的 JSON 物件（source 固定為 \"cloud\"）：\n"
        + json.dumps(schema_example, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# 雲端回應解析與驗證
# ---------------------------------------------------------------------------

def _validate_cloud_response(data: dict) -> bool:
    """驗證雲端回傳的 dict 符合週報 schema；不符回 False。"""
    if not isinstance(data, dict):
        return False
    if not (isinstance(data.get("period"), str) and data["period"].strip()):
        return False
    if not (isinstance(data.get("summary"), str) and data["summary"].strip()):
        return False
    for key in ("highlights", "concerns", "suggestions"):
        val = data.get(key)
        if not isinstance(val, list):
            return False
        if not (1 <= len(val) <= 3):
            return False
        for item in val:
            if not (isinstance(item, str) and str(item).strip()):
                return False
    return True


def _parse_cloud_response(text: str) -> dict | None:
    """解析雲端回傳的 JSON 字串並驗證 schema；任何問題回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    # 容忍 ```json 圍欄（與 homework 同樣的寬容策略）
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
# 去識別化輔助（對應 homework 的 _deidentify_profile / _deidentify_diagnosis）
# ---------------------------------------------------------------------------

_MAX_TEXT_LEN = 200


def _deidentify_profile(profile: dict) -> dict:
    """profile 上雲前投影（白名單，見 agents/privacy.py）。"""
    return privacy.safe_profile(profile)


def _deidentify_diag(diag: dict) -> dict:
    """單筆 diagnosis 上雲前投影（白名單，見 agents/privacy.py）。"""
    return privacy.safe_diagnosis(diag)


def _deidentify_diagnoses(diagnoses: list[dict]) -> list[dict]:
    """批次投影 diagnoses 列表，呼叫端再拼入 prompt。"""
    return privacy.safe_diagnoses(diagnoses)


# ---------------------------------------------------------------------------
# 公開契約入口
# ---------------------------------------------------------------------------

def generate_report(
    profile: dict,
    diagnoses: list[dict],
    *,
    allow_cloud: bool = True,
) -> dict:
    """週報 agent 主入口。

    流程：
    1. allow_cloud=False 或未取得家長同意 → 直接走規則式，不碰任何雲端呼叫。
    2. allow_cloud=True → 嘗試 Bedrock Converse：
       a. 去識別化 profile / diagnoses 自由文字欄位。
       b. 呼叫 bedrock_converse.converse_text（cfg=resolve_config(role="diag")）。
       c. 對整體回傳字串做 passes_guardrail；不通過 → 降級。
       d. 解析並驗證回傳 JSON schema；不合法 → 降級。
       e. 任何例外 → 靜默 log 後降級。
    3. 規則式（離線 fallback）永遠保底，一定能產出合法結果。

    任何情況都不往外拋例外——**包含規則式路徑自己爆掉**。
    """
    try:
        return _generate_report(profile, diagnoses, allow_cloud=allow_cloud)
    except Exception:
        _log.exception("generate_report 全數路徑失敗，回傳最小合法週報")
        return _minimal_report()


def _generate_report(profile, diagnoses, *, allow_cloud: bool) -> dict:
    # 防禦性正規化
    profile = profile if isinstance(profile, dict) else {}
    diagnoses = list(diagnoses) if isinstance(diagnoses, (list, tuple)) else []

    # allow_cloud=False：最高優先閘門，連 resolve_config 都不呼叫。
    # consent 同級：家長同意是資料出境的 chokepoint（見 diagnose.py）。
    if not allow_cloud or not guardrails.consent_granted():
        return _rule_based_report(profile, diagnoses)

    # 雲端路徑。後端優先序：AgentCore Harness → Bedrock Converse → 規則式。
    try:
        ac_cfg = agentcore.resolve_config("report")
        cfg = None if ac_cfg else bedrock_converse.resolve_config(role="diag")
        if ac_cfg is None and cfg is None:
            # 兩個雲端後端都沒設定 → 直接走規則式
            return _rule_based_report(profile, diagnoses)

        # 去識別化（上雲前對自由文字遮罩個資）
        profile_safe = _deidentify_profile(profile)
        diags_safe = _deidentify_diagnoses(diagnoses)

        # 組裝 prompt
        period = _period_desc(diagnoses)
        user_prompt = _build_user_prompt(profile_safe, diags_safe, period)

        if ac_cfg is not None:
            # AgentCore：system prompt 在 Harness 建立時已宣告，這裡只送訊息。
            raw_text = agentcore.invoke(
                ac_cfg, user_prompt,
                actor_id=(profile or {}).get("student_id"),
                session_id=f"rpt-{(diagnoses or [{}])[-1].get('date') or 'na'}",
            )
        else:
            raw_text = bedrock_converse.converse_text(
                _SYSTEM_PROMPT,
                user_prompt,
                cfg=cfg,
                max_tokens=1024,
                timeout_s=_TIMEOUT_S,
            )

        # 護欄：整體回傳字串過安全過濾
        if not guardrails.passes_guardrail(raw_text):
            _log.warning("generate_report 雲端回覆未通過護欄，降級回規則式")
            return _rule_based_report(profile, diagnoses)

        # 解析與 schema 驗證
        parsed = _parse_cloud_response(raw_text)
        if parsed is None:
            _log.warning("generate_report 雲端回覆 schema 不合法，降級回規則式")
            return _rule_based_report(profile, diagnoses)

        # 強制標記 source 為 cloud
        parsed["source"] = "cloud"
        return parsed

    except Exception:
        # 任何例外（網路、逾時、boto3 未安裝…）都不往外拋
        _log.exception("generate_report 雲端路徑失敗，降級回規則式")
        return _rule_based_report(profile, diagnoses)
