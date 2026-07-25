# -*- coding: utf-8 -*-
"""dump_recent_turns.py — 斷網彩排量測的客觀證據來源（NETCUT-03）。

斷網彩排時（`edge/NETWORK_CUT_REHEARSAL.md`）在裝置上一行印出最近幾輪對話
的時間戳、網路模式與各階段延遲，供 §5 結果表的 M1/M2 貼回證據——把恢復
時間從主觀碼錶口述變成裝置上可複製貼上的客觀紀錄。

用法：`python -m edge.runtime.dump_recent_turns [--limit N]`（預設 5）。

比照 edge/runtime/measure_peak_rss.py 的結構：純函式（format_turns_table）
無 I/O 副作用、可單元測試；main() 才是唯一觸及 DB 的進入點，且以函式內
lazy import 載入 server.store，讓 `import edge.runtime.dump_recent_turns`
本身不觸發任何 DB 初始化。
"""

from __future__ import annotations

_COLUMNS = ("#", "ts", "network_mode", "llm_ms", "tts_first_ms", "round_total_ms", "synced")


def _latency_value(latency_ms: object, key: str) -> str:
    """從 latency_ms 取單一鍵值，缺鍵或型別不符一律回傳 '-'（不拋例外）。

    真機資料可能不完整（latency_ms 缺鍵、甚至整個不是 dict）；彩排現場
    工具絕不能因單筆資料不完整而炸掉，寧可印 '-' 也不要中斷演練。
    """
    if not isinstance(latency_ms, dict):
        return "-"
    value = latency_ms.get(key)
    if value is None:
        return "-"
    return str(value)


def format_turns_table(rows: list[dict]) -> str:
    """把 store.list_interactions() 風格的 dict list 轉成單一字串表格。

    純函式：不觸網、不讀檔、不寫檔。欄位順序固定為 #/ts/network_mode/
    llm_ms/tts_first_ms/round_total_ms/synced，任一缺值填 '-'。
    """
    header = " | ".join(_COLUMNS)
    lines = [header, "-" * len(header)]

    if not rows:
        lines.append("（無互動紀錄）")
        return "\n".join(lines)

    for row in rows:
        latency_ms = row.get("latency_ms")
        seq = row.get("seq", "-")
        ts = row.get("ts", "-")
        network_mode = row.get("network_mode", "-")
        synced = row.get("synced", "-")
        line = " | ".join(
            str(v)
            for v in (
                seq,
                ts,
                network_mode,
                _latency_value(latency_ms, "llm"),
                _latency_value(latency_ms, "tts_first"),
                _latency_value(latency_ms, "round_total"),
                synced,
            )
        )
        lines.append(line)

    return "\n".join(lines)


def _parse_limit(argv: list[str]) -> int:
    """從 argv 取 --limit N，缺省或非數字時退回預設 5，不拋例外。"""
    default = 5
    for i, arg in enumerate(argv):
        if arg == "--limit" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except ValueError:
                return default
        if arg.startswith("--limit="):
            try:
                return int(arg.split("=", 1)[1])
            except ValueError:
                return default
    return default


def main(argv: list[str] | None = None) -> int:
    """裝置上一行執行取數的進入點：印最近 N 筆互動紀錄的延遲表。"""
    if argv is None:
        import sys

        argv = sys.argv[1:]
    limit = _parse_limit(argv)

    from server import store  # lazy import：避免 import 本模組時觸發 DB 初始化

    try:
        rows = store.list_interactions(limit=limit)
    except Exception:
        # 空 DB（尚未 init_db()，資料表不存在）或其他讀取失敗一律視為無紀錄，
        # 彩排現場不能因為這支小工具的例外而中斷——寧可印「無互動紀錄」。
        rows = []
    print(format_turns_table(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
