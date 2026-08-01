# -*- coding: utf-8 -*-
"""test_unit_alignment.py — 帶讀句要跟老師「本週上到哪一課」對齊。

2026-08-01 線上實測的逐字稿：

    孩子：今天天氣 sunny
    玩偶：哇，今天天氣真的很好呢！…一起來練習：I see a dog.

系統認得 sunny（Unit 3 教材已進 VOCAB），帶讀句卻與天氣毫無關係。三個獨立
缺陷疊在一起造成它，這個檔把三個都釘住：

1. 目標句依診斷輪替出來的 ``cat`` 選，與 ``seed_units.UNITS`` 是兩套從未對齊
   的資料 → ``pick_target_sentence`` 現在優先給本週單元的句子。
2. 詞庫查詢只有中文方向，孩子講出教材裡的英文字時反而比對不到 →
   ``scaffold._find_en_vocab``。
3. ``_article_is_consistent`` 對單字 np 一律回 False，把形容詞/數詞/動名詞
   全部誤殺（Unit 5 的 late、thirty 就是這樣消失的）。
"""

from __future__ import annotations

import pytest

from server import lesson, scaffold, seed_units
from server.agents import material


@pytest.fixture(autouse=True)
def _restore_vocab():
    """register_material_vocab 會原地 mutate 全域 VOCAB，測試後還原快照。"""
    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    yield
    scaffold.VOCAB.clear()
    scaffold.VOCAB.update(snapshot)


def _register(unit_no: int) -> None:
    scaffold.register_material_vocab(seed_units.UNIT_MATERIALS[unit_no]["entries"])


# ---------------------------------------------------------------------------
# 缺陷 3：單字 np 被驗證關卡誤殺
# ---------------------------------------------------------------------------

def _ctx():
    return (
        {v["en"].lower() for v in scaffold.VOCAB.values()},
        {v["sent"] for v in scaffold.VOCAB.values()},
        set(scaffold.VOCAB.keys()),
    )


@pytest.mark.parametrize("entry", [
    {"zh": "遲到的", "en": "late", "cat": "time", "np": "late",
     "sent": "You are late."},
    {"zh": "三十", "en": "thirty", "cat": "time", "np": "thirty",
     "sent": "It is seven thirty."},
    {"zh": "正在跑", "en": "running", "cat": "action", "np": "running",
     "sent": "The boy is running fast."},
])
def test_single_word_np_is_accepted(entry):
    """形容詞、數詞、動名詞寫不出名詞片語，沒有冠詞可驗就該放行。"""
    assert scaffold._is_valid_material_entry(entry, *_ctx()) is True


def test_wrong_article_is_still_rejected():
    """放行單字 np 不等於放棄冠詞檢查——真的寫錯冠詞仍要擋。"""
    entry = {"zh": "無尾熊", "en": "koala", "cat": "animal", "np": "an koala",
             "sent": "I see a koala."}
    assert scaffold._is_valid_material_entry(entry, *_ctx()) is False


def test_action_entry_no_longer_bypasses_duplicate_guard():
    """撤掉 action 的整條早退後，它同樣受「zh 已存在一律拒絕」保護。

    早退版本會讓一條 cat=action 的詞條無聲覆蓋既有課綱詞條（本例是把「蘋果」
    從 apple 改寫成 running），而那個損失會持久化到 materials 表、每次重啟重放。
    """
    entry = {"zh": "蘋果", "en": "running", "cat": "action", "np": "running",
             "sent": "The boy is running fast."}
    assert scaffold._is_valid_material_entry(entry, *_ctx()) is False

    accepted, rejected = scaffold.register_material_vocab([entry])
    assert (accepted, rejected) == ([], 1)
    assert scaffold.VOCAB["蘋果"]["en"] == "apple", "課綱詞條不得被教材覆蓋"


def test_unit5_vocabulary_survives_validation():
    """課本 Unit 5 的八個字，只要 agent 標得出分類就都該進得去。"""
    entries = [
        {"zh": "時間", "en": "time", "cat": "time", "np": "the time",
         "sent": "What time is it?"},
        {"zh": "……點鐘", "en": "o'clock", "cat": "time", "np": "o'clock",
         "sent": "It is seven o'clock."},
        {"zh": "三十", "en": "thirty", "cat": "time", "np": "thirty",
         "sent": "It is seven thirty."},
        {"zh": "起床", "en": "get up", "cat": "action", "np": "get up",
         "sent": "I get up at six o'clock."},
        {"zh": "遲到的", "en": "late", "cat": "time", "np": "late",
         "sent": "Hurry up! You are late."},
    ]
    accepted, rejected = scaffold.register_material_vocab(entries)
    assert rejected == 0, f"不該有任何一條被擋：accepted={[e['en'] for e in accepted]}"
    assert {"thirty", "late"} <= {e["en"] for e in accepted}


# ---------------------------------------------------------------------------
# 缺陷 2（分類）：天氣詞不該再被塞進 color
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cat", ["weather", "place", "time"])
def test_new_material_cats_are_accepted(cat):
    entry = {"zh": "測試詞", "en": "zzztest", "cat": cat, "np": "zzztest",
             "sent": "This is a zzztest."}
    assert scaffold._is_valid_material_entry(entry, *_ctx()) is True


