# -*- coding: utf-8 -*-
"""edge/runtime/dump_recent_turns.py 單元測試（斷網彩排量測的客觀證據來源）。

只測純函式 format_turns_table() 的各種輸入形狀，以及 main() 走 tmp_db fixture
的整合路徑；不依賴真裝置或真 DB 路徑（tmp_db 為 autouse，直接用即可）。
"""

from __future__ import annotations

from edge.runtime.dump_recent_turns import format_turns_table, main


def test_format_turns_table_normal_input():
    rows = [
        {
            "seq": 2,
            "ts": "2026-07-25T20:10:05+08:00",
            "network_mode": "edge",
            "latency_ms": {"llm": 1780, "tts_first": 950, "round_total": 2960},
            "synced": False,
        },
        {
            "seq": 1,
            "ts": "2026-07-25T20:09:00+08:00",
            "network_mode": "cloud",
            "latency_ms": {"llm": 900, "tts_first": 600, "round_total": 1600},
            "synced": True,
        },
    ]
    out = format_turns_table(rows)
    assert "network_mode" in out
    assert "edge" in out
    assert "cloud" in out
    assert "1780" in out
    assert "950" in out
    assert "2960" in out


def test_format_turns_table_missing_latency_ms_key_fills_dash():
    rows = [
        {
            "seq": 1,
            "ts": "2026-07-25T20:09:00+08:00",
            "network_mode": "edge",
            "latency_ms": {"llm": 1234},  # tts_first / round_total 缺鍵
            "synced": False,
        }
    ]
    out = format_turns_table(rows)
    assert "1234" in out
    assert "-" in out


def test_format_turns_table_latency_ms_not_dict_does_not_raise():
    rows = [
        {
            "seq": 1,
            "ts": "2026-07-25T20:09:00+08:00",
            "network_mode": "edge",
            "latency_ms": None,
            "synced": False,
        },
        {
            "seq": 2,
            "ts": "2026-07-25T20:10:00+08:00",
            "network_mode": "edge",
            "latency_ms": "not-a-dict",
            "synced": False,
        },
    ]
    out = format_turns_table(rows)
    assert out  # 未拋例外，且有輸出
    assert "-" in out


def test_format_turns_table_empty_input_returns_header_and_placeholder():
    out = format_turns_table([])
    assert out
    assert "network_mode" in out
    assert "（無互動紀錄）" in out


def test_main_prints_recent_turns_from_db(capsys):
    from server import store

    store.add_interaction(
        {
            "device_id": "DEV-1",
            "student_id": "STUDENT-1",
            "ts": "2026-07-25T20:09:00+08:00",
            "network_mode": "cloud",
            "student_text": "你好",
            "asr_confidence": 0.9,
            "ai_response_text": "你好呀",
            "scores": {},
            "latency_ms": {"llm": 900, "tts_first": 600, "round_total": 1600},
            "synced": True,
        }
    )
    store.add_interaction(
        {
            "device_id": "DEV-1",
            "student_id": "STUDENT-1",
            "ts": "2026-07-25T20:10:05+08:00",
            "network_mode": "edge",
            "student_text": "再見",
            "asr_confidence": 0.8,
            "ai_response_text": "再見囉",
            "scores": {},
            "latency_ms": {"llm": 1780, "tts_first": 950, "round_total": 2960},
            "synced": False,
        }
    )

    exit_code = main([])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "cloud" in captured.out
    assert "edge" in captured.out
