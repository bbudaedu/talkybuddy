# -*- coding: utf-8 -*-
"""`LLM_THREADS` 的預設值必須等於真機實測的最佳值。

`edge/EDGE_TURN_LOOP_VALIDATION.md` §A 的 llama-bench 掃描（真機、`-t 1,2,4,6,8 -r 3`）：

| threads | pp128 | tg128 |
|---|---|---|
| 4 | 26.49 | 10.50 |
| **6** | **39.06** | **12.35** |

ELOOP-03 早就量完並選定 6，但**只用 `TALKYBUDDY_LLM_THREADS=6` env override 套在
裝置上，程式碼預設仍停在佔位的 4**。env override 活在環境裡，重開機或啟動腳本
沒帶就悄悄退回 4——而 `edge/runtime/run_edge.sh` 確實沒有設定它。

2026-07-29 實測裝置正是跑 `--threads 4`，prefill 因此慢 1.47×。這條測試把「已經
量過的結論」釘在程式碼裡，不讓它再退回佔位值。
"""

import importlib

from server import config as config_mod

# edge/EDGE_TURN_LOOP_VALIDATION.md §A 掃描出的最佳值
MEASURED_OPTIMUM = 6


def test_default_thread_count_is_the_measured_optimum(monkeypatch):
    monkeypatch.delenv("TALKYBUDDY_LLM_THREADS", raising=False)
    cfg = importlib.reload(config_mod)
    try:
        assert cfg.LLM_THREADS == MEASURED_OPTIMUM
    finally:
        importlib.reload(config_mod)


def test_env_override_still_wins(monkeypatch):
    """現場若需要臨時改（例如換一顆 CPU 不同的板子），env 仍要蓋得過預設。"""
    monkeypatch.setenv("TALKYBUDDY_LLM_THREADS", "2")
    cfg = importlib.reload(config_mod)
    try:
        assert cfg.LLM_THREADS == 2
    finally:
        monkeypatch.delenv("TALKYBUDDY_LLM_THREADS", raising=False)
        importlib.reload(config_mod)
