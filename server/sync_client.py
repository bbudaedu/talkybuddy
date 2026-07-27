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

from server import guardrails, store

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
