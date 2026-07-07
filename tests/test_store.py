# -*- coding: utf-8 -*-
"""test_store.py — SQLite 儲存層純單元測試（DB_PATH 由 conftest.tmp_db 導向 tmp）。

涵蓋 CONTRACTS.md「store.py」八個契約函式：
init_db / add_interaction / list_interactions / pending_count /
mark_all_synced / add_diagnosis / list_diagnoses / seed_demo
"""

from __future__ import annotations

from server import store


def _sample_interaction(text: str = "hi", synced: bool = False) -> dict:
    return {
        "network_mode": "edge",
        "student_text": text,
        "asr_confidence": 0.9,
        "ai_response_text": "hello",
        "scores": {"fluency": 50, "vocabulary": 50, "grammar": 50},
        "latency_ms": {"asr": 100, "llm": 200, "tts_first": 150, "round_total": 500},
        "synced": synced,
    }


def test_add_interaction_returns_incrementing_seq():
    """add_interaction 回傳的 seq 應自動遞增（max+1）。"""
    seq1 = store.add_interaction(_sample_interaction("first"))
    seq2 = store.add_interaction(_sample_interaction("second"))
    assert seq1 == 1
    assert seq2 == seq1 + 1


def test_list_interactions_order_new_to_old_and_fields():
    """list_interactions 回傳新→舊，且欄位含契約定義的 seq/synced/device_id/student_id。"""
    store.add_interaction(_sample_interaction("first"))
    store.add_interaction(_sample_interaction("second"))

    rows = store.list_interactions(limit=50)
    assert [r["student_text"] for r in rows] == ["second", "first"]
    assert rows[0]["seq"] == 2
    assert rows[0]["synced"] is False
    assert rows[0]["device_id"] == "GENIO-520-X992"
    assert rows[0]["student_id"] == "STUDENT-AMING-004"
    assert "ts" in rows[0]


def test_list_interactions_respects_limit():
    """limit 參數應限制回傳筆數。"""
    for i in range(5):
        store.add_interaction(_sample_interaction(f"msg{i}"))
    rows = store.list_interactions(limit=2)
    assert len(rows) == 2


def test_pending_count_and_mark_all_synced():
    """pending_count 只算 synced=0；mark_all_synced 標記並回傳剛同步的紀錄。"""
    store.add_interaction(_sample_interaction("a", synced=False))
    store.add_interaction(_sample_interaction("b", synced=False))
    store.add_interaction(_sample_interaction("c", synced=True))

    assert store.pending_count() == 2

    just_synced = store.mark_all_synced()
    assert len(just_synced) == 2
    assert all(r["synced"] is True for r in just_synced)
    assert {r["student_text"] for r in just_synced} == {"a", "b"}

    # 全部標記後 pending 應歸零
    assert store.pending_count() == 0
    # 再次呼叫應回空清單（已無未同步紀錄）
    assert store.mark_all_synced() == []


def test_add_and_list_diagnoses_sorted_by_date_ascending():
    """add_diagnosis / list_diagnoses：依 date 升冪排序。"""
    store.add_diagnosis(_sample_diagnosis("2026-07-02"))
    store.add_diagnosis(_sample_diagnosis("2026-07-01"))
    store.add_diagnosis(_sample_diagnosis("2026-07-03"))

    diagnoses = store.list_diagnoses()
    assert [d["date"] for d in diagnoses] == ["2026-07-01", "2026-07-02", "2026-07-03"]


def test_add_diagnosis_same_date_overwrites():
    """同日期重複 add_diagnosis 應覆寫（INSERT OR REPLACE），不重複兩筆。"""
    store.add_diagnosis(_sample_diagnosis("2026-07-01", emotional="第一次"))
    store.add_diagnosis(_sample_diagnosis("2026-07-01", emotional="第二次覆寫"))

    diagnoses = store.list_diagnoses()
    assert len(diagnoses) == 1
    assert diagnoses[0]["emotional_status"] == "第二次覆寫"


def _sample_diagnosis(date: str, emotional: str = "穩定") -> dict:
    return {
        "date": date,
        "scores": {"pronunciation": 60, "fluency": 55, "vocabulary": 60, "grammar": 55},
        "strengths": ["願意開口"],
        "weaknesses": ["冠詞常漏"],
        "emotional_status": emotional,
        "instructions": {"classroom": "x", "device": "y", "peer": "z"},
    }


def test_seed_demo_seeds_when_empty():
    """seed_demo：兩表皆空時灌入 14 天診斷 + 20 筆互動。"""
    assert store.pending_count() == 0
    assert store.list_diagnoses() == []

    store.seed_demo()

    diagnoses = store.list_diagnoses()
    interactions = store.list_interactions(limit=100)
    assert len(diagnoses) == 14
    assert len(interactions) == 20


def test_seed_demo_is_noop_when_not_empty():
    """seed_demo：只要任一表非空就不重複灌資料（避免 demo 重複疊加）。"""
    store.add_interaction(_sample_interaction("existing"))
    store.seed_demo()

    interactions = store.list_interactions(limit=100)
    # 只有原本手動加入的那一筆，seed_demo 應該是 no-op
    assert len(interactions) == 1
    assert interactions[0]["student_text"] == "existing"
    assert store.list_diagnoses() == []
