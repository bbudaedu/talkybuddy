from server import lesson


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
