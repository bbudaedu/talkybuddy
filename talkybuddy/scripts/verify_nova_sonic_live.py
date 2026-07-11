# -*- coding: utf-8 -*-
"""實機 smoke test：真打 Amazon Nova (2) Sonic 端到端雙向串流（S2S）。

驗證目標：
    1. 這帳號能否實際 **開啟 bidirectional stream 並收到模型事件**（＝真的可用，非只有 access）。
    2. 觀察 textOutput：USER 端 ASR 逐字稿 + ASSISTANT 端回覆 → 判斷中文/英文能力。
    3. 統計 audioOutput bytes → 確認有語音輸出。

⚠️ 認證：Nova Sonic 的 InvokeModelWithBidirectionalStream 在 aws-sdk-bedrock-runtime
    只掛 **SigV4**，不吃 bearer token（AWS_BEARER_TOKEN_BEDROCK 無效）。
    必須提供 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY（可選 AWS_SESSION_TOKEN）。

用法：
    cd talkybuddy
    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... BEDROCK_REGION=us-east-1 \
      .venv/bin/python scripts/verify_nova_sonic_live.py [音檔.wav]

可調環境變數：
    NOVA_MODEL_ID   預設 amazon.nova-2-sonic-v1:0
    BEDROCK_REGION  預設 us-east-1
    NOVA_VOICE      預設 tiffany（Nova 2 polyglot 嗓音）
    NOVA_SYSTEM     system prompt（預設要求用繁體中文簡短回覆 → 測中文輸出）
    NOVA_MAX_SECONDS 只送前 N 秒音訊（預設 6，控 token）
    NOVA_WAV        音檔路徑（等同位置參數；預設 docs/voice-reference/alice_v3_baseline.wav）
                    → 想驗「中文 ASR」請改餵中文語音檔。
"""
import asyncio
import base64
import json
import os
import sys
import uuid
import wave

try:
    import audioop  # Python 3.12 仍內建（3.13 移除）
except ImportError:  # pragma: no cover
    audioop = None

DEFAULT_WAV = "docs/voice-reference/alice_v3_baseline.wav"
MODEL_ID = os.environ.get("NOVA_MODEL_ID", "amazon.nova-2-sonic-v1:0")
REGION = os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or "us-east-1"
VOICE = os.environ.get("NOVA_VOICE", "tiffany")
SYSTEM = os.environ.get(
    "NOVA_SYSTEM",
    "你是友善的英語學伴。請務必只用繁體中文（台灣用語）簡短回覆一句話。",
)
MAX_SECONDS = float(os.environ.get("NOVA_MAX_SECONDS", "0"))  # 0 = 送完整音訊
CHUNK_SAMPLES = 1024  # 每個 audioInput 塊的樣本數（16-bit → 2048 bytes）


def load_pcm16k(path):
    """讀 wav → 16kHz / mono / 16-bit little-endian PCM bytes。"""
    if audioop is None:
        raise RuntimeError("此 Python 無 audioop，無法重採樣；請改用 3.12 或先轉好 16kHz mono PCM。")
    w = wave.open(path, "rb")
    ch, sw, fr = w.getnchannels(), w.getsampwidth(), w.getframerate()
    pcm = w.readframes(w.getnframes())
    w.close()
    if sw != 2:
        pcm = audioop.lin2lin(pcm, sw, 2)
    if ch == 2:
        pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
    if fr != 16000:
        pcm, _ = audioop.ratecv(pcm, 2, 1, fr, 16000, None)
    if MAX_SECONDS > 0:
        max_bytes = int(MAX_SECONDS * 16000) * 2
        if len(pcm) > max_bytes:
            pcm = pcm[:max_bytes]
    pcm += b"\x00\x00" * int(0.8 * 16000)  # 補 0.8s 靜音，幫 VAD 判 end-of-speech
    return pcm, (ch, sw, fr)


def _ev(chunk_cls, part_cls, payload):
    return chunk_cls(value=part_cls(bytes_=json.dumps(payload).encode("utf-8")))


