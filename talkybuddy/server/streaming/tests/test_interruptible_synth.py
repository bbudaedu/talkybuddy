"""可中止逐句合成核心的單元測試（真 sherpa，不依賴 Pipecat）。

從 spike/a2_pipecat/tests/test_interruptible_synth.py 搬入，import 路徑改 server.streaming.*。
"""
import numpy as np
import pytest

from server.streaming.interruptible_synth import InterruptibleSynth, split_sentences
from server.streaming.tests.sherpa_voice import load_zh_voice

_REPLY = "你好呀，我是企鵝。今天天氣真好。我們一起來玩遊戲吧。你想先做什麼呢？"


def test_split_sentences_by_punct():
    out = split_sentences(_REPLY)
    assert out == ["你好呀，我是企鵝。", "今天天氣真好。", "我們一起來玩遊戲吧。", "你想先做什麼呢？"]


def test_split_sentences_drops_empty():
    assert split_sentences("  。！  ？ ") == []
    assert split_sentences("") == []


@pytest.fixture(scope="module")
def voice():
    return load_zh_voice()


def test_synth_all_when_not_interrupted(voice):
    synth = InterruptibleSynth(voice)
    produced = list(synth.synth_sentences(split_sentences(_REPLY)))
    assert len(produced) == 4
    for _, audio in produced:
        assert np.asarray(audio.samples).size > 0


def test_callback_returns_nonzero_only_when_interrupted(voice):
    synth = InterruptibleSynth(voice)
    dummy = np.zeros(4, dtype=np.float32)
    assert synth._callback(dummy, 0.5) == 0
    synth.interrupt()
    assert synth._callback(dummy, 0.5) != 0


def test_stops_at_sentence_boundary_after_interrupt(voice):
    """第 1 句合成後 interrupt → 不再合成後續句（句界乾淨停）。"""
    synth = InterruptibleSynth(voice)
    sents = split_sentences(_REPLY)
    seen = []
    for idx, _audio in synth.synth_sentences(sents):
        seen.append(idx)
        if idx == 0:
            synth.interrupt()
    assert seen == [0]
