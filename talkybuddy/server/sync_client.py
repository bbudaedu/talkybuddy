# -*- coding: utf-8 -*-
"""玩偶端上行同步：把本地未同步互動批次送上雲端。

http_post 以參數注入（(url, json, headers) -> obj），方便測試不打真網路；
正式端可傳 urllib/requests 包裝。
"""
from __future__ import annotations

from server import store


def push_pending(base_url: str, token: str, http_post) -> dict:
    """讀本地未同步互動 → POST /api/sync → 成功則 mark_all_synced。

    回傳雲端回應 {"accepted", "skipped"}；無待同步時回 0/0（不打網路）。
    """
    pending = [it for it in store.list_interactions(limit=100000) if not it.get("synced")]
    if not pending:
        return {"accepted": 0, "skipped": 0}
    payload = {"interactions": pending}
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_post(f"{base_url}/api/sync", payload, headers)
    if resp.get("accepted", 0) or resp.get("skipped", 0):
        store.mark_all_synced()
    return resp
