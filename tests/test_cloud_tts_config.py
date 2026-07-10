# -*- coding: utf-8 -*-
"""雲端情緒 TTS 的 config 環境變數：預設值與型別。"""

from __future__ import annotations

from server import config


def test_cloud_tts_defaults_present():
    # 未設環境變數時的預設值（金鑰空字串；voice/模型/逾時/情緒參數有預設）
    assert config.ELEVENLABS_API_KEY == ""
    # voice 預設 Alice（外國腔中文＝外國企鵝角色）；克隆/換聲僅覆蓋此值。
    assert config.ELEVENLABS_VOICE_ID == "Xb7hH8MSUJpSbSDYk0k2"
    # 模型預設 eleven_v3（情緒表現最強；驗證 v3 忽略 speed，改用情緒參數）。
    assert config.ELEVENLABS_MODEL == "eleven_v3"
    assert isinstance(config.CLOUD_TTS_TIMEOUT_S, float)
    assert config.CLOUD_TTS_TIMEOUT_S == 6.0
    # v3 voice_settings 情緒參數（取代無效的 speed）。
    assert isinstance(config.ELEVENLABS_STABILITY, float)
    assert config.ELEVENLABS_STABILITY == 0.5
    assert isinstance(config.ELEVENLABS_SIMILARITY_BOOST, float)
    assert config.ELEVENLABS_SIMILARITY_BOOST == 0.8
    assert isinstance(config.ELEVENLABS_STYLE, float)
    assert config.ELEVENLABS_STYLE == 0.2
    assert isinstance(config.ELEVENLABS_USE_SPEAKER_BOOST, bool)
    assert config.ELEVENLABS_USE_SPEAKER_BOOST is True
