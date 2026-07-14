from server import lesson
from server import curriculum


def test_pick_target_sentence_by_topic():
    s = lesson.pick_target_sentence("food", None)
    assert isinstance(s, str) and s
    # food 類的例句都在講吃/喝
    assert ("eat" in s) or ("drink" in s)


def test_pick_target_sentence_prefers_learning_vocab():
    # profile 指定正在學 banana → 應挑到 banana 的例句
    profile = {"learning_vocab": [{"en": "banana", "zh": "香蕉", "cat": "food"}]}
    s = lesson.pick_target_sentence("food", profile)
    assert "banana" in s


def test_pick_target_sentence_unknown_topic_falls_back():
    s = lesson.pick_target_sentence("no_such_topic", None)
    assert s == "How are you today?"


def test_pick_target_sentence_missing_profile_ok():
    s = lesson.pick_target_sentence("animal", {})
    assert isinstance(s, str) and s


def test_build_lesson_cold_start_uses_defaults():
    lp = lesson.build_lesson([], None)
    assert lp.topic == curriculum.TOPIC_ORDER[0]  # "animal"
    assert lp.target_form == curriculum._TARGET_FORM[1]
    assert lp.directive is None
    assert isinstance(lp.target_sentence, str) and lp.target_sentence


def test_build_lesson_uses_latest_diagnosis():
    diagnoses = [{
        "companion_directive": {"difficulty": "up"},
        "level_state": {"topic": "food", "target_form": "短句 3-4 詞"},
    }]
    lp = lesson.build_lesson(diagnoses, None)
    assert lp.topic == "food"
    assert lp.target_form == "短句 3-4 詞"
    assert lp.directive and isinstance(lp.directive, str)
    assert ("eat" in lp.target_sentence) or ("drink" in lp.target_sentence)


def test_build_lesson_missing_level_state_falls_back():
    lp = lesson.build_lesson([{"companion_directive": {"difficulty": "keep"}}], None)
    assert lp.topic == curriculum.TOPIC_ORDER[0]
    assert isinstance(lp.target_sentence, str) and lp.target_sentence
