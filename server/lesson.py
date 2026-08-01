# -*- coding: utf-8 -*-
"""lesson.py — 每場 live 對話開始時「選教材」：由最新診斷 + profile
決定今日主題、目標句與 B1 策略字串。與 WS/Nova 無耦合，離線可測。
"""

from __future__ import annotations

from dataclasses import dataclass

_DEFAULT_SENTENCE = "How are you today?"


@dataclass
class Lesson:
    topic: str
    target_sentence: str
    target_form: str | None
    directive: str | None
    # 目標句實際所屬的分類。與 topic 是兩回事，刻意分開兩個欄位：
    # topic 由診斷決定（延伸問句、遊戲出題靠它，見 test_unit_alignment），
    # 而帶讀句優先取老師指定的本週單元，兩者本來就可能不同類。
    sentence_topic: str | None = None
    # 帶讀句來自老師指定的本週單元時，這裡帶單元資訊給畫面當標籤。
    #
    # 為什麼不能只靠 sentence_topic：Unit 6「What Are You Doing?」的
    # "He is eating an apple." 在 VOCAB 裡歸類是 action（因為關鍵字是
    # eating），畫面就顯示「今天主題：動作」——可是使用者看到的是 apple，
    # 讀起來還是對不上。詞彙分類本來就不是給孩子看的標籤，單元名稱才是。
    unit_no: int | None = None
    unit_title: str | None = None
    unit_zh: str | None = None


def _unit_entries(unit_no=None) -> list[dict]:
    """本週單元的教材詞條：拿單元字表去 scaffold.VOCAB 反查，保持字表原順序。

    不直接讀 seed_units.UNIT_MATERIALS，而是查 VOCAB——VOCAB 才是執行期的真相：
    老師現場用 /api/material 再上傳一份教材，新詞會即時進 VOCAB，這裡就跟得上；
    而課綱本來就有的字（breakfast/lunch/dinner 在 Unit 5 字表裡，但它們早在
    scaffold.VOCAB 的 food 類）也一併撈得到，不會因為「教材 agent 沒新增它」
    就在本週單元裡消失。

    單元字表裡沒被任何詞條對上的字（例如 material agent 這次沒提煉到的）就跳過，
    回空 list 是完全合法的狀態——呼叫端一律有退路。
    """
    from server import scaffold, seed_units
    u = seed_units.unit(unit_no) if unit_no is not None else seed_units.current_unit()
    by_en: dict[str, dict] = {}
    for zh, info in scaffold.VOCAB.items():
        en = str(info.get("en") or "").lower()
        if en and info.get("sent") and en not in by_en:
            by_en[en] = {"zh": zh, **info}
    out = []
    for w in u.get("words") or []:
        hit = by_en.get(str(w).lower())
        if hit is not None:
            out.append(hit)
    return out


def unit_sentences(profile=None, limit: int = 5, unit_no=None) -> list[str]:
    """本週單元可以拿來練的句子，孩子正在學的詞排前面。

    這是「同一份教材，每個孩子走不同的路」在帶讀句上的落點：**句子的來源
    對全班是同一份（老師指定的本週單元）**，排序才依各自的 profile 分歧。

    任何例外都回空 list，讓呼叫端退回原本依 cat 選句的行為，不擋 live。
    """
    try:
        entries = _unit_entries(unit_no)
        learning = set()
        for v in (profile or {}).get("learning_vocab", []) or []:
            en = v.get("en") if isinstance(v, dict) else v
            if en:
                learning.add(str(en).lower())
        ranked = ([e for e in entries if str(e["en"]).lower() in learning]
                  + [e for e in entries if str(e["en"]).lower() not in learning])
        out: list[str] = []
        for e in ranked:
            if e["sent"] not in out:
                out.append(e["sent"])
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def pick_target_sentence(topic, profile=None, unit_no=None) -> str:
    """挑本場跟讀的英文目標句。

    **優先給本週單元的句子**（``unit_no=None`` 代表 seed_units.CURRENT_UNIT_NO）。
    2026-08-01 線上實測：孩子講「今天天氣 sunny」，系統認得 sunny（Unit 3 教材
    已進 VOCAB），帶讀句卻是 ``I see a dog.``——因為目標句一直是依診斷輪替出來的
    ``cat``（curriculum.TOPIC_ORDER 那 6 類）挑的，與老師「本週上到哪一課」是兩套
    從未對齊的資料。老師指定了教材，玩偶卻在教別的東西，這是評審當場看得見的破綻。

    本週單元撈不到任何句子時（教材還沒載入、或字表與 VOCAB 完全對不上），
    退回原本行為：從 scaffold.VOCAB 篩 cat==topic 且有 sent 的詞；優先挑 profile
    正在學（learning_vocab）對應的詞的例句；缺就取該類第一句；完全無 → 通用預設。
    任何例外都退化為預設，不炸。
    """
    from server import scaffold
    picked = unit_sentences(profile, limit=1, unit_no=unit_no)
    if picked:
        return picked[0]
    try:
        cands = [info for info in scaffold.VOCAB.values()
                 if info.get("cat") == topic and info.get("sent")]
        if not cands:
            return _DEFAULT_SENTENCE
        learning = set()
        for v in (profile or {}).get("learning_vocab", []) or []:
            en = v.get("en") if isinstance(v, dict) else v
            if en:
                learning.add(str(en))
        for info in cands:
            if info.get("en") in learning:
                return info["sent"]
        return cands[0]["sent"]
    except Exception:
        return _DEFAULT_SENTENCE


