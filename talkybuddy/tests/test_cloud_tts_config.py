# -*- coding: utf-8 -*-
"""雲端情緒 TTS 的 config 環境變數：預設值與型別。"""

from __future__ import annotations

from server import config


def test_cloud_tts_defaults_present():
    # 未設環境變數時的預設值（金鑰/voice 空字串、模型與逾時有預設）
    assert config.ELEVENLABS_API_KEY == ""
    assert config.ELEVENLABS_VOICE_ID == ""
    assert config.ELEVENLABS_MODEL == "eleven_flash_v2_5"
    assert isinstance(config.CLOUD_TTS_TIMEOUT_S, float)
    assert config.CLOUD_TTS_TIMEOUT_S == 6.0
