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


def _mk_event(payload: dict):
    """把 dict payload 包成 SDK bidi input chunk。lazy import SDK 型別。"""
    from aws_sdk_bedrock_runtime.models import (
        InvokeModelWithBidirectionalStreamInputChunk as InChunk,
        BidirectionalInputPayloadPart as InPart,
    )
    return InChunk(value=InPart(bytes_=json.dumps(payload).encode("utf-8")))


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

    def _build_client(self):
        """lazy 建 bidi client（SigV4 需顯式 EnvironmentCredentialsResolver）。"""
        from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient
        from aws_sdk_bedrock_runtime.config import Config
        from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver
        config = Config(
            region=self.region,
            aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
        )
        return BedrockRuntimeClient(config=config)

    async def _send(self, payload: dict) -> None:
        await self._stream.input_stream.send(_mk_event(payload))

    async def start(self, system_prompt: str) -> None:
        from aws_sdk_bedrock_runtime.models import (
            InvokeModelWithBidirectionalStreamOperationInput as OpInput,
        )
        client = self._build_client()
        self._stream = await client.invoke_model_with_bidirectional_stream(
            OpInput(model_id=self.model_id))
        sys_content = str(uuid.uuid4())
        # 1) sessionStart
        await self._send({"event": {"sessionStart": {"inferenceConfiguration": {
            "maxTokens": 512, "topP": 0.9, "temperature": 0.7}}}})
        # 2) promptStart（含 24kHz 語音輸出 + voiceId）
        await self._send({"event": {"promptStart": {
            "promptName": self._prompt_name,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": 24000,
                "sampleSizeBits": 16, "channelCount": 1,
                "voiceId": self.voice, "encoding": "base64", "audioType": "SPEECH"}}}})
        # 3) system prompt（TEXT）
        await self._send({"event": {"contentStart": {
            "promptName": self._prompt_name, "contentName": sys_content, "type": "TEXT",
            "interactive": True, "role": "SYSTEM",
            "textInputConfiguration": {"mediaType": "text/plain"}}}})
        await self._send({"event": {"textInput": {
            "promptName": self._prompt_name, "contentName": sys_content,
            "content": system_prompt}}})
        await self._send({"event": {"contentEnd": {
            "promptName": self._prompt_name, "contentName": sys_content}}})
        # 啟動背景 receive loop（Task 4 以完整多 completion 收斂版取代 _receive_loop）
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def send_audio(self, pcm16: bytes) -> None:
        """送一塊 16kHz PCM16；首塊前自動開 AUDIO 內容區。"""
        if self._audio_content is None:
            self._audio_content = str(uuid.uuid4())
            await self._send({"event": {"contentStart": {
                "promptName": self._prompt_name, "contentName": self._audio_content,
                "type": "AUDIO", "interactive": True, "role": "USER",
                "audioInputConfiguration": {
                    "mediaType": "audio/lpcm", "sampleRateHertz": 16000,
                    "sampleSizeBits": 16, "channelCount": 1,
                    "audioType": "SPEECH", "encoding": "base64"}}}})
        b64 = base64.b64encode(pcm16).decode("ascii")
        await self._send({"event": {"audioInput": {
            "promptName": self._prompt_name, "contentName": self._audio_content,
            "content": b64}}})

    async def end_user_turn(self) -> None:
        """結束本輪使用者音訊（送 contentEnd）；不送 promptEnd（協定踩雷）。"""
        if self._audio_content is not None:
            await self._send({"event": {"contentEnd": {
                "promptName": self._prompt_name, "contentName": self._audio_content}}})
            self._audio_content = None

    async def _receive_loop(self) -> None:
        """佔位版（Task 4 取代）：立即放入結束哨兵。"""
        await self._queue.put(None)

    async def close(self) -> None:
        """最小收尾（Task 4 補送 promptEnd/sessionEnd）：關 input、取消 recv、關流。"""
        try:
            await self._stream.input_stream.close()
        except Exception:
            pass
        if self._recv_task is not None:
            self._recv_task.cancel()
            self._recv_task = None
        try:
            await self._stream.close()
        except Exception:
            pass
