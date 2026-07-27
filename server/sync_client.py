# -*- coding: utf-8 -*-
"""sync_client.py — 玩偶端上行同步：裝置端資料出境的唯一 chokepoint。

本檔與 ``server/agents/privacy.py`` 是同一個原則、不同關注點：privacy.py
談的是雲端 agent 產出（診斷／派作業）回饋時的 prompt 最小化，本檔談的是
**裝置上傳到 /api/sync 的 payload schema 最小化**——兩邊都堅持「白名單
挑欄位，不是黑名單遮欄位」，理由見 privacy.py 的 docstring：黑名單的失敗
模式是「新增一個欄位、沒人記得同步遮罩清單，資料就靜默上雲」；白名單的
失敗模式相反且可回復（忘記更新只會讓該送的欄位沒送）。隱私的預設值必須
是「不送」。

``push_pending()`` 的處理順序（不可調換，見其 docstring）：
consent 閘門 → 白名單投影＋去識別化 → 全數處理才標記已同步。

http_post 以參數注入（(url, json, headers) -> obj），方便測試不打真網路；
正式端可傳 urllib/requests 包裝。
"""
from __future__ import annotations

import logging

from server import guardrails, store

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 上傳白名單（D-04）：只有列在這裡的欄位會離開裝置。預設拒絕——
# 未列名者（例如未來新增的音檔路徑欄位）一律不送，這是「音檔絕不出裝置」
# 唯一可稽核的寫法。
# ---------------------------------------------------------------------------

# 身分／去重鍵欄位：原樣帶出，不經 deidentify——student_id 是 /api/sync
# 綁定學生的依據；device_id + client_ts 是 /api/sync 的去重鍵；
# network_mode／source 是「這一輪是離線產生的」的來源證明。
UPLOAD_ID_FIELDS = ("student_id", "device_id", "client_ts", "network_mode", "source")

# 數值分數欄位：原樣帶出，不經 deidentify——deidentify 會把 3 位以上連續
# 數字換成 [數字]，套在分數上會毀掉資料；scores 是 dict，字串轉換也無意義。
UPLOAD_SCORE_FIELDS = ("scores", "asr_confidence", "asr_conf")

# 自由文字欄位：逐欄呼叫 guardrails.deidentify()（D-01：只在上傳瞬間套用，
# 本地 SQLite 保留原文）。
UPLOAD_TEXT_FIELDS = ("student_text", "ai_response_text", "asr_text", "reply_text")

# 供測試／稽核斷言「輸出鍵集合是它的子集」。
UPLOAD_FIELDS = (
    frozenset(UPLOAD_ID_FIELDS) | frozenset(UPLOAD_SCORE_FIELDS) | frozenset(UPLOAD_TEXT_FIELDS)
)

# 明確不上傳（列舉供稽核）：latency_ms（裝置遙測、無教學價值）、seq／synced
# （本地狀態，接收端無意義）、學生姓名（見 11-CONTEXT.md D-05：姓名存在
# server 端的 student_profile，裝置端不必也不該傳它）、以及任何未列名欄位
# ——未列名者一律不送是預設行為，這是本 chokepoint 的核心承諾。


def project_for_upload(item: dict) -> dict:
    """把一筆本地互動投影成上傳 payload：白名單挑欄位＋文字去識別化（D-01+D-04）。

    只讀允許鍵組出輸出，不是「複製整包再刪黑名單」——這樣任何未來新增的
    欄位（例如音檔路徑）預設就不會出現在輸出裡。純函式、不修改傳入的
    dict；垃圾輸入（非 dict）回空 dict，比照 privacy.safe_diagnosis() 的
    不拋例外契約。
    """
    if not isinstance(item, dict):
        return {}
    out: dict = {}
    for key in UPLOAD_ID_FIELDS:
        if key == "client_ts":
            continue
        val = item.get(key)
        if val is not None:
            out[key] = val
    # client_ts 特例：本地列存的是 ts，/api/sync 的去重鍵讀 client_ts；
    # 兩者不接則去重永遠落空，補傳會產生重複列（見 11-01-PLAN.md）。
    client_ts = item.get("client_ts")
    if client_ts is None:
        client_ts = item.get("ts")
    if client_ts is not None:
        out["client_ts"] = client_ts
    for key in UPLOAD_SCORE_FIELDS:
        val = item.get(key)
        if val is not None:
            out[key] = val
    for key in UPLOAD_TEXT_FIELDS:
        if key in item:
            out[key] = guardrails.deidentify(str(item[key]))
    return out


