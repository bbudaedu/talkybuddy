# -*- coding: utf-8 -*-
"""nova_sonic：Amazon Nova 2 Sonic 即時雙向串流（S2S）一場對話的封裝。

把 scripts/verify_nova_sonic_live.py 已實機證實的 InvokeModelWithBidirectionalStream
協定收斂成 NovaSonicSession。所有已知踩雷（EnvironmentCredentialsResolver、
contentEnd 後不立刻 promptEnd、多 completion 收斂）都封裝在此。

重依賴（aws_sdk_bedrock_runtime / smithy_aws_core）一律 lazy import；
import 本模組絕不可炸（無 SDK 時仍要能啟動，比照 server/cloud_llm.py）。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from dataclasses import dataclass

_log = logging.getLogger(__name__)


def available() -> bool:
    """能否啟用 Nova Sonic bidi：SigV4 env 齊全 + SDK 可 import。"""
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        return False
    try:
        import aws_sdk_bedrock_runtime  # noqa: F401
        import smithy_aws_core  # noqa: F401
    except Exception:
        return False
    return True


@dataclass
class NovaEvent:
    """events() yield 的事件：kind ∈ {"transcript","audio","turn_end"}。"""
    kind: str
    role: str = ""          # transcript：USER / ASSISTANT
    text: str = ""          # transcript 文字
    audio: bytes = b""      # audio：24kHz PCM16 raw bytes


class NovaSonicSession:
    """封裝一場 Nova Sonic bidi 對話。呼叫順序：
    start() → (send_audio()* → end_user_turn() → events() 迭代)* → close()。
    """

    def __init__(self, model_id: str, voice: str, region: str):
        self.model_id = model_id
        self.voice = voice
        self.region = region
        self._stream = None
        self._prompt_name = str(uuid.uuid4())
        self._audio_content: str | None = None   # 目前 AUDIO 內容的 contentName
        self._queue: asyncio.Queue[NovaEvent | None] = asyncio.Queue()
        self._recv_task: asyncio.Task | None = None
