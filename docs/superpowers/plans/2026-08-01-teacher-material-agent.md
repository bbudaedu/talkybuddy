# 老師教材提煉 agent（子專案 F）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `server/agents/material.py`，把老師貼上的教材文字提煉成跟 `scaffold.VOCAB` 同 schema 的詞條並原地合併，讓現有 games/homework/srs/profile 零改動自動支援老師上傳的教材。

**Architecture:** 第四個 agent，降級鏈同現有三個 agent（`agent_backends.resolve("material")`：AgentCore Harness → Bedrock Converse → 規則式）。`scaffold.register_material_vocab` 是單一驗證/合併入口，通過驗證的詞條原地寫入 `scaffold.VOCAB` 這個共享 dict 物件。長期記憶與教師回饋沿用既有 `store.py`／`profile.py`／`srs.py`／`report.py`／`/teacher` 儀表板，不新增機制。

**Tech Stack:** Python 3.12、FastAPI、SQLite（`server/store.py`）、pytest、既有 `server.bedrock_converse`／`server.agentcore`／`server.agent_backends`。

## Global Constraints

- 所有面向使用者／教師的文字一律用繁體中文（台灣用語）；程式碼識別字、註解沿用既有英文/中文混排風格。
- 零新依賴：只用 Python 標準庫與專案既有模組（`agent_backends`／`bedrock_converse`／`agentcore`／`guardrails`／`scaffold`／`store`）。
- `cat` 只能是既有 6 類之一：`food`／`school`／`animal`／`family`／`action`／`color`；不開放新分類。
- 任何 agent 函式任何情況都不得往外拋例外——包含規則式路徑自己爆掉；規則式路徑永遠能產出合法結果。
- `allow_cloud=False` 或 `guardrails.consent_granted()` 為否時，完全不呼叫 `resolve_config`／`converse_text`／`agentcore.invoke`，連解析都不做。
- 教材文字上傳僅純文字，不做檔案/PDF/OCR；本次不新增 `/teacher` UI 區塊，只新增 API 端點。
- 教材是全域共用詞庫擴充，不分學生／班級隔離。
- `scaffold.VOCAB` 的合併一律用 `VOCAB[zh] = {...}` **原地寫入同一個 dict 物件**，不得重新賦值整個 `VOCAB`（否則既有模組的 `from server.scaffold import VOCAB` 參照會失效）。

---

## Task 1: `scaffold.register_material_vocab`（驗證與合併入口）

**Files:**
- Modify: `server/scaffold.py`（在 `_A_EXCEPTIONS`／`_article_for` 定義之後，約第 384 行後新增）
- Test: `tests/test_scaffold_register_material.py`

**Interfaces:**
- Consumes: `scaffold.VOCAB`（既有全域 dict）、`scaffold._article_for(word: str) -> str`（既有函式，line 376）
- Produces: `scaffold.register_material_vocab(entries: list[dict]) -> tuple[list[dict], int]`——回傳 `(accepted_entries, rejected_count)`；`accepted_entries` 是 `[{"zh": str, "en": str, "cat": str, "np": str, "sent": str}, ...]`，且已原地合併進 `scaffold.VOCAB`。

因為要驗證合併會 mutate 全域 `VOCAB`，本檔測試需要自己的 autouse fixture 在每個測試後還原快照，避免污染其他測試檔案（`tmp_db` 只處理 SQLite，不管這個全域 dict）。

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_scaffold_register_material.py`：

```python
# -*- coding: utf-8 -*-
"""test_scaffold_register_material.py — 教材詞條驗證與合併入口。"""

from __future__ import annotations

import pytest

from server import scaffold


@pytest.fixture(autouse=True)
def _restore_vocab():
    """register_material_vocab 會原地 mutate 全域 VOCAB，測試後還原快照，
    避免污染其他測試檔案（tmp_db 只處理 SQLite，管不到這個全域 dict）。"""
    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    yield
    scaffold.VOCAB.clear()
    scaffold.VOCAB.update(snapshot)


