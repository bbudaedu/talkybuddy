"""在 .venv-streaming 下，espeak-ng-data 定位需 venv-無關，load_zh_voice() 可成功。"""
from pathlib import Path

from server.streaming.tests import sherpa_voice


def test_espeak_data_dir_resolves_in_current_venv():
    p = sherpa_voice._espeak_data_dir()
    assert p is not None
    assert Path(p).is_dir()
    assert (Path(p) / "phontab").exists() or any(Path(p).iterdir())


def test_load_zh_voice_succeeds():
    voice = sherpa_voice.load_zh_voice()
    assert voice is not None
    assert int(voice.sample_rate) > 0