def topic_sentences(topic, profile=None, limit: int = 5, unit_no=None) -> list[str]:
    """取本場可練的多個例句，今日目標句排第一。

    與 ``pick_target_sentence`` 同一套優先序：**先給本週單元的句子**，不足才用
    同 cat 的例句補到 limit。兩者共用 ``unit_sentences`` 的排序，所以
    ``out[0] == pick_target_sentence(...)`` 這個契約在有沒有單元教材時都成立。

    `pick_target_sentence` 一次只給一句，於是即時陪聊的教練 prompt 只知道
    一句可以練。2026-07-31 十輪模擬對話的結果是：孩子第 1 輪就唸對了，玩偶
    仍然十輪都教同一句，孩子抗議「你怎麼一直叫我唸一樣的啦」。

    教練 prompt 本來就寫著「孩子跟上就換下一句或延伸一點」——缺的不是指令，
    是**材料**。`scaffold.VOCAB` 的 animal 類其實有 29 句。

    任何例外都回空 list，不炸（與本模組其他函式一致）。
    """
    from server import scaffold

    try:
        out = unit_sentences(profile, limit=limit, unit_no=unit_no)
        if not out:
            # 沒有單元教材可用：維持原行為。主題不存在時 pick_target_sentence 會
            # 回通用預設，那不算這個主題的句子，整批視為空。
            if not any(i.get("cat") == topic and i.get("sent")
                       for i in scaffold.VOCAB.values()):
                return []
            out = [pick_target_sentence(topic, profile, unit_no)]
        for info in scaffold.VOCAB.values():
            if len(out) >= limit:
                break
            if info.get("cat") != topic or not info.get("sent"):
                continue
            sent = info["sent"]
            if sent not in out:
                out.append(sent)
        return out[:limit]
    except Exception:
        return []


def topic_of_sentence(sent) -> str | None:
    """這句目標句實際屬於哪一類（scaffold.VOCAB 的 cat）；查不到回 None。"""
    from server import scaffold
    try:
        for info in scaffold.VOCAB.values():
            if info.get("sent") == sent:
                return info.get("cat") or None
    except Exception:
        pass
    return None


def build_lesson(diagnoses, profile=None) -> Lesson:
    """由最新診斷 + profile 組本場教材。全程安全退化，永不擋 live。

    ⚠️ **topic 與 sentence_topic 是兩個欄位，不要合併。**
    - ``topic``：由診斷決定，延伸問句與遊戲出題靠它（見
      ``tests/test_unit_alignment.py``，那是刻意的契約）。
    - ``sentence_topic``：目標句實際所屬的分類。帶讀句優先取老師指定的本週
      單元，所以它跟 ``topic`` 本來就可能不同類。

    2026-08-01 線上 Unit 6（What Are You Doing?）實測：學生端同時顯示
    「今天主題：動物」與「He is eating an apple.」。那不是資料錯，是畫面拿
    錯欄位——標籤該講的是「這句話在教什麼」，而不是編排器內部輪到哪一類。
    """
    from server import curriculum, diagnose
    default_topic = curriculum.TOPIC_ORDER[0]
    default_form = curriculum._TARGET_FORM[1]
    try:
        latest = (diagnoses or [])[-1] if (diagnoses or []) else None
        directive = None
        topic = default_topic
        target_form = default_form
        if latest:
            ls = latest.get("level_state") or {}
            cd = latest.get("companion_directive")
            if cd:
                directive = diagnose.format_directive_for_prompt(cd, ls) or None
            topic = ls.get("topic") or default_topic
            target_form = ls.get("target_form") or default_form
        sent = pick_target_sentence(topic, profile)
        return Lesson(topic, sent, target_form, directive,
                      sentence_topic=topic_of_sentence(sent) or topic,
                      **_unit_fields(sent, profile))
    except Exception:
        sent = pick_target_sentence(default_topic, profile)
        return Lesson(default_topic, sent, default_form, None,
                      sentence_topic=topic_of_sentence(sent) or default_topic,
                      **_unit_fields(sent, profile))


def _unit_fields(sent, profile) -> dict:
    """這句是不是本週單元的句子；是的話回單元編號與名稱，不是就回空欄位。

    畫面拿它當「今天在練什麼」的標籤——單元名稱是老師和孩子都認得的說法，
    而 VOCAB 的 cat（food/action/…）只是內部分類，直接顯示會出現
    「今天主題：動作」配「He is eating an apple.」這種讀起來對不上的組合。
    任何例外都回空欄位，讓畫面退回原本的主題標籤，不擋 live。
    """
    try:
        from server import seed_units
        if sent and sent in set(unit_sentences(profile, limit=50)):
            u = seed_units.current_unit() or {}
            return {"unit_no": u.get("no"), "unit_title": u.get("title"),
                    "unit_zh": u.get("zh")}
    except Exception:
        pass
    return {}
