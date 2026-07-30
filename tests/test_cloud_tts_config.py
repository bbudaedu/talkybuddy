# -*- coding: utf-8 -*-
"""雲端情緒 TTS 的 config 環境變數：預設值與型別。"""

from __future__ import annotations

from server import config


def test_cloud_tts_defaults_present():
    # 未設環境變數時的預設值（金鑰空字串；voice/模型/逾時/情緒參數有預設）
    assert config.ELEVENLABS_API_KEY == ""
    # voice 預設 Alice（外國腔中文＝外國企鵝角色）；克隆/換聲僅覆蓋此值。
    assert config.ELEVENLABS_VOICE_ID == "Xb7hH8MSUJpSbSDYk0k2"
    # 模型預設 eleven_turbo_v2_5（2026-07-30 從 eleven_v3 換過來）：真機 A/B
    # 試聽後確認 turbo 相對邊緣 sherpa-onnx 已有明顯提升，不值得為 v3 多付延遲
    # （2.97s vs 0.37s）。
    assert config.ELEVENLABS_MODEL == "eleven_turbo_v2_5"
    assert isinstance(config.CLOUD_TTS_TIMEOUT_S, float)
    # 1.5s 不變：對 turbo（實測中位數 0.37s）有 4 倍餘裕，同時滿足「斷網降級
    # < 1–2 秒」的硬上界（見 tests/test_pipeline_timeout_isolation.py）。
    # 先前配 eleven_v3（約 3s）時每次必逾時——問題出在模型，不是這個值。
    assert config.CLOUD_TTS_TIMEOUT_S == 1.5
    # voice_settings 情緒參數。
    assert isinstance(config.ELEVENLABS_STABILITY, float)
    assert config.ELEVENLABS_STABILITY == 0.5
    assert isinstance(config.ELEVENLABS_SIMILARITY_BOOST, float)
    assert config.ELEVENLABS_SIMILARITY_BOOST == 0.8
    assert isinstance(config.ELEVENLABS_STYLE, float)
    assert config.ELEVENLABS_STYLE == 0.2
    assert isinstance(config.ELEVENLABS_USE_SPEAKER_BOOST, bool)
    assert config.ELEVENLABS_USE_SPEAKER_BOOST is True
    # 放慢語速預設 0.90（比原聲再慢一點點）。達成方式依模型分流，見
    # tests/test_cloud_tts_speed_routing.py。
    assert isinstance(config.CLOUD_TTS_SPEED, float)
    assert config.CLOUD_TTS_SPEED == 0.90