async def run():
    # 認證前置檢查（bidi 只吃 SigV4）
    if not (os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY")):
        print("❌ 缺 SigV4 憑證：Nova Sonic 雙向串流不吃 bearer token。")
        print("   請設 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY（可選 AWS_SESSION_TOKEN）後重跑。")
        return 2

    from aws_sdk_bedrock_runtime.client import BedrockRuntimeClient
    from aws_sdk_bedrock_runtime.config import Config
    from aws_sdk_bedrock_runtime.models import (
        InvokeModelWithBidirectionalStreamOperationInput as OpInput,
        InvokeModelWithBidirectionalStreamInputChunk as InChunk,
        BidirectionalInputPayloadPart as InPart,
    )
    # 這版 SDK 的 SigV4 需顯式 identity resolver；直接塞 key 到 Config 不夠。
    from smithy_aws_core.identity.environment import EnvironmentCredentialsResolver

    wav = os.environ.get("NOVA_WAV") or (sys.argv[1] if len(sys.argv) > 1 else DEFAULT_WAV)
    pcm, orig = load_pcm16k(wav)
    print("=" * 64)
    print("Nova Sonic 端到端雙向串流 smoke test")
    print("=" * 64)
    print(f"model_id  = {MODEL_ID}")
    print(f"region    = {REGION}")
    print(f"voice     = {VOICE}")
    print(f"wav       = {wav}  原始{orig} → 16kHz/mono/16bit，送 {len(pcm)/2/16000:.2f}s")
    print(f"system    = {SYSTEM!r}")
    print("-" * 64)

    config = Config(
        region=REGION,
        aws_credentials_identity_resolver=EnvironmentCredentialsResolver(),
    )
    client = BedrockRuntimeClient(config=config)

    prompt_name = str(uuid.uuid4())
    sys_content = str(uuid.uuid4())
    audio_content = str(uuid.uuid4())

    def mk(payload):
        return _ev(InChunk, InPart, payload)

    collected = {"user_text": [], "assistant_text": [], "audio_bytes": 0,
                 "events": {}, "errors": []}

    async def receive_loop():
        _, out_stream = await stream.await_output()
        print("[recv] await_output 完成，開始收事件…", flush=True)
        while True:
            try:
                event = await out_stream.receive()
            except Exception as e:  # noqa: BLE001
                collected["errors"].append(f"receive raised: {type(e).__name__}: {e}")
                return
            if event is None:
                return
            raw = getattr(getattr(event, "value", None), "bytes_", None)
            if raw is None:
                # 可能是 modeled exception 事件
                collected["errors"].append(f"non-payload event: {type(event).__name__}")
                continue
            try:
                msg = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            ev = msg.get("event", {})
            for k in ev:
                collected["events"][k] = collected["events"].get(k, 0) + 1
            if "textOutput" in ev:
                t = ev["textOutput"]
                role = t.get("role", "?")
                content = t.get("content", "")
                if role == "USER":
                    collected["user_text"].append(content)
                elif role == "ASSISTANT":
                    collected["assistant_text"].append(content)
            elif "audioOutput" in ev:
                c = ev["audioOutput"].get("content", "")
                try:
                    collected["audio_bytes"] += len(base64.b64decode(c))
                except Exception:  # noqa: BLE001
                    pass
            elif "completionEnd" in ev:
                # Nova Sonic 分多個 completion：先 user-ASR、後 assistant 回覆。
                # 只有已收到助理內容才收尾，否則繼續等下一個 completion。
                if collected["assistant_text"] or collected["audio_bytes"] > 0:
                    print("[recv] 已收到助理回覆 + completionEnd，收尾。", flush=True)
                    return
                print("[recv] completionEnd（user-ASR 段），繼續等助理回覆…", flush=True)

    try:
        stream = await client.invoke_model_with_bidirectional_stream(OpInput(model_id=MODEL_ID))
    except Exception as e:  # noqa: BLE001
        print(f"❌ 開串流失敗：{type(e).__name__}: {e}")
        return 1

    print("[send] 串流已開，準備送事件…", flush=True)

    async def send_all():
        pub = stream.input_stream
        # 1) sessionStart
        await pub.send(mk({"event": {"sessionStart": {"inferenceConfiguration": {
            "maxTokens": 512, "topP": 0.9, "temperature": 0.7}}}}))
        # 2) promptStart（含語音輸出設定）
        await pub.send(mk({"event": {"promptStart": {
            "promptName": prompt_name,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": 24000, "sampleSizeBits": 16,
                "channelCount": 1, "voiceId": VOICE, "encoding": "base64", "audioType": "SPEECH"}}}}))
        # 3) system prompt（TEXT）
        await pub.send(mk({"event": {"contentStart": {
            "promptName": prompt_name, "contentName": sys_content, "type": "TEXT",
            "interactive": True, "role": "SYSTEM",
            "textInputConfiguration": {"mediaType": "text/plain"}}}}))
        await pub.send(mk({"event": {"textInput": {
            "promptName": prompt_name, "contentName": sys_content, "content": SYSTEM}}}))
        await pub.send(mk({"event": {"contentEnd": {
            "promptName": prompt_name, "contentName": sys_content}}}))
        # 4) 音訊內容（AUDIO）
        await pub.send(mk({"event": {"contentStart": {
            "promptName": prompt_name, "contentName": audio_content, "type": "AUDIO",
            "interactive": True, "role": "USER",
            "audioInputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": 16000, "sampleSizeBits": 16,
                "channelCount": 1, "audioType": "SPEECH", "encoding": "base64"}}}}))
        step = CHUNK_SAMPLES * 2
        for i in range(0, len(pcm), step):
            b64 = base64.b64encode(pcm[i:i + step]).decode("ascii")
            await pub.send(mk({"event": {"audioInput": {
                "promptName": prompt_name, "contentName": audio_content, "content": b64}}}))
            await asyncio.sleep(0.01)  # 輕度節流，貼近即時串流
        await pub.send(mk({"event": {"contentEnd": {
            "promptName": prompt_name, "contentName": audio_content}}}))
        print("[send] 音訊 contentEnd 已送；先不送 promptEnd，等模型自動回覆…", flush=True)

    recv = asyncio.create_task(receive_loop())
    try:
        await asyncio.wait_for(send_all(), timeout=60)
        await asyncio.wait_for(recv, timeout=90)  # 等模型回覆到 completionEnd
    except asyncio.TimeoutError:
        collected["errors"].append("逾時（未見 completionEnd）")
        recv.cancel()
    except Exception as e:  # noqa: BLE001
        collected["errors"].append(f"send/recv raised: {type(e).__name__}: {e}")
    finally:
        # 收到回覆後才收尾：promptEnd + sessionEnd + 關閉 input
        try:
            await stream.input_stream.send(mk({"event": {"promptEnd": {"promptName": prompt_name}}}))
            await stream.input_stream.send(mk({"event": {"sessionEnd": {}}}))
            await stream.input_stream.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await stream.close()
        except Exception:  # noqa: BLE001
            pass

    # 結果
    print("\n--- 收到的事件種類統計 ---")
    for k, v in sorted(collected["events"].items()):
        print(f"  {k}: {v}")
    user_txt = " ".join(collected["user_text"]).strip()
    asst_txt = " ".join(collected["assistant_text"]).strip()
    print("\n--- USER ASR 逐字稿 ---")
    print(f"  {user_txt!r}")
    print("\n--- ASSISTANT 回覆文字 ---")
    print(f"  {asst_txt!r}")
    print(f"\n--- audioOutput 累計 bytes = {collected['audio_bytes']} "
          f"(~{collected['audio_bytes']/2/24000:.2f}s @24kHz) ---")
    if collected["errors"]:
        print("\n--- 錯誤/警告 ---")
        for e in collected["errors"]:
            print(f"  ⚠️ {e}")

    has_cjk = any("一" <= c <= "鿿" for c in (user_txt + asst_txt))
    ok = bool(asst_txt or collected["audio_bytes"] > 0)
    print("\n" + "=" * 64)
    print(f"可呼叫（收到模型輸出）= {'✅ 是' if ok else '❌ 否'}")
    print(f"輸出含中文字元        = {'✅ 是' if has_cjk else '—（本次無）'}")
    print("=" * 64)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
