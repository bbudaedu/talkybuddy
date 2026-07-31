# -*- coding: utf-8 -*-
"""量 aplay 緩衝可以調到多小而不 underrun（板子上跑，需佔用喇叭）。

    ssh root@192.168.31.78 'systemctl stop talkybuddy-pipecat; cd /root/pipecat-lab && \\
      PYTHONPATH=/root/pipecat-lab ./.venv/bin/python -u edge/probes/probe_playback_buffer.py'

2026-08-01 板子實測結果見 PIPECAT_HANDOFF.md 三之四。

## 為什麼要有這支

`PlaybackGate` 的死區 = 音訊長度 + **aplay 緩衝延遲** + tail。板子實測死區
8.0 秒，其中 2.0 秒純粹是緩衝。調小就直接省下來。

但調小的失敗樣子是**聲音斷斷續續**，比慢更糟。那個失敗有一個客觀訊號：
aplay 會往 stderr 印 `underrun!!!`。所以不必靠耳朵也判得出來。

## 模擬的是哪個情境

玩偶逐句推 TTS：講完一句 → 合成下一句（1.2 秒空檔）→ 再講。那個空檔正是
ALSA 環形緩衝會餓死的地方，也正是 keepalive 要填的洞。

用 `build_aplay_argv` 而不是自己拼 argv：取樣率／格式／`-t raw` 都必須與正式
路徑逐字相同，否則測到的不是同一件事。
"""
import subprocess
import sys
import threading
import time

sys.path.insert(0, "/root/pipecat-lab")

from edge.runtime.live_client import build_aplay_argv  # noqa: E402

RATE = 22050
DEVICE = "plughw:0,0"
SENTENCE_S = 1.5        # 一句話的長度
GAP_S = 1.2             # 合成下一句的空檔
SENTENCES = 4
KEEPALIVE_S = 0.1


def _tone(seconds: float) -> bytes:
    """拿正弦波當「玩偶在講話」——內容不重要，時長與連續性才重要。"""
    import math
    import struct

    n = int(RATE * seconds)
    return struct.pack(
        f"<{n}h",
        *(int(8000 * math.sin(2 * math.pi * 440 * i / RATE)) for i in range(n)),
    )


def run(buffer_us: int, keepalive: bool) -> tuple[int, float]:
    """跑一次模擬播放。

    Args:
        buffer_us: aplay 的 `--buffer-time`。
        keepalive: 空檔要不要餵靜音。

    Returns:
        (underrun 次數, 總耗時秒)
    """
    argv = build_aplay_argv(DEVICE, RATE)
    argv[argv.index("--buffer-time") + 1] = str(buffer_us)
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stderr=subprocess.PIPE, text=False
    )

    stop = threading.Event()
    silence = b"\x00" * int(RATE * 2 * KEEPALIVE_S)
    last_write = [time.monotonic()]
    lock = threading.Lock()

    def keeper():
        while not stop.is_set():
            time.sleep(KEEPALIVE_S)
            if time.monotonic() - last_write[0] < KEEPALIVE_S:
                continue
            with lock:
                try:
                    proc.stdin.write(silence)
                    proc.stdin.flush()
                except Exception:
                    return

    t = threading.Thread(target=keeper, daemon=True)
    if keepalive:
        t.start()

    t0 = time.monotonic()
    sentence = _tone(SENTENCE_S)
    for _ in range(SENTENCES):
        with lock:
            proc.stdin.write(sentence)
            proc.stdin.flush()
            last_write[0] = time.monotonic()
        time.sleep(GAP_S)          # 合成下一句的空檔
    time.sleep(SENTENCE_S + 0.5)   # 等最後一句播完
    stop.set()

    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    err = proc.stderr.read().decode("utf-8", "replace")
    return err.lower().count("underrun"), time.monotonic() - t0


if __name__ == "__main__":
    print(f"{'緩衝(us)':>10} {'keepalive':>10} {'underrun':>9} {'耗時(s)':>8}")
    print("-" * 42)
    for keepalive in (False, True):
        for buf in (2_000_000, 1_000_000, 500_000, 300_000):
            n, secs = run(buf, keepalive)
            flag = "❌" if n else "✅"
            print(f"{buf:>10} {str(keepalive):>10} {n:>9} {secs:>8.1f}  {flag}")
            time.sleep(1)
