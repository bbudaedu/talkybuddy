"""實體按鍵探測：讀 `/dev/input/event*`，印出按了哪一顆。

**為什麼需要這支**：裝置沒有螢幕也沒有鍵盤，而 `local_client.py` 的錄音觸發是
`audio_io.wait_for_trigger()` → `input("按 Enter…")`，需要有人 SSH 進去按 Enter。
那不是可以上台的觸發方式，而原生喚醒詞（`wake_listener.py`）真人辨識率已判 NO-GO。

`/proc/bus/input/devices` 顯示板上有 `mtk-pmic-keys`（event1），
`B: KEY=10004000000000` 解碼為 bit 102 與 116 = **KEY_HOME 與 KEY_POWER**。
若 KEY_HOME 真的按得到，`wait_for_trigger()` 就能改成讀它——
**「按一下玩偶就開始講話」**，不需要焊任何東西。

⚠️ **不要按 KEY_POWER**，可能觸發關機。本探針會把它標成紅字提醒。

**這支同時是「按了沒反應」的診斷工具**：它會先印出名稱↔節點對照、以及
`local_client` 實際會去讀哪一個節點，然後監聽**所有** `/dev/input/event*`。
若事件從別的節點進來，會直接標出來並給出該設的環境變數。

會這樣做是因為節點編號會位移：USB 音效裝置（麥克風）插上去也會註冊 input
節點。而 `_key_device_usable()` 的「存在且可讀」對 USB 那顆一樣成立，於是
迴圈會阻塞在一個永遠不送 KEY_HOME 的裝置上——症狀是「印出提示、按了完全
沒反應」，不是明確報錯。

用法（裝置上，Ctrl-C 結束）。**必須用 `-m`**，本模組會 import `edge.runtime.audio_io`，
直接跑檔案路徑會 `ModuleNotFoundError`（同 `dump_recent_turns.py` 的陷阱）：

    ssh -t root@192.168.31.78 'cd /root/talkybuddy && \
      ./.venv/bin/python -m edge.runtime.key_probe'

不需要 `evdev` 模組——input_event 是固定的 24-byte 結構，標準函式庫即可。
"""
import argparse
import glob
import select
import signal
import struct
import sys
import time

from edge.runtime import audio_io

# struct input_event（64-bit）：__kernel_ulong_t sec, usec; __u16 type, code; __s32 value
_FMT = "llHHi"
_SIZE = struct.calcsize(_FMT)

EV_KEY = 0x01
_VALUE = {0: "放開", 1: "按下", 2: "長按重複"}

# 只列我們關心的；其餘直接印碼號
_NAMES = {102: "KEY_HOME（實測不送事件）", 116: "KEY_POWER（觸發鍵）",
          114: "KEY_VOLUMEDOWN", 115: "KEY_VOLUMEUP", 158: "KEY_BACK"}


def _all_event_devices() -> list[str]:
    """掃出所有 input 節點。

    不寫死 event0/event1：USB 音效裝置（麥克風）插上去也會註冊 input 節點，
    按鍵那顆的編號會位移。寫死就會漏掉真正在送事件的節點，回報「沒收到任何
    按鍵事件」，而真正原因只是掃錯地方。
    """
    return sorted(glob.glob("/dev/input/event*"))


def _print_layout() -> None:
    """先印出「名稱 ↔ 節點」對照，以及 audio_io 實際會去讀哪一個。"""
    print("=== /proc/bus/input/devices 名稱對照 ===")
    try:
        with open("/proc/bus/input/devices", "r", encoding="utf-8", errors="replace") as f:
            name = ""
            for raw in f:
                line = raw.strip()
                if not line:
                    name = ""
                elif line.startswith("N: Name="):
                    name = line[len("N: Name="):].strip().strip('"')
                elif line.startswith("H: Handlers="):
                    nodes = [t for t in line[len("H: Handlers="):].split()
                             if t.startswith("event")]
                    if nodes:
                        print(f"  {', '.join(nodes):<12} {name}")
    except OSError as exc:
        print(f"  讀不到：{exc}")

    chosen = audio_io._resolve_key_device()
    usable = audio_io._key_device_usable()
    print("\n=== local_client 目前會讀哪一個 ===")
    print(f"  節點：{chosen}")
    print(f"  鍵碼：{audio_io._KEY_CODE}")
    print(f"  可用（存在且可讀）：{'✅' if usable else '❌ 會退回 Enter 觸發'}")
    if not usable:
        print("  ⚠️ 這代表按鍵觸發根本沒啟用，會卡在等 Enter。")
    # 沖掉緩衝：下面開節點失敗的訊息走 stderr（不緩衝），不 flush 會交錯在
    # 這段診斷輸出前面，真機上很容易誤判是哪一段出的錯。
    print(flush=True)


