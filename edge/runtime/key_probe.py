"""實體按鍵探測：讀 `/dev/input/event*`，印出按了哪一顆。

**為什麼需要這支**：裝置沒有螢幕也沒有鍵盤，而 `local_client.py` 的錄音觸發是
`audio_io.wait_for_trigger()` → `input("按 Enter…")`，需要有人 SSH 進去按 Enter。
那不是可以上台的觸發方式，而原生喚醒詞（`wake_listener.py`）真人辨識率已判 NO-GO。

`/proc/bus/input/devices` 顯示板上有 `mtk-pmic-keys`（event1），
`B: KEY=10004000000000` 解碼為 bit 102 與 116 = **KEY_HOME 與 KEY_POWER**。
若 KEY_HOME 真的按得到，`wait_for_trigger()` 就能改成讀它——
**「按一下玩偶就開始講話」**，不需要焊任何東西。

⚠️ **不要按 KEY_POWER**，可能觸發關機。本探針會把它標成紅字提醒。

用法（裝置上，Ctrl-C 結束）：

    ssh -t root@192.168.31.78 'cd /root/talkybuddy && \
      ./.venv/bin/python edge/runtime/key_probe.py'

不需要 `evdev` 模組——input_event 是固定的 24-byte 結構，標準函式庫即可。
"""
import select
import struct
import sys
import time

# struct input_event（64-bit）：__kernel_ulong_t sec, usec; __u16 type, code; __s32 value
_FMT = "llHHi"
_SIZE = struct.calcsize(_FMT)

EV_KEY = 0x01
_VALUE = {0: "放開", 1: "按下", 2: "長按重複"}

# 只列我們關心的；其餘直接印碼號
_NAMES = {102: "KEY_HOME", 116: "KEY_POWER ⚠️不要用", 114: "KEY_VOLUMEDOWN",
          115: "KEY_VOLUMEUP", 158: "KEY_BACK"}

DEVICES = ["/dev/input/event0", "/dev/input/event1"]


def main() -> int:
    fds = {}
    for path in DEVICES:
        try:
            fds[open(path, "rb", buffering=0)] = path
        except OSError as exc:
            print(f"  開不了 {path}：{exc}", file=sys.stderr)
    if not fds:
        print("沒有可讀的 input 裝置")
        return 1

    print("請依序按板子上的實體按鍵（Ctrl-C 結束）。")
    print("⚠️ 先按**非電源鍵**那顆；電源鍵可能關機。\n", flush=True)

    seen: dict[int, int] = {}
    try:
        while True:
            ready, _, _ = select.select(list(fds), [], [], 1.0)
            for f in ready:
                data = f.read(_SIZE)
                if not data or len(data) < _SIZE:
                    continue
                _sec, _usec, etype, code, value = struct.unpack(_FMT, data)
                if etype != EV_KEY:
                    continue
                name = _NAMES.get(code, f"code={code}")
                print(f"  [{fds[f]}] {name}  {_VALUE.get(value, value)}", flush=True)
                if value == 1:
                    seen[code] = seen.get(code, 0) + 1
    except KeyboardInterrupt:
        pass
    finally:
        for f in fds:
            f.close()

    print("\n=== 按下統計 ===")
    if not seen:
        print("  沒有收到任何按鍵事件 —— 板子可能沒有實體按鍵，或按鍵未接到 PMIC。")
        return 1
    for code, n in sorted(seen.items()):
        print(f"  {_NAMES.get(code, f'code={code}')}：{n} 次")
    usable = [c for c in seen if c != 116]
    print()
    if usable:
        print(f"✅ 可用來當觸發鍵：{'、'.join(_NAMES.get(c, str(c)) for c in usable)}")
        print("   下一步：把 audio_io.wait_for_trigger() 改成讀這顆鍵。")
    else:
        print("⚠️ 只有電源鍵有反應 —— 不建議拿來當觸發鍵，改走 GPIO 外接按鈕。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