def test_material_prompt_offers_every_valid_cat():
    """prompt 的分類清單與驗證關卡不得漂移。

    Unit 3 的天氣詞全被標成 color，根因就是 prompt 裡根本沒有 weather 可選——
    agent 只能在被告知的分類裡硬挑一個。
    """
    for cat in scaffold._MATERIAL_CATS:
        assert cat in material._CAT_CHOICES
        assert cat in material._SYSTEM_PROMPT
        assert cat in material._build_user_prompt("Unit 3: How's the weather?")


def test_every_material_cat_has_a_chinese_label():
    """教師端的興趣統計依 cat 聚合，缺標籤會直接顯示英文 key。"""
    from server import profile

    for cat in scaffold._MATERIAL_CATS:
        assert cat in material._CAT_ZH
        assert cat in profile._CAT_LABEL


# ---------------------------------------------------------------------------
# 缺陷 2（英文方向查詢）：孩子講出教材裡的英文字要接得住
# ---------------------------------------------------------------------------

def test_english_word_in_mixed_input_drives_the_readalong():
    """實測逐字稿的重現：帶讀句必須是 sunny 的例句，不是今日目標句。"""
    _register(3)
    r = scaffold.respond("今天天氣 sunny", turn_index=0,
                         lesson_topic="animal",
                         lesson_target_sentence="I see a dog.")
    assert r.target_sentence == "It is sunny today."
    assert r.matched is True
    assert "I see a dog." not in r.reply_text


def test_find_en_vocab_prefers_the_longest_hit():
    """living room 要贏過同句出現的 in，孩子講的是前者。"""
    _register(4)
    assert scaffold._find_en_vocab("我在 living room") == "客廳"


def test_find_en_vocab_respects_word_boundaries():
    """不得用子字串命中——否則 in 會在 living／drinking 裡到處誤觸。"""
    _register(4)
    assert scaffold._find_en_vocab("我在 dining") != "在……裡面"


def test_no_vocab_hit_still_falls_back_to_lesson_target():
    """完全沒命中詞庫時維持原行為，退回今日目標句。"""
    r = scaffold.respond("今天好無聊", turn_index=0,
                         lesson_topic="animal",
                         lesson_target_sentence="I see a dog.")
    assert r.target_sentence == "I see a dog."
    assert r.matched is False


# ---------------------------------------------------------------------------
# 缺陷 1：帶讀句對齊本週單元
# ---------------------------------------------------------------------------

def test_target_sentence_comes_from_the_current_unit():
    """診斷說今天練 animal，但老師本週教 Unit 6 → 帶讀句要來自 Unit 6。"""
    _register(seed_units.CURRENT_UNIT_NO)
    unit_sents = {e["sent"] for e in
                  seed_units.UNIT_MATERIALS[seed_units.CURRENT_UNIT_NO]["entries"]}

    picked = lesson.pick_target_sentence("animal", None)

    assert picked in unit_sents
    assert picked != "I see a dog."


def test_build_lesson_target_follows_the_unit_but_keeps_the_topic():
    """topic 仍由診斷決定（延伸問句、遊戲出題靠它），只有帶讀句改成跟單元走。"""
    _register(seed_units.CURRENT_UNIT_NO)
    diagnoses = [{"level_state": {"topic": "food", "target_form": "短句 3-4 詞"}}]

    lp = lesson.build_lesson(diagnoses, None)

    assert lp.topic == "food"
    assert lp.target_sentence in {
        e["sent"] for e in
        seed_units.UNIT_MATERIALS[seed_units.CURRENT_UNIT_NO]["entries"]}


def test_unit_sentences_put_the_child_s_learning_vocab_first():
    """同一份教材、每個孩子不同起點：來源是全班共同的單元，排序才依 profile。"""
    _register(seed_units.CURRENT_UNIT_NO)
    profile = {"learning_vocab": [{"en": "singing", "zh": "正在唱歌"}]}

    out = lesson.unit_sentences(profile, limit=5)

    assert out[0] == "They are singing a song."
    assert lesson.pick_target_sentence("animal", profile) == out[0]


def test_falls_back_to_topic_when_the_unit_has_no_material():
    """教材還沒載入（容器剛起、seed 尚未 replay）→ 維持原本依 cat 選句的行為。"""
    picked = lesson.pick_target_sentence("food", None)
    assert ("eat" in picked) or ("drink" in picked)


def test_topic_sentences_first_entry_still_matches_the_target():
    """教練 prompt 的進度清單第一句 = 今日目標句，有沒有單元教材都成立。"""
    _register(seed_units.CURRENT_UNIT_NO)
    out = lesson.topic_sentences("animal", limit=4)
    assert out and out[0] == lesson.pick_target_sentence("animal")
    assert len(set(out)) == len(out), "不可以有重複"


def test_unit_entries_include_curriculum_words_not_added_by_the_agent():
    """Unit 5 字表裡的 breakfast/lunch/dinner 早在課綱 VOCAB，也算本週單元的字。

    教材 agent 正確拒絕重複新增它們，但那不代表它們不是這一課要練的內容。
    _unit_entries 查的是執行期的 VOCAB 而不是 agent 的產出清單，才接得住這點。
    """
    ens = {e["en"] for e in lesson._unit_entries(unit_no=5)}
    assert {"breakfast", "lunch", "dinner"} <= ens