class _Stop(Exception):
    """SIGTERM 收到時用來走與 Ctrl-C 相同的收尾路徑（印統計）。"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="實體按鍵探測 / 診斷")
    parser.add_argument(
        "--seconds", type=float, default=0.0,
        help="監聽幾秒後自動結束並印統計；0（預設）＝直到 Ctrl-C。"
             "SSH 會斷的環境請務必指定，配合 nohup 讓結果留在裝置上。",
    )
    args = parser.parse_args(argv)

    _print_layout()

    fds = {}
    for path in _all_event_devices():
        try:
            fds[open(path, "rb", buffering=0)] = path
        except OSError as exc:
            print(f"  開不了 {path}：{exc}", file=sys.stderr)
    if not fds:
        print("沒有可讀的 input 裝置")
        return 1

    chosen = audio_io._resolve_key_device()
    if args.seconds > 0:
        deadline = time.monotonic() + args.seconds
        print(f"監聽 {len(fds)} 個節點，{args.seconds:.0f} 秒後自動結束。現在開始按。")
    else:
        deadline = None
        print(f"監聽 {len(fds)} 個節點。請依序按板子上的實體按鍵（Ctrl-C 結束）。")
    if audio_io._KEY_CODE == 116:
        print("觸發鍵是 power 鍵（短按）。⚠️ 先確認上面沒有出現 logind 關機警告，")
        print("   否則按下去是關機。也不要按住不放（PMIC 長按強制斷電擋不掉）。\n",
              flush=True)
    else:
        print("⚠️ 電源鍵短按可能關機，先確認 logind 設定。\n", flush=True)

    # SIGTERM 也要印統計：`timeout`／`kill` 結束時若直接死掉，就拿不到結論。
    def _on_term(_signum, _frame):
        raise _Stop()

    signal.signal(signal.SIGTERM, _on_term)

    # 以 (節點, 鍵碼) 計數：要能看出事件是從**哪個節點**來的，否則無法判斷
    # local_client 有沒有在讀對的那一個。
    seen: dict[tuple[str, int], int] = {}
    try:
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            ready, _, _ = select.select(list(fds), [], [], 1.0)
            for f in ready:
                data = f.read(_SIZE)
                if not data or len(data) < _SIZE:
                    continue
                _sec, _usec, etype, code, value = struct.unpack(_FMT, data)
                if etype != EV_KEY:
                    continue
                path = fds[f]
                name = _NAMES.get(code, f"code={code}")
                mark = "" if path == chosen else "  ← local_client 沒在讀這個節點！"
                print(f"  [{path}] {name}  {_VALUE.get(value, value)}{mark}", flush=True)
                if value == 1:
                    key = (path, code)
                    seen[key] = seen.get(key, 0) + 1
    except (KeyboardInterrupt, _Stop):
        pass
    finally:
        for f in fds:
            f.close()

    print("\n=== 按下統計 ===")
    if not seen:
        print("  沒有收到任何按鍵事件。")
        print("  ⚠️ 先排除**觀測管道**再懷疑硬體：若這支是透過 SSH 前景執行，")
        print("     連線一中斷，輸出就卡在斷掉的 TCP 裡、永遠傳不回來，")
        print("     看起來與「按了沒反應」一模一樣。")
        print("     可靠做法（結果留在裝置上，不依賴連線存活）：")
        print("       nohup ./.venv/bin/python -m edge.runtime.key_probe \\")
        print("         --seconds 60 > /tmp/keyprobe.log 2>&1 &")
        print("     按完鍵等 60 秒，再（重連後）看 /tmp/keyprobe.log。")
        print("  2026-07-30 實測：板上 KEY_HOME(102) 註冊了但不送事件，")
        print("  唯一可用的實體鍵是 KEY_POWER(116) 短按。你按的是那一顆嗎？")
        return 1
    for (path, code), n in sorted(seen.items()):
        print(f"  [{path}] {_NAMES.get(code, f'code={code}')}：{n} 次")

    # 116 不再排除：2026-07-30 實測它是板上唯一會送事件的實體鍵，
    # 而 logind 的 HandlePowerKey=ignore（見 provision_device.sh）讓短按不再關機。
    print()
    path, code = sorted(seen.items())[0][0]
    print(f"✅ 可用來當觸發鍵：{_NAMES.get(code, str(code))}（{path}）")
    if path == chosen and code == audio_io._KEY_CODE:
        print("   local_client 讀的節點與鍵碼都正確，按鍵觸發應該就能動。")
    else:
        print("   ⚠️ 與 local_client 目前的設定不符——這就是「按了沒反應」的原因。")
        print("   啟動 local_client 時加上：")
        if path != chosen:
            print(f"     TALKYBUDDY_EDGE_KEY_DEVICE={path} \\")
        if code != audio_io._KEY_CODE:
            print(f"     TALKYBUDDY_EDGE_KEY_CODE={code} \\")
    return 0


if __name__ == "__main__":
    sys.exit(main())