def push_pending(base_url: str, token: str, http_post) -> dict:
    """讀本地未同步互動 → consent 閘門 → 白名單投影＋去識別化 → POST /api/sync
    → 全數處理才標記已同步。

    處理順序（不可調換，D-02/D-04 的具體實作）：
    1. 無 pending 時直接回 {"accepted": 0, "skipped": 0}，不打網路（既有行為）。
    2. consent 閘門：``guardrails.consent_granted()`` 為 False 時，在組 payload
       與呼叫 http_post 之前就立即返回 {"accepted": 0, "skipped": 0,
       "consent_required": True}——不打任何網路、不標記任何紀錄，全數留在
       pending 佇列等日後補傳（D-02）。
    3. 白名單投影＋去識別化：project_for_upload() 逐筆組 payload（D-01+D-04）。
    4. 全數處理才標記：/api/sync 只回兩個彙總數字（accepted + skipped），
       沒有逐筆明細，發送端無從得知是哪幾筆被拒。只有當 accepted + skipped
       == len(pending) 時才呼叫 store.mark_synced(seqs)；否則一筆都不標記，
       全數留著等下次補傳。由於每筆都帶 client_ts，/api/sync 的
       (student_id, device_id, client_ts) 去重會把重送的已收紀錄計入
       skipped，因此重送冪等、不會產生重複列。

    誠實限制：``guardrails.deidentify()`` 不遮中文人名（見其 docstring 自承
    需語意層），所以上雲文字仍可能含中文姓名——「已呼叫 deidentify」不等於
    「已完成去識別化」。
    """
    pending = [it for it in store.list_interactions(limit=100000) if not it.get("synced")]
    if not pending:
        return {"accepted": 0, "skipped": 0}
    if not guardrails.consent_granted():
        return {"accepted": 0, "skipped": 0, "consent_required": True}
    seqs = [it["seq"] for it in pending]
    payload = {"interactions": [project_for_upload(it) for it in pending]}
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_post(f"{base_url}/api/sync", payload, headers)
    if resp.get("accepted", 0) + resp.get("skipped", 0) == len(pending):
        store.mark_synced(seqs)
    return resp


def opportunistic_sync(*, base_url: str | None = None, token: str | None = None, http_post=None) -> dict:
    """D-03 兩層機會式觸發（network_mode edge→cloud 轉換瞬間 + 回合尾兜底）
    共用的唯一入口。呼叫端（``app.py::api_network_mode`` 與
    ``pipeline.VoicePipeline._opportunistic_sync``）一律走這裡，不得再各自
    直呼 ``store`` 的標記 helper。

    處理順序（不可調換）：
    1. ``store.pending_count()`` 為 0 → 直接回 ``{"synced": 0}``，不做任何事
       （沒有 pending 就沒有機會式同步的意義）。
    2. **consent 閘門**：``guardrails.consent_granted()`` 為 False → 回
       ``{"synced": 0, "consent_required": True}``，不標記、不打任何網路。
       紀錄留在 pending 佇列等日後補傳（D-02）。此閘門必須在任何 transport
       分派之前，兩條路都不能繞過。
    3. 分派：依呼叫端是否提供完整 transport 三件組（``base_url`` +
       ``token`` + ``http_post``）分成兩條路（見下方「同程序拓樸」說明）。

    **同程序拓樸下「上傳」的誠實語意：** 決賽拓樸是單一 Genio 520 process
    ——學生端 UI、教師儀表板、``/api/*`` 與 ``pipeline`` 共用同一個
    SQLite，遠端路徑打的 ``/api/sync`` 端點其實是**同一個程序裡的自己**。
    若在這個拓樸下仍走遠端路徑，每筆 pending 會在同一個 DB 被重新 INSERT
    一次，因此：

    - **遠端路徑**（三者皆提供）：確有跨程序邊界要跨（Phase 6 雲端 VM
      部署），委派模組內的 HTTP 送出函式走完整的白名單投影＋去識別化＋
      HTTP，其回傳的 ``accepted + skipped`` 折算成本函式的 ``synced``。
    - **本機路徑**（預設，決賽用）：沒有跨程序邊界要跨，因此不做白名單
      投影、不打 HTTP，只把目前 pending 的 seq 清單以 ``store.mark_synced()``
      直接升級為已同步——投影是為了保護「離開裝置」的資料，同程序內沒有
      東西離開裝置。

    整個函式包在 ``try/except Exception`` 內，任何例外都先 ``_log.exception``
    記錄後回 ``{"synced": 0, "error": True}``——絕不可用無聲吞掉例外的寫法
    （空的 except 區塊）帶過；本專案吃過「背景工作悄悄停掉、畫面照跑、沒人發現」的虧（見
    ``server/pipeline.py`` 對 ``_refresh_directive`` 的同一則教訓）。

    本函式不建立任何 timer、``sleep`` 迴圈或排程；它是被動被呼叫的，兩層
    觸發（轉換瞬間 / 回合尾）由呼叫端各自決定何時呼叫。
    """
    try:
        if store.pending_count() == 0:
            return {"synced": 0}
        if not guardrails.consent_granted():
            return {"synced": 0, "consent_required": True}
        if base_url is not None and token is not None and http_post is not None:
            resp = push_pending(base_url, token, http_post)
            synced = resp.get("accepted", 0) + resp.get("skipped", 0)
            return {"synced": synced}
        pending = [it for it in store.list_interactions(limit=100000) if not it.get("synced")]
        seqs = [it["seq"] for it in pending]
        changed = store.mark_synced(seqs)
        return {"synced": changed}
    except Exception:
        _log.exception("opportunistic_sync 失敗，本次不同步，pending 留待下次補傳")
        return {"synced": 0, "error": True}