def test_valid_entry_is_merged_in_place():
    """合法詞條原地寫入 VOCAB（同一個 dict 物件，不是重新賦值）。"""
    vocab_ref_before = scaffold.VOCAB
    entries = [{"en": "koala", "zh": "無尾熊", "cat": "animal",
                "np": "a koala", "sent": "I see a koala."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert scaffold.VOCAB is vocab_ref_before, "應原地 mutate，不得重新賦值 VOCAB"
    assert rejected == 0
    assert len(accepted) == 1
    assert accepted[0]["zh"] == "無尾熊"
    assert "無尾熊" in scaffold.VOCAB
    assert scaffold.VOCAB["無尾熊"] == {
        "en": "koala", "cat": "animal", "np": "a koala", "sent": "I see a koala."
    }


def test_duplicate_english_word_is_rejected():
    """en 與既有 VOCAB 重複（不分大小寫）→ 拒絕，不覆蓋既有詞條。"""
    original = dict(scaffold.VOCAB["獅子"])
    entries = [{"en": "Lion", "zh": "新獅子詞", "cat": "animal",
                "np": "a lion", "sent": "I want a lion."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 1
    assert "新獅子詞" not in scaffold.VOCAB
    assert scaffold.VOCAB["獅子"] == original


def test_duplicate_sentence_is_rejected():
    """sent 與既有 VOCAB 重複 → 拒絕（homework 靠 sent 去重出題）。"""
    entries = [{"en": "koala", "zh": "無尾熊", "cat": "animal",
                "np": "a lion", "sent": "I see a lion."}]  # sent 撞既有「獅子」

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 1
    assert "無尾熊" not in scaffold.VOCAB


def test_invalid_category_is_rejected():
    """cat 不在既有 6 類 → 拒絕（games.py 的分類假設不能被打破）。"""
    entries = [{"en": "robot", "zh": "機器人", "cat": "toy",
                "np": "a robot", "sent": "I see a robot."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 1
    assert "機器人" not in scaffold.VOCAB


def test_wrong_article_is_rejected():
    """np 冠詞不符合 a/an 規則 → 拒絕（koala 開頭是子音，應該用 a 不是 an）。"""
    entries = [{"en": "koala", "zh": "無尾熊", "cat": "animal",
                "np": "an koala", "sent": "I see an koala."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 1
    assert "無尾熊" not in scaffold.VOCAB


def test_non_a_an_article_is_not_strictly_checked():
    """np 開頭是 some/my/the 等非 a/an 時不做嚴格檢查（沒有明確規則可比對）。"""
    entries = [{"en": "juice", "zh": "果汁", "cat": "food",
                "np": "some juice", "sent": "I want to drink some juice."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert rejected == 0
    assert len(accepted) == 1
    assert "果汁" in scaffold.VOCAB


def test_one_bad_entry_does_not_block_the_rest_of_the_batch():
    """一批詞條裡有不合法的，只丟該條，其他合法詞條照常合併。"""
    entries = [
        {"en": "koala", "zh": "無尾熊", "cat": "animal",
         "np": "a koala", "sent": "I see a koala."},
        {"en": "robot", "zh": "機器人", "cat": "toy",  # 不合法分類
         "np": "a robot", "sent": "I see a robot."},
    ]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert len(accepted) == 1
    assert rejected == 1
    assert "無尾熊" in scaffold.VOCAB
    assert "機器人" not in scaffold.VOCAB


def test_missing_or_empty_field_is_rejected():
    """欄位缺漏或空字串 → 拒絕，不拋例外。"""
    entries = [
        {"en": "", "zh": "無尾熊", "cat": "animal", "np": "a koala", "sent": "I see a koala."},
        {"en": "koala", "cat": "animal", "np": "a koala", "sent": "I see a koala."},  # 缺 zh
    ]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert accepted == []
    assert rejected == 2


def test_non_dict_entry_is_rejected_without_raising():
    """輸入不是 dict（None、字串、list…）不拋例外，直接算拒絕。"""
    entries = [None, "not a dict", 42, {"en": "koala", "zh": "無尾熊",
               "cat": "animal", "np": "a koala", "sent": "I see a koala."}]

    accepted, rejected = scaffold.register_material_vocab(entries)

    assert len(accepted) == 1
    assert rejected == 3
```

- [ ] **Step 2: 執行測試確認全部失敗**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_scaffold_register_material.py -v`
Expected: FAIL，錯誤訊息為 `AttributeError: module 'server.scaffold' has no attribute 'register_material_vocab'`

- [ ] **Step 3: 在 `server/scaffold.py` 新增驗證與合併函式**

在 `_article_for` 定義（約第 376-383 行）之後插入：

```python
# ---------------------------------------------------------------------------
# 教材提煉 agent 用：驗證並合併老師上傳教材提煉出的詞條（子專案 F）
# ---------------------------------------------------------------------------

_MATERIAL_CATS = {"food", "school", "animal", "family", "action", "color"}
_MATERIAL_ENTRY_KEYS = ("en", "zh", "cat", "np", "sent")


def _article_is_consistent(np: str) -> bool:
    """只驗證 a/an 這條有明確規則的冠詞；其餘開頭（some/my/the…）不強制檢查。"""
    parts = np.split()
    if len(parts) < 2:
        return False
    article = parts[0].lower()
    if article not in ("a", "an"):
        return True
    return _article_for(parts[1]) == article


def _is_valid_material_entry(entry, existing_en: set[str], existing_sent: set[str]) -> bool:
    """單一教材詞條的合法性檢查：欄位齊全、分類合法、en/sent 不重複、冠詞一致。"""
    if not isinstance(entry, dict):
        return False
    for key in _MATERIAL_ENTRY_KEYS:
        if not (isinstance(entry.get(key), str) and entry[key].strip()):
            return False
    if entry["cat"] not in _MATERIAL_CATS:
        return False
    if entry["en"].lower() in existing_en:
        return False
    if entry["sent"] in existing_sent:
        return False
    if not _article_is_consistent(entry["np"]):
        return False
    return True


def register_material_vocab(entries: list[dict]) -> tuple[list[dict], int]:
    """驗證教材 agent 提議的詞條，通過的原地合併進 VOCAB。

    回傳 ``(accepted_entries, rejected_count)``。任一詞條不合法只丟該條，
    不中斷整批；``accepted_entries`` 是 ``[{"zh", "en", "cat", "np", "sent"}]``，
    且已確實合併進 ``VOCAB``。

    合併用 ``VOCAB[zh] = {...}`` 原地寫入同一個 dict 物件——
    homework.py／games.py／profile.py 都是 ``from server.scaffold import VOCAB``
    拿到同一個參照，原地 mutate 後這些模組不必改就看得到新詞。

    任何輸入（None、非 dict、缺欄位）都不拋例外，直接計入 rejected。
    """
    accepted: list[dict] = []
    rejected = 0
    existing_en = {v["en"].lower() for v in VOCAB.values()}
    existing_sent = {v["sent"] for v in VOCAB.values()}
    for entry in entries or []:
        if not _is_valid_material_entry(entry, existing_en, existing_sent):
            rejected += 1
            continue
        zh = entry["zh"]
        clean = {"en": entry["en"], "cat": entry["cat"],
                  "np": entry["np"], "sent": entry["sent"]}
        VOCAB[zh] = clean
        existing_en.add(entry["en"].lower())
        existing_sent.add(entry["sent"])
        accepted.append({"zh": zh, **clean})
    return accepted, rejected
```

- [ ] **Step 4: 執行測試確認全部通過**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_scaffold_register_material.py -v`
Expected: PASS（9 個測試全過）

- [ ] **Step 5: 對既有詞庫回歸測試跑一次，確認沒有動到既有規則**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_scaffold_vocab.py tests/test_curriculum_data.py -v`
Expected: PASS（既有測試不受影響）

- [ ] **Step 6: Commit**

```bash
git add server/scaffold.py tests/test_scaffold_register_material.py
git commit -m "feat(scaffold): 教材詞條驗證與合併入口 register_material_vocab

老師上傳教材提煉出的詞條要進同一個 VOCAB 才能讓 games/homework/srs
零改動自動支援，這裡是唯一的驗證閘門：cat 白名單、en/sent 去重、
a/an 冠詞規則，一批裡壞一條不影響其他條。"
```

---

## Task 2: `profile._EN_INFO` 改成惰性函式 `_en_info()`

**Files:**
- Modify: `server/profile.py:26-29`（常量定義）、`server/profile.py:112-143`（`build_profile` 內兩處查找）
- Test: `tests/test_profile.py`（新增一個測試函式；若檔案不存在則新建）

**Interfaces:**
- Consumes: `scaffold.VOCAB`（Task 1 之後可能已被合併新詞）
- Produces: `profile._en_info() -> dict[str, dict]`——取代原本的模組常量 `_EN_INFO`

**背景**：`_EN_INFO` 目前是 import 時算好的靜態快照，教材合併發生在 import 之後不會反映進去，學生 profile 的興趣/字彙掌握度統計就看不到教材詞。這是本次唯一必須碰的既有分析邏輯。

- [ ] **Step 1: 確認目前 `_EN_INFO` 的位置與用法（不用寫測試，先讀程式碼定位）**

Run: `grep -n "_EN_INFO" /home/budaedu/talkybuddy/server/profile.py`
Expected 輸出三處：第 26 行定義、第 124 行與第 132 行的 `.get(token)` 查找（皆在 `build_profile` 函式內）。

- [ ] **Step 2: 寫失敗測試——驗證 profile 能看到合併後的新詞**

若 `tests/test_profile.py` 尚不存在，新建；若已存在，在檔案末尾追加以下測試（先確認檔案開頭有 `from server import profile, scaffold` 之類的 import，沒有就自行補上）：

```python
def test_build_profile_picks_up_materially_registered_vocab():
    """教材 agent 合併進 VOCAB 的新詞，profile 的興趣分類要看得到（不是 import 時的舊快照）。"""
    from server import profile, scaffold

    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        scaffold.VOCAB["無尾熊"] = {
            "en": "koala", "cat": "animal", "np": "a koala", "sent": "I see a koala."
        }
        interactions = [
            {"student_text": "I see a koala.", "ai_response_text": "Great job!",
             "asr_confidence": 0.9, "seq": 1},
        ]
        result = profile.build_profile(interactions, [])

        assert result["interests"].get("animal", 0) >= 1, (
            "新合併的 koala 詞應被計入 animal 分類的興趣統計，"
            "若 _EN_INFO 仍是 import 時的舊快照就會漏算"
        )
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)
```

如果 `build_profile` 回傳結構裡 `interests` 的確切鍵名跟上面假設的不同，先執行下一步看實際失敗訊息或直接讀 `server/profile.py` 裡 `build_profile` 組裝回傳值那段（搜尋 `"interests"`）確認鍵名，再調整斷言。

- [ ] **Step 3: 執行測試，確認目前會失敗（因為 `_EN_INFO` 是舊快照）**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_profile.py::test_build_profile_picks_up_materially_registered_vocab -v`
Expected: FAIL（`animal` 分類統計不到新詞，或斷言的鍵值為 0）

- [ ] **Step 4: 把 `_EN_INFO` 常量改成函式**

把 `server/profile.py` 第 26-29 行：

```python
_EN_INFO: dict[str, dict] = {
    v["en"].lower(): {"en": v["en"], "zh": zh, "cat": v["cat"]}
    for zh, v in scaffold.VOCAB.items()
}
```

改成：

```python
def _en_info() -> dict[str, dict]:
    """英文詞（小寫）→ 詞條反查表。每次呼叫依當下 scaffold.VOCAB 重算，
    確保教材 agent 合併進來的新詞（見 scaffold.register_material_vocab）
    也會被學生 profile 的興趣/掌握度統計看到。"""
    return {
        v["en"].lower(): {"en": v["en"], "zh": zh, "cat": v["cat"]}
        for zh, v in scaffold.VOCAB.items()
    }
```

- [ ] **Step 5: 更新 `build_profile` 內的兩處查找**

在 `server/profile.py` 的 `build_profile` 函式裡，找到第 112 行附近 `for it in inters:` 迴圈**之前**插入一行（跟其他區域變數初始化放在一起，例如緊接在 `en_ratios: list[float] = []` 之後）：

```python
    en_info = _en_info()
```

然後把迴圈內原本兩處：

```python
            info = _EN_INFO.get(token)
```

（第 124 行與第 132 行，各自的上下文不同但寫法相同）都改成：

```python
            info = en_info.get(token)
```

- [ ] **Step 6: 執行測試確認通過**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_profile.py -v`
Expected: PASS

- [ ] **Step 7: 執行既有 profile 測試全套，確認沒有回歸**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/ -k profile -v`
Expected: PASS（既有 profile 相關測試不受影響）

- [ ] **Step 8: Commit**

```bash
git add server/profile.py tests/test_profile.py
git commit -m "fix(profile): _EN_INFO 從 import 期快照改成惰性函式

原本的模組常量在 import 時就算好反查表，教材 agent 之後合併進
scaffold.VOCAB 的新詞永遠看不到，學生興趣/字彙掌握度統計會漏算。
改成 _en_info() 每次呼叫重算，成本可忽略（一兩百個詞條的 dict 推導）。"
```

---

## Task 3: `server/agents/material.py`（規則式路徑）

**Files:**
- Create: `server/agents/material.py`
- Test: `tests/test_agent_material.py`

**Interfaces:**
- Consumes: `scaffold.VOCAB`
- Produces: `_rule_based_extract(text: str) -> dict`（模組內部函式，Task 4 會在此基礎上加公開入口 `extract_vocab`）

**背景**：規則式路徑不生成任何新詞——只在教材文字裡比對既有 `scaffold.VOCAB` 的中文鍵或英文詞，命中的詞就是這份教材的重點詞。因為這些詞本來就已經在 `VOCAB` 裡，這條路徑**不需要**呼叫 `register_material_vocab`。

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_agent_material.py`：

```python
# -*- coding: utf-8 -*-
"""test_agent_material.py — 教材提煉 agent（server/agents/material.py）測試集。

嚴格 TDD：規則式路徑先於雲端路徑測試。
"""

from __future__ import annotations


def test_rule_based_extract_finds_existing_vocab_by_chinese_key():
    """教材文字含既有詞庫的中文鍵 → 命中並回傳該詞條。"""
    from server.agents.material import _rule_based_extract

    result = _rule_based_extract("今天我們去動物園看獅子和大象。")

    assert result["source"] == "rule"
    assert result["rejected_count"] == 0
    hit_zh = {e["zh"] for e in result["entries"]}
    assert "獅子" in hit_zh
    assert "大象" in hit_zh
    assert result["accepted_count"] == len(result["entries"])


def test_rule_based_extract_finds_existing_vocab_by_english_word():
    """教材文字含既有詞庫的英文詞（不分大小寫）→ 也能命中。"""
    from server.agents.material import _rule_based_extract

    result = _rule_based_extract("Today we saw a Lion at the zoo.")

    hit_en = {e["en"] for e in result["entries"]}
    assert "lion" in hit_en


def test_rule_based_extract_never_invents_new_words():
    """規則式路徑絕不能回傳不在既有 VOCAB 裡的詞——就算文字裡有課綱外的字。"""
    from server.agents.material import _rule_based_extract
    from server.scaffold import VOCAB

    result = _rule_based_extract("我們今天學了 quokka 這個新單字，牠是一種可愛的動物。")

    for entry in result["entries"]:
        assert entry["zh"] in VOCAB, f"{entry} 不應是自創詞"
        assert VOCAB[entry["zh"]]["en"] == entry["en"]


def test_rule_based_extract_handles_no_match_without_raising():
    """教材文字完全沒有課綱詞彙 → 空 entries，不拋例外，仍是合法 schema。"""
    from server.agents.material import _rule_based_extract

    result = _rule_based_extract("這是一段完全沒有相關詞彙的中文。")

    assert result["entries"] == []
    assert result["accepted_count"] == 0
    assert result["rejected_count"] == 0
    assert isinstance(result["topic"], str) and result["topic"].strip()


def test_rule_based_extract_handles_empty_and_none_input():
    """空字串／None 輸入不拋例外。"""
    from server.agents.material import _rule_based_extract

    for bad_input in ("", None):
        result = _rule_based_extract(bad_input)
        assert result["source"] == "rule"
        assert result["entries"] == []


def test_rule_based_extract_caps_at_max_entries():
    """就算教材文字命中很多既有詞，也不超過上限（避免單次教材塞爆詞庫）。"""
    from server.agents.material import _rule_based_extract
    from server.scaffold import VOCAB

    # 把所有詞庫的中文鍵串成一段長文字，確保命中數遠超過上限
    all_keys_text = "、".join(VOCAB.keys())
    result = _rule_based_extract(all_keys_text)

    assert len(result["entries"]) <= 8
```

- [ ] **Step 2: 執行測試確認全部失敗**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_agent_material.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'server.agents.material'`）

- [ ] **Step 3: 建立 `server/agents/material.py`**

```python
# -*- coding: utf-8 -*-
"""material.py — 教材提煉 agent（子專案 F）。

公開契約：
    extract_vocab(text: str, *, allow_cloud: bool = True) -> dict

回傳固定 schema（雲端與規則式格式一致）：
    {
        "topic": str,              # 這份教材的主題，人話描述
        "entries": [               # 通過驗證、已合併進 VOCAB 的詞條
            {"en": str, "zh": str, "cat": str, "np": str, "sent": str}
        ],
        "accepted_count": int,
        "rejected_count": int,
        "source": "cloud" | "rule",
    }

設計原則（與 homework / report / orchestrator 一致）：
- 雲端路徑走 agent_backends.resolve("material")：AgentCore Harness → Bedrock Converse。
- allow_cloud=False 完全不觸雲端，連 resolve_config 都不呼叫。
- 雲端回覆整體字串經 guardrails.passes_guardrail；不通過降級回規則式。
- 任何例外不往外拋，一律靜默降級回規則式；規則式路徑永遠能產出合法結果。
- 規則式路徑不發明新詞：只在教材文字裡比對既有 scaffold.VOCAB，命中的詞
  就是這份教材的重點詞，保證不可能弄壞全域字典。這些詞本來就已經在
  VOCAB 裡，因此規則式路徑不呼叫 register_material_vocab。
"""

from __future__ import annotations

import logging

from server import scaffold

_log = logging.getLogger(__name__)

_MAX_ENTRIES = 8

_CAT_ZH = {
    "food": "食物", "school": "學校", "animal": "動物",
    "family": "家庭", "action": "動作", "color": "顏色",
}


def _rule_based_extract(text: str) -> dict:
    """規則式教材提煉（完全離線，零外部依賴）。

    不生成任何新詞——只在文字裡比對既有 scaffold.VOCAB 的中文鍵/英文詞，
    命中的詞就是這份教材的重點詞。
    """
    text = text or ""
    text_lower = text.lower()
    hits: list[dict] = []
    for zh, info in scaffold.VOCAB.items():
        if len(hits) >= _MAX_ENTRIES:
            break
        if zh in text or info["en"].lower() in text_lower:
            hits.append({"en": info["en"], "zh": zh, "cat": info["cat"],
                         "np": info["np"], "sent": info["sent"]})

    if hits:
        cat_counts: dict[str, int] = {}
        for h in hits:
            cat_counts[h["cat"]] = cat_counts.get(h["cat"], 0) + 1
        top_cat = max(cat_counts, key=lambda c: cat_counts[c])
        topic = f"教材中的{_CAT_ZH.get(top_cat, top_cat)}主題詞彙"
    else:
        topic = "教材中未找到對應課綱詞彙"

    return {
        "topic": topic,
        "entries": hits,
        "accepted_count": len(hits),
        "rejected_count": 0,
        "source": "rule",
    }
```

- [ ] **Step 4: 執行測試確認全部通過**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_agent_material.py -v`
Expected: PASS（6 個測試全過）

- [ ] **Step 5: Commit**

```bash
git add server/agents/material.py tests/test_agent_material.py
git commit -m "feat(agents): 教材提煉 agent 規則式路徑

離線時老師上傳教材，效果是「標出教材裡對應課綱的既有詞」，不是
語意生成——保證規則式路徑不可能弄壞全域 VOCAB。雲端路徑下一步接。"
```

---

## Task 4: `extract_vocab` 公開入口 + 雲端路徑

**Files:**
- Modify: `server/agents/material.py`（新增 `extract_vocab`、`_build_user_prompt`、`_parse_cloud_response`、`_SYSTEM_PROMPT`）
- Modify: `server/agentcore.py:65-69`（`_ROLE_ENV` 新增 `"material"` 項）
- Test: `tests/test_agent_material.py`（追加）、`tests/test_agent_backends.py`（追加一個測試）

**Interfaces:**
- Consumes: `agent_backends.resolve(role: str) -> tuple[dict|None, dict|None]`（既有）、`agentcore.invoke(cfg, user_message, *, session_id=None, actor_id=None, timeout_s=...) -> str`（既有）、`bedrock_converse.converse_text(system, user, *, cfg, max_tokens=1024, temperature=0.7, timeout_s=...) -> str`（既有）、`guardrails.passes_guardrail(text) -> bool`（既有）、`guardrails.consent_granted() -> bool`（既有）、`scaffold.register_material_vocab(entries) -> tuple[list[dict], int]`（Task 1）
- Produces: `material.extract_vocab(text: str, *, allow_cloud: bool = True) -> dict`（公開契約，供 Task 6 的 API 端點呼叫）

**背景**：`agentcore.py` 的 `_ROLE_ENV` 目前只有 `orchestrator`／`homework`／`report` 三個角色。不加上 `"material"` 的話，`agentcore.resolve_config("material")` 永遠回 `None`，AgentCore 分支形同虛設（現場即使補上憑證也用不到）。這是讓「AgentCore 留在鏈首」這句話誠實成立的必要一步。

- [ ] **Step 1: 在 `agentcore.py` 註冊 material 角色**

把 `server/agentcore.py` 第 65-69 行：

```python
_ROLE_ENV = {
    "orchestrator": "AGENTCORE_HARNESS_ORCHESTRATOR",
    "homework": "AGENTCORE_HARNESS_HOMEWORK",
    "report": "AGENTCORE_HARNESS_REPORT",
}
```

改成：

```python
_ROLE_ENV = {
    "orchestrator": "AGENTCORE_HARNESS_ORCHESTRATOR",
    "homework": "AGENTCORE_HARNESS_HOMEWORK",
    "report": "AGENTCORE_HARNESS_REPORT",
    "material": "AGENTCORE_HARNESS_MATERIAL",
}
```

- [ ] **Step 2: 追加一個 `test_agent_backends.py` 測試，確認新角色能出現在鏈上**

在 `tests/test_agent_backends.py` 的 `_ENV` 清單（約第 20-24 行）加入 `"AGENTCORE_HARNESS_MATERIAL"`：

```python
_ENV = [
    "TALKYBUDDY_AGENT_BACKEND", "TALKYBUDDY_CLOUD_PROVIDER",
    "AGENTCORE_HARNESS_ORCHESTRATOR", "AGENTCORE_HARNESS_HOMEWORK",
    "AGENTCORE_HARNESS_REPORT", "AGENTCORE_HARNESS_MATERIAL", "AGENTCORE_REGION",
]
```

然後在檔案末尾追加：

```python
def test_chain_supports_material_role(_clean_env):
    """material 角色比照既有三個 agent，能出現在降級鏈最前面。"""
    _clean_env.setenv("TALKYBUDDY_AGENT_BACKEND", "agentcore")
    _clean_env.setenv("AGENTCORE_HARNESS_MATERIAL", "arn:material")
    _clean_env.setenv("TALKYBUDDY_CLOUD_PROVIDER", "bedrock")
    assert agent_backends.chain("material") == ["agentcore", "bedrock", "rule"]
```

- [ ] **Step 3: 執行測試確認通過**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_agent_backends.py -v`
Expected: PASS（含新增的 `test_chain_supports_material_role`）

- [ ] **Step 4: 寫 `extract_vocab` 的失敗測試（追加進 `tests/test_agent_material.py`）**

在檔案末尾追加：

```python
# ---------------------------------------------------------------------------
# extract_vocab 公開入口：allow_cloud 閘門與降級鏈
# ---------------------------------------------------------------------------

def _assert_valid_schema(result: dict, expected_source: str | None = None) -> None:
    assert isinstance(result, dict)
    assert isinstance(result.get("topic"), str) and result["topic"].strip()
    assert isinstance(result.get("entries"), list)
    assert isinstance(result.get("accepted_count"), int)
    assert isinstance(result.get("rejected_count"), int)
    assert result.get("source") in ("cloud", "rule")
    if expected_source is not None:
        assert result["source"] == expected_source


def test_allow_cloud_false_never_touches_network(monkeypatch):
    """allow_cloud=False → resolve_config／converse_text 皆不被呼叫，source='rule'。"""
    from server.agents import material
    from server import bedrock_converse

    def _should_not_call(*a, **kw):
        import pytest
        pytest.fail("allow_cloud=False 時不應呼叫雲端函式")

    monkeypatch.setattr(bedrock_converse, "resolve_config", _should_not_call)
    monkeypatch.setattr(bedrock_converse, "converse_text", _should_not_call)

    result = material.extract_vocab("我們去動物園看獅子。", allow_cloud=False)

    _assert_valid_schema(result, expected_source="rule")


def test_cloud_path_success_merges_new_entries(monkeypatch):
    """雲端回傳合法 JSON → 詞條經 register_material_vocab 驗證合併，source='cloud'。"""
    import json
    from server.agents import material
    from server import bedrock_converse, scaffold

    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        cloud_json = json.dumps({
            "topic": "動物園一日遊",
            "entries": [
                {"en": "koala", "zh": "無尾熊", "cat": "animal",
                 "np": "a koala", "sent": "I see a koala."},
            ],
            "source": "cloud",
        }, ensure_ascii=False)

        monkeypatch.setattr(bedrock_converse, "resolve_config",
                            lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
        monkeypatch.setattr(bedrock_converse, "converse_text",
                            lambda *a, **kw: cloud_json)

        result = material.extract_vocab("今天去動物園看了一隻無尾熊。", allow_cloud=True)

        _assert_valid_schema(result, expected_source="cloud")
        assert result["accepted_count"] == 1
        assert result["rejected_count"] == 0
        assert "無尾熊" in scaffold.VOCAB
        assert scaffold.VOCAB["無尾熊"]["en"] == "koala"
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)


def test_cloud_response_with_invalid_entries_reports_rejected(monkeypatch):
    """雲端提議的詞條裡有不合法的（分類錯誤）→ accepted/rejected 誠實回報。"""
    import json
    from server.agents import material
    from server import bedrock_converse, scaffold

    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        cloud_json = json.dumps({
            "topic": "動物園一日遊",
            "entries": [
                {"en": "koala", "zh": "無尾熊", "cat": "animal",
                 "np": "a koala", "sent": "I see a koala."},
                {"en": "robot", "zh": "機器人", "cat": "toy",  # 不合法分類
                 "np": "a robot", "sent": "I see a robot."},
            ],
            "source": "cloud",
        }, ensure_ascii=False)

        monkeypatch.setattr(bedrock_converse, "resolve_config",
                            lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
        monkeypatch.setattr(bedrock_converse, "converse_text",
                            lambda *a, **kw: cloud_json)

        result = material.extract_vocab("動物園教材", allow_cloud=True)

        assert result["accepted_count"] == 1
        assert result["rejected_count"] == 1
        assert "機器人" not in scaffold.VOCAB
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)


def test_cloud_failure_falls_back_to_rule(monkeypatch):
    """converse_text 拋例外 → 靜默降級，source='rule'。"""
    from server.agents import material
    from server import bedrock_converse

    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("網路超時")))

    result = material.extract_vocab("動物園教材", allow_cloud=True)

    _assert_valid_schema(result, expected_source="rule")


def test_cloud_invalid_json_falls_back_to_rule(monkeypatch):
    """converse_text 回傳非 JSON → 降級到規則式，不拋例外。"""
    from server.agents import material
    from server import bedrock_converse

    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text",
                        lambda *a, **kw: "這不是 JSON {broken")

    result = material.extract_vocab("動物園教材", allow_cloud=True)

    _assert_valid_schema(result, expected_source="rule")


def test_guardrail_hit_falls_back_to_rule(monkeypatch):
    """雲端回覆含禁詞 → 護欄攔截後降級，source='rule'。"""
    import json
    from server.agents import material
    from server import bedrock_converse

    unsafe = json.dumps({
        "topic": "動物園",
        "entries": [{"en": "kill", "zh": "殺", "cat": "action",
                     "np": "kill", "sent": "Kill the monster."}],
        "source": "cloud",
    }, ensure_ascii=False)

    monkeypatch.setattr(bedrock_converse, "resolve_config",
                        lambda role=None: {"region": "ap-east-2", "model_id": "test-model"})
    monkeypatch.setattr(bedrock_converse, "converse_text", lambda *a, **kw: unsafe)

    result = material.extract_vocab("動物園教材", allow_cloud=True)

    _assert_valid_schema(result, expected_source="rule")


def test_no_cloud_backend_configured_falls_back_to_rule(monkeypatch):
    """allow_cloud=True 但沒有任何雲端後端設定（resolve_config 回 None）→ 規則式。"""
    from server.agents import material
    from server import bedrock_converse

    monkeypatch.setattr(bedrock_converse, "resolve_config", lambda role=None: None)

    result = material.extract_vocab("動物園教材", allow_cloud=True)

    _assert_valid_schema(result, expected_source="rule")


def test_no_exception_on_extreme_inputs():
    """None／空字串輸入不拋例外。"""
    from server.agents import material

    for bad in (None, ""):
        result = material.extract_vocab(bad, allow_cloud=False)
        _assert_valid_schema(result)
```

- [ ] **Step 5: 執行測試確認新增的測試全部失敗**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_agent_material.py -v`
Expected: FAIL（`AttributeError: module 'server.agents.material' has no attribute 'extract_vocab'`）

- [ ] **Step 6: 在 `server/agents/material.py` 補上雲端路徑與公開入口**

在檔案頂部 import 區塊（`from server import scaffold` 那行）改成：

```python
from __future__ import annotations

import json
import logging
import re

from server import agent_backends, agentcore, bedrock_converse, guardrails, scaffold
```

在 `_rule_based_extract` 函式**之後**（檔案其餘部分）追加：

```python
_TIMEOUT_S = 12.0
_MAX_TEXT_LEN = 2000  # 教材原文送雲端的長度上限

_SYSTEM_PROMPT = (
    "你是台灣國小英語教材分析專家。從老師提供的教材文字中，"
    "挑出最多 8 個適合國小生學習的詞彙。"
    "每個詞附：英文（en）、繁體中文（zh）、分類（cat，只能是 "
    "food/school/animal/family/action/color 之一）、"
    "含正確冠詞的名詞片語（np）、一句用到這個詞的目標英文例句（sent）。"
    "同時給這份教材一個簡短的主題描述（topic，繁體中文）。"
    "只輸出一個 JSON 物件，不得有 markdown 圍欄或額外文字。"
)


def _build_user_prompt(text: str) -> str:
    schema_example = {
        "topic": "動物園一日遊",
        "entries": [
            {"en": "lion", "zh": "獅子", "cat": "animal",
             "np": "a lion", "sent": "I see a lion."},
        ],
        "source": "cloud",
    }
    return (
        f"教材內容：\n{text[:_MAX_TEXT_LEN]}\n\n"
        "請從上述教材挑出最多 8 個適合國小生的詞彙。"
        "cat 只能是 food/school/animal/family/action/color 之一。"
        "僅輸出符合以下 schema 的 JSON 物件（source 固定為 \"cloud\"）：\n"
        + json.dumps(schema_example, ensure_ascii=False)
    )


def _parse_cloud_response(raw_text: str) -> dict | None:
    """解析雲端回傳的 JSON 字串並做最基本的形狀檢查；任何問題回 None。"""
    text = (raw_text or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not (isinstance(data.get("topic"), str) and data["topic"].strip()):
        return None
    if not isinstance(data.get("entries"), list):
        return None
    return data


def extract_vocab(text: str, *, allow_cloud: bool = True) -> dict:
    """教材提煉 agent 主入口。

    流程：
    1. allow_cloud=False 或未取得家長同意 → 直接走規則式，不碰任何雲端呼叫。
    2. allow_cloud=True → 嘗試 agent_backends.resolve("material")：
       AgentCore Harness → Bedrock Converse。
    3. 雲端回覆整體字串經 guardrails.passes_guardrail；不通過 → 降級。
    4. 解析 JSON，對提議詞條呼叫 scaffold.register_material_vocab 逐條驗證。
    5. 任何例外不往外拋，一律降級回規則式；規則式路徑永遠能產出合法結果。
    """
    try:
        return _extract_vocab(text, allow_cloud=allow_cloud)
    except Exception:
        _log.exception("extract_vocab 全數路徑失敗，回傳最小合法結果")
        return {
            "topic": "教材解析暫時失敗", "entries": [],
            "accepted_count": 0, "rejected_count": 0, "source": "rule",
        }


def _extract_vocab(text: str, *, allow_cloud: bool) -> dict:
    text = text if isinstance(text, str) else ""

    if not allow_cloud or not guardrails.consent_granted():
        return _rule_based_extract(text)

    try:
        ac_cfg, cfg = agent_backends.resolve("material")
        if ac_cfg is None and cfg is None:
            return _rule_based_extract(text)

        user_prompt = _build_user_prompt(text)

        raw_text = None
        if ac_cfg is not None:
            try:
                raw_text = agentcore.invoke(
                    ac_cfg, user_prompt,
                    actor_id=None,
                    session_id="material-upload",
                )
            except Exception:
                _log.exception("extract_vocab AgentCore 失敗，改試 Bedrock Converse")
                raw_text = None

        if raw_text is None:
            if cfg is None:
                return _rule_based_extract(text)
            raw_text = bedrock_converse.converse_text(
                _SYSTEM_PROMPT, user_prompt, cfg=cfg,
                max_tokens=768, timeout_s=_TIMEOUT_S,
            )

        if not guardrails.passes_guardrail(raw_text):
            _log.warning("extract_vocab 雲端回覆未通過護欄，降級回規則式")
            return _rule_based_extract(text)

        parsed = _parse_cloud_response(raw_text)
        if parsed is None:
            _log.warning("extract_vocab 雲端回覆 schema 不合法，降級回規則式")
            return _rule_based_extract(text)

        accepted_entries, rejected = scaffold.register_material_vocab(parsed["entries"])
        return {
            "topic": parsed["topic"],
            "entries": accepted_entries,
            "accepted_count": len(accepted_entries),
            "rejected_count": rejected,
            "source": "cloud",
        }
    except Exception:
        _log.exception("extract_vocab 雲端路徑失敗，降級回規則式")
        return _rule_based_extract(text)
```

- [ ] **Step 7: 執行全部測試確認通過**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_agent_material.py tests/test_agent_backends.py -v`
Expected: PASS 全部

- [ ] **Step 8: Commit**

```bash
git add server/agents/material.py server/agentcore.py tests/test_agent_material.py tests/test_agent_backends.py
git commit -m "feat(agents): 教材提煉 agent 雲端路徑 + material 角色接進降級鏈

extract_vocab 比照 homework/report/orchestrator 走
agent_backends.resolve('material')：AgentCore → Bedrock → 規則式。
agentcore._ROLE_ENV 補上 material 角色，否則 AgentCore 分支永遠是死路。
雲端提議的詞條一律經 register_material_vocab 驗證，誠實回報
accepted/rejected 而不是照單全收。"
```

---

## Task 5: `store.py` 教材持久化

**Files:**
- Modify: `server/store.py`（`init_db` 內新增 `materials` 表；新增 `add_material`／`list_materials`）
- Test: `tests/test_store_materials.py`

**Interfaces:**
- Consumes: 無新依賴，沿用 `store.py` 既有的 `_get_conn()`／`_lock`
- Produces: `store.add_material(payload: dict) -> int`、`store.list_materials() -> list[dict]`

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_store_materials.py`：

```python
# -*- coding: utf-8 -*-
"""test_store_materials.py — 教材上傳持久化（server/store.py 新增部分）。

tmp_db fixture（見 tests/conftest.py，autouse）已經把 DB 導向乾淨的 tmp 檔案，
本檔不需要自己處理隔離。
"""

from __future__ import annotations

from server import store


def test_add_material_returns_incrementing_seq():
    seq1 = store.add_material({"title": "動物園教材", "text": "...", "topic": "動物",
                                "entries": [], "accepted_count": 0,
                                "rejected_count": 0, "source": "rule"})
    seq2 = store.add_material({"title": "餐廳教材", "text": "...", "topic": "食物",
                                "entries": [], "accepted_count": 0,
                                "rejected_count": 0, "source": "rule"})
    assert seq2 == seq1 + 1


def test_list_materials_returns_oldest_first_with_full_payload():
    store.add_material({"title": "第一份", "text": "t1", "topic": "動物",
                         "entries": [{"zh": "無尾熊", "en": "koala", "cat": "animal",
                                      "np": "a koala", "sent": "I see a koala."}],
                         "accepted_count": 1, "rejected_count": 0, "source": "cloud"})
    store.add_material({"title": "第二份", "text": "t2", "topic": "食物",
                         "entries": [], "accepted_count": 0,
                         "rejected_count": 0, "source": "rule"})

    rows = store.list_materials()

    assert len(rows) == 2
    assert rows[0]["title"] == "第一份"  # 舊→新
    assert rows[1]["title"] == "第二份"
    assert rows[0]["entries"][0]["zh"] == "無尾熊"
    assert "seq" in rows[0] and "ts" in rows[0]


def test_list_materials_empty_when_none_uploaded():
    assert store.list_materials() == []
```

- [ ] **Step 2: 執行測試確認全部失敗**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_store_materials.py -v`
Expected: FAIL（`AttributeError: module 'server.store' has no attribute 'add_material'`）

- [ ] **Step 3: 在 `init_db()` 新增 `materials` 表**

在 `server/store.py` 的 `init_db()` 函式內，找到 `word_reviews` 表建立語句（第 188-200 行）之後、`conn.commit()` 之前，插入：

```python
        # 老師上傳教材（子專案 F）。全域共用，不分學生——教材是詞庫擴充，
        # 不是某個孩子的個人資料。entries_json 存 agent 回傳的已驗證詞條，
        # 供 app 啟動時 replay 回 scaffold.VOCAB（見 server/app.py 的 lifespan）。
        conn.execute(
            "CREATE TABLE IF NOT EXISTS materials ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts TEXT NOT NULL,"
            " payload TEXT NOT NULL)"
        )
```

- [ ] **Step 4: 新增 `add_material`／`list_materials`**

在 `server/store.py` 的 `list_agent_outputs` 函式定義結束後（該函式段落結尾，下一個函式定義之前），新增：

```python
def add_material(payload: dict) -> int:
    """新增一筆教材上傳紀錄，回傳自增 seq。

    payload 應含 title/text/topic/entries/accepted_count/rejected_count/source
    （見 server/agents/material.py 的回傳 schema，外加呼叫端補的 title/text）。
    全域共用，不帶 student_id——教材是詞庫擴充，不分學生。
    """
    body = dict(payload)
    ts = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ).isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO materials (ts, payload) VALUES (?, ?)",
            (ts, json.dumps(body, ensure_ascii=False)),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_materials() -> list[dict]:
    """列出全部已上傳教材（舊→新），供 app 啟動時依序 replay 回 scaffold.VOCAB。"""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT seq, ts, payload FROM materials ORDER BY seq ASC"
        ).fetchall()
    out: list[dict] = []
    for seq, ts, payload in rows:
        d = json.loads(payload)
        d["seq"] = int(seq)
        d["ts"] = ts
        out.append(d)
    return out
```

確認檔案頂部已有 `import datetime` 與 `import json`（既有 `add_interaction`／`add_agent_output` 已經在用，應該已存在，不用重複加）。

- [ ] **Step 5: 執行測試確認全部通過**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_store_materials.py -v`
Expected: PASS（3 個測試全過）

- [ ] **Step 6: 執行 store 既有測試全套確認沒有回歸**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/ -k store -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add server/store.py tests/test_store_materials.py
git commit -m "feat(store): 教材上傳持久化 materials 表

比照 agent_outputs 的 pattern，全域共用不分學生（教材是詞庫擴充）。
供 app 啟動時 replay 回 scaffold.VOCAB，裝置重啟後教材詞不遺失。"
```

---

## Task 6: `POST /api/material` 端點 + 啟動時 replay

**Files:**
- Modify: `server/app.py`（新增 `MaterialBody`、`api_material`、`_replay_materials`、`lifespan` 內呼叫）
- Test: `tests/test_app_material.py`

**Interfaces:**
- Consumes: `material.extract_vocab(text, *, allow_cloud) -> dict`（Task 4）、`store.add_material(payload) -> int`／`store.list_materials() -> list[dict]`（Task 5）、`scaffold.register_material_vocab(entries) -> tuple[list[dict], int]`（Task 1）、既有 `identity_from_header(authorization) -> dict`（`server/app.py` 既有函式）
- Produces: `POST /api/material`（tutor 角色專用）；`lifespan` 啟動流程新增 replay 步驟

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_app_material.py`：

```python
# -*- coding: utf-8 -*-
"""test_app_material.py — POST /api/material 端點。"""

from __future__ import annotations

from starlette.testclient import TestClient

from server import app as app_mod, auth, scaffold, store


def _tok(sub, role):
    return auth.issue_token(sub, role)


def test_requires_token():
    client = TestClient(app_mod.app)
    resp = client.post("/api/material", json={"title": "t", "text": "動物園"})
    assert resp.status_code == 401


def test_student_role_forbidden():
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('STUDENT-AMING-004', 'student')}"}
    resp = client.post("/api/material", json={"title": "t", "text": "動物園"},
                       headers=h)
    assert resp.status_code == 403


def test_tutor_can_upload_material_offline_rule_path():
    """network_mode 預設是 edge/測試環境沒有雲端設定，走規則式，仍要回合法 schema。"""
    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        client = TestClient(app_mod.app)
        h = {"Authorization": f"Bearer {_tok('TUTOR-001', 'tutor')}"}
        app_mod.pipeline.network_mode = "edge"

        resp = client.post("/api/material",
                           json={"title": "動物園教材", "text": "今天去看了獅子和大象。"},
                           headers=h)

        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "rule"
        assert "topic" in body and "accepted_count" in body and "rejected_count" in body
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)


def test_uploaded_material_is_persisted():
    client = TestClient(app_mod.app)
    h = {"Authorization": f"Bearer {_tok('TUTOR-001', 'tutor')}"}
    app_mod.pipeline.network_mode = "edge"

    client.post("/api/material", json={"title": "動物園教材", "text": "獅子"},
               headers=h)

    rows = store.list_materials()
    assert len(rows) == 1
    assert rows[0]["title"] == "動物園教材"


def test_lifespan_replay_merges_stored_materials_into_vocab():
    """啟動時 replay：DB 裡已有的教材詞條要重新合併回 scaffold.VOCAB。"""
    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        store.add_material({
            "title": "舊教材", "text": "...", "topic": "動物",
            "entries": [{"zh": "無尾熊", "en": "koala", "cat": "animal",
                        "np": "a koala", "sent": "I see a koala."}],
            "accepted_count": 1, "rejected_count": 0, "source": "cloud",
        })
        assert "無尾熊" not in scaffold.VOCAB  # replay 前確認還沒合併

        app_mod._replay_materials()

        assert "無尾熊" in scaffold.VOCAB
        assert scaffold.VOCAB["無尾熊"]["en"] == "koala"
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)
```

- [ ] **Step 2: 執行測試確認全部失敗**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_app_material.py -v`
Expected: FAIL（404 `/api/material` 不存在，`_replay_materials` 不存在）

- [ ] **Step 3: 在 `server/app.py` 新增 `MaterialBody` 與端點**

在 `NetworkModeBody` 類別定義（第 326-329 行）之後插入：

```python
class MaterialBody(BaseModel):
    """POST /api/material 的 body。"""

    title: str
    text: str
```

在 `/api/network_mode` 端點函式結束之後（下一個函式定義 `identity_from_header` 之前，或任一函式之間的空白處，只要在 `app` 已建立之後），新增：

```python
@app.post("/api/material")
async def api_material(body: MaterialBody,
                       authorization: str | None = Header(default=None)):
    """老師上傳教材文字，經 agent 提煉後合併進 scaffold.VOCAB（子專案 F）。

    tutor 角色專用：這是老師端動作，會改變全域詞庫，跟 /api/network_mode
    （不限角色）不同級。
    """
    claims = identity_from_header(authorization)
    if claims["role"] != "tutor":
        raise HTTPException(status_code=403, detail="只有 tutor 角色能上傳教材")

    from server.agents import material as material_agent

    result = material_agent.extract_vocab(
        body.text, allow_cloud=(pipeline.network_mode == "cloud"),
    )
    try:
        store.add_material({
            "title": body.title,
            "text": body.text,
            "topic": result["topic"],
            "entries": result["entries"],
            "accepted_count": result["accepted_count"],
            "rejected_count": result["rejected_count"],
            "source": result["source"],
        })
    except Exception:
        logger.exception("教材上傳持久化失敗，本次合併已生效但重啟後會遺失")
    return result
```

**注意**：`identity_from_header` 定義在 `/api/network_mode` 端點**之後**（第 405 行左右），Python 對模組層級函式呼叫不要求定義順序在呼叫之前，只要在函式**執行時**已經定義即可（模組載入完成後才會有請求進來），所以新端點放在 `identity_from_header` 定義之前或之後都能運作；若不確定，直接放在 `identity_from_header` 定義**之後**最保險，跟 `/api/interactions` 等既有端點的相對位置一致。

- [ ] **Step 4: 新增 `_replay_materials` 並接進 `lifespan`**

在 `_prewarm_engines()` 函式定義（第 78-91 行左右）之後，`lifespan` 定義之前，新增：

```python
def _replay_materials() -> None:
    """啟動時把先前上傳的教材詞重新合併進 scaffold.VOCAB。

    scaffold.VOCAB 是記憶體內的全域字典，process 重啟就清空；教材是
    老師上傳的東西，裝置重啟後不該消失，所以每次啟動都從 DB 重放一次。
    任何失敗只記 log，不擋啟動——沒有教材詞不影響核心對話迴圈。
    """
    try:
        for m in store.list_materials():
            entries = m.get("entries") or []
            if entries:
                scaffold.register_material_vocab(entries)
    except Exception:
        logger.exception("教材 replay 失敗，本次啟動的自訂詞彙可能不完整")
```

把 `lifespan` 函式本體：

```python
    auth.assert_secret_is_safe()
    store.init_db()
    store.seed_demo()
    threading.Thread(target=_prewarm_engines, daemon=True).start()
    yield
```

改成：

```python
    auth.assert_secret_is_safe()
    store.init_db()
    store.seed_demo()
    _replay_materials()
    threading.Thread(target=_prewarm_engines, daemon=True).start()
    yield
```

- [ ] **Step 5: 執行測試確認全部通過**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_app_material.py -v`
Expected: PASS（5 個測試全過）

- [ ] **Step 6: 執行 app.py 既有測試全套，確認沒有回歸**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/ -k app -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add server/app.py tests/test_app_material.py
git commit -m "feat(app): POST /api/material 端點 + 啟動時教材 replay

tutor 角色專用；network_mode==cloud 才允許 agent 走雲端，
edge 模式強制規則式。啟動時從 materials 表 replay 回 scaffold.VOCAB，
裝置重啟後老師先前上傳的教材詞不會消失。"
```

---

## Task 7: 整合驗證——新教材詞流進既有 homework / games

**Files:**
- Test: `tests/test_material_integration.py`

**Interfaces:**
- Consumes: `scaffold.register_material_vocab`（Task 1）、`server.agents.homework.generate_homework`（既有）、`server.games`（既有）

這個任務不新增production code，純粹驗證 §3 的核心承諾成立：詞條原地合併後，**完全不改** `homework.py`／`games.py` 的情況下，新詞真的會出現在既有系統的候選池裡。

- [ ] **Step 1: 寫測試**

建立 `tests/test_material_integration.py`：

```python
# -*- coding: utf-8 -*-
"""test_material_integration.py — 教材詞合併後，既有 homework/games 零改動自動支援。

驗證 docs/superpowers/specs/2026-08-01-teacher-material-agent-design.md §3
的核心承諾：VOCAB 原地合併後，homework.py／games.py 完全不用改就看得到新詞。
"""

from __future__ import annotations


def test_homework_picks_up_newly_registered_word():
    """合併新詞到 animal 分類後，該分類產作業時新詞要有機會出現在候選池裡。"""
    from server import scaffold
    from server.agents.homework import _pick_vocab_entries

    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        accepted, rejected = scaffold.register_material_vocab([
            {"en": "koala", "zh": "無尾熊", "cat": "animal",
             "np": "a koala", "sent": "I see a koala."},
        ])
        assert rejected == 0 and len(accepted) == 1

        # animal 分類挑滿詞庫所有候選（n 設大一點確保新詞排得進來）
        entries = _pick_vocab_entries("pronunciation", n=100)
        zh_keys = {e["zh_key"] for e in entries}

        assert "無尾熊" in zh_keys, (
            "新合併的教材詞應出現在 homework 的候選池裡，"
            "若沒出現代表 homework._pick_vocab_entries 沒有即時讀 VOCAB"
        )
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)


def test_games_module_sees_new_word_via_shared_vocab_object():
    """games.py 讀的 scaffold.VOCAB 跟合併時操作的是同一個物件。"""
    from server import scaffold, games

    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    try:
        scaffold.register_material_vocab([
            {"en": "koala", "zh": "無尾熊", "cat": "animal",
             "np": "a koala", "sent": "I see a koala."},
        ])

        assert games.scaffold.VOCAB is scaffold.VOCAB, (
            "games.py 應該跟 scaffold.py 共用同一個 VOCAB 物件參照"
        )
        assert "無尾熊" in games.scaffold.VOCAB
    finally:
        scaffold.VOCAB.clear()
        scaffold.VOCAB.update(snapshot)
```

若 `_pick_vocab_entries` 的參數簽章跟上面假設的不同（例如 `n` 不是關鍵字參數，或還需要 `due_words`／`rotation`），先讀 `server/agents/homework.py` 裡 `_pick_vocab_entries` 的定義（本計畫 Task 3 之前已經讀過完整內容，簽章是 `_pick_vocab_entries(dim: str, n: int = 5, due_words: list[str] | None = None, rotation: int = 0) -> list[dict]`），依實際簽章調整呼叫方式，不要改函式本身。

- [ ] **Step 2: 執行測試**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/test_material_integration.py -v`
Expected: PASS（若失敗，先確認失敗原因是測試寫錯還是 Task 1-4 有遺漏，不要為了讓測試過而修改 `homework.py`／`games.py`——這兩個檔案本次計畫刻意不動）

- [ ] **Step 3: 跑一次全專案測試，確認沒有任何既有測試被打壞**

Run: `cd /home/budaedu/talkybuddy && python3 -m pytest tests/ -v 2>&1 | tail -60`
Expected: 全部 PASS（或跟開工前 baseline 一致，無新增失敗）

- [ ] **Step 4: Commit**

```bash
git add tests/test_material_integration.py
git commit -m "test(material): 驗證教材詞合併後 homework/games 零改動自動支援

釘住設計文件 §3 的核心承諾：VOCAB 原地 mutate 後，既有兩個消費端
（homework._pick_vocab_entries／games.py）完全不用改就看得到新詞。"
```

---

## 完成後：現場彩排手動驗證（不是自動化任務，上台前務必照走一次）

1. 啟動 server（`uvicorn server.app:app` 或既有啟動腳本），開啟 `/teacher`
2. 用 `tutor@demo` / `demo1234` 登入（見 `server/auth.py` 的 `_SEED`），呼叫 `POST /api/material`
   貼一段含 3-5 個課綱內詞彙的短文，確認回應顯示 `accepted_count`
3. 開一局既有小遊戲（I Spy 選對應主題），確認新詞有機會被抽到
4. 觸發一次作業產生，確認新詞出現在作業詞條裡
5. 重啟 server（模擬裝置重開機），重新打開 `/teacher`，確認先前上傳的教材詞仍在（驗證 `_replay_materials` 有效）
6. 把 `network_mode` 切成 `edge`，重複步驟 2，確認離線時走規則式比對（只回既有詞，`source: "rule"`），不報錯

若步驟 5 失敗（reboot 後教材詞消失），優先檢查 `lifespan` 是否真的呼叫了 `_replay_materials()`，以及 `store.list_materials()` 回傳的 `entries` 欄位是否為非空 list。
