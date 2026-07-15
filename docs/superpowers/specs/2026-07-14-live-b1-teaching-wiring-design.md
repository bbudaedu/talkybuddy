# 設計：把 B1/B3 教學內容接進 live 對話（解決重複亂聊）

- 日期：2026-07-14
- 分支：`feat/cloud-llm-bedrock`（worktree `cloud-llm`）
- 母脈絡：`talkybuddy-nova-sonic-live`、`talkybuddy-b-axis`

## 1. 問題與根因

現象：live hands-free 對話「一直重複亂聊」——沒有教學方向、每次都用類似開場、漫談。

根因（已驗證，現況程式碼）：

- `ws_live`（`server/app.py:429-431`）在連線時**一次性**組 system prompt：
  - `directive = getattr(pipeline, "_directive", None)`
  - `target = "How are you today?"`（寫死）
- `pipeline._directive` **只在半雙工路徑被寫入**：`/api/network_mode` cloud（`app.py:219`）與 `pipeline._refresh_directive`（每 5 個成功半雙工回合，`pipeline.py:275`）。
- 純 live 場次全程**不跑診斷、不刷新 directive** → `_directive` 恆為 `None` → Nova 拿通用 prompt、無 B1 策略/課程 → 亂聊。
- 且 live 場次即使記錄了逐字稿（`_store_live_turn`，source=`live_s2s`），也**不會回產診斷**，導致跨場次難度/主題不會隨孩子進步而調整。

結論：**live 能讀 directive，但沒人餵；且 target 句寫死；且 live 練習不回寫診斷。** B1/B3 機器都已存在，只差把它接進 live 這條線。

## 2. 目標與非目標

目標：

- 純 live hands-free 場次也能拿到「今日主題＋今日目標句＋B1 策略」。
- Nova 以**教練角色跑跟讀迴圈**（帶讀→請跟讀→回饋→鼓勵→換句），不再亂聊。
- **每場對話開始算一次**教學內容（喚醒迴圈每場重連＝天然自適應邊界）。
- live 練習**回寫診斷**，讓下一場難度/主題隨進步漸進（閉環）。

非目標（本設計明確不做）：

- 聲學發音評測（allosaurus / wav2vec2 GOP 等）＝另開 session 處理。
- 場中即時更新 Nova system prompt（Nova system prompt 為連線一次性、bidi 不保證中途重送生效）＝不做，靠每場重連自適應。
- 新課程資料模型／新句庫＝不做，重用既有 `scaffold.VOCAB`。

## 3. 架構：元件與邊界

### 元件① 新檔 `server/lesson.py`（選教材，純函式為主、離線可測）

職責：由「最新診斷＋profile」決定這一場要教的主題、目標句與策略字串。與 WS/Nova 無耦合，可離線單元測試。

- `pick_target_sentence(topic, profile) -> str`
  - 從 `scaffold.VOCAB` 篩 `cat == topic` 的詞，優先挑 `profile.learning_vocab`（未熟）對應的詞，取其 `sent` 例句。
  - 該類無未熟詞 → 取該類第一個詞的 `sent`。
  - 完全無對應（未知 topic / VOCAB 缺）→ 安全預設通用引導句（例：`"How are you today?"`）。
  - 純函式、不炸。

- `build_lesson(diagnoses, profile) -> Lesson`
  - 取最新診斷 `diagnoses[-1]`（若有）→ `companion_directive` + `level_state`。
  - `directive = diagnose.format_directive_for_prompt(companion_directive, level_state)`；無診斷 → `None`。
  - `topic / target_form` 取自 `level_state`；**冷啟動（無診斷/缺 level_state）→ `curriculum` 預設**（band 1 → `TOPIC_ORDER[0]`＝`animal`、`_TARGET_FORM[1]`）。
  - `target_sentence = pick_target_sentence(topic, profile)`。
  - 回傳 `Lesson(topic, target_sentence, target_form, directive)`（dataclass）。
  - **全程 try/except → 任何失敗回安全預設 Lesson，永不擋 live。**

### 元件② `scaffold.build_live_system_prompt` 重寫 ＋ `_LIVE_STATIC_FRAME` 教練化

- 新簽名：`build_live_system_prompt(target_sentence, directive, topic=None)`。
  - 唯一呼叫者是 `app.py`；`target_form` 已含在 `directive` 字串（`format_directive_for_prompt` 的 CEFR clause），不重複傳。
  - 對 `None`/空 `target_sentence`/`directive` 向後相容（沿用既有行為）。
- `_LIVE_STATIC_FRAME` 由「泛帶讀」升級為**教練角色＋跟讀迴圈**：
  1. 角色：孩子的英文說話教練「說說學伴」，溫暖有耐心、以繁中為主、帶讀短英文；保留現有「明顯放慢咬字」指示。
  2. 這一場任務：帶孩子練**今天的主題＋今天的目標句**，用跟讀（shadowing）方式。
  3. 跟讀迴圈（每輪）：① 中文自然引出情境 → ② 清楚放慢說一句短英文（目標句或其變化）→ ③ 邀請孩子跟說一次 → ④ 具體誇獎＋溫柔修正一兩個發音/用詞＋再示範一次 → ⑤ 跟上→換下一句/延伸；卡住→拆更短、放更慢、再帶一次（降階護信心）。
  4. 禁止：漫無目的閒聊、每次同一句開場、一次丟太多、長篇。
- 組裝順序：`[_LIVE_STATIC_FRAME, CHILD_SAFETY_CLAUSE, 今日主題句(topic), 今日目標句(target_sentence), directive]`（空欄位略過）。

### 元件③ `app.py` `ws_live` 接線（取代 429–431 寫死）

```python
lesson = lesson.build_lesson(store.list_diagnoses(), store.get_profile())
system_prompt = scaffold.build_live_system_prompt(
    lesson.target_sentence, lesson.directive, topic=lesson.topic)
```

`_store_live_turn`（逐字稿落地，source=`live_s2s`）維持不動＝記錄。

### 元件④ 場末背景診斷 → 供下一場自適應（納入）

- live session 收尾時**背景（不 await）**跑 `generate_diagnosis(recent live interactions)` → `store.add_diagnosis(...)`。
- 效果：補上 live 路徑缺的「產診斷」那條線，讓**下次喚醒**的 `build_lesson` 讀到新診斷 → 難度/主題隨練習漸進（B1/B3 閉環在 live 也轉起來）。
- 等效於半雙工既有 `pipeline._refresh_directive`，只是移到 live 收尾。
- try/except，失敗 logged、不影響串流關閉。
- 提醒：live 逐字稿 scores 為空，診斷靠 LLM/規則從逐字稿重算，發音維度偏粗（由另開 session 的聲學評測日後升級，不衝突）。

## 4. 資料流

```
喚醒/點企鵝 → /ws/live 連線
  build_lesson(list_diagnoses(), get_profile())
    → directive(最新診斷.companion_directive + level_state)
    → topic / target_form(level_state；冷啟動用 curriculum 預設)
    → pick_target_sentence(topic, profile) ← scaffold.VOCAB.sent
  build_live_system_prompt(target_sentence, directive, topic)  [教練+跟讀迴圈]
  Nova session 整場用此 prompt
  每回合: _store_live_turn (source=live_s2s)            ← 記錄(已有)
  session 結束(背景): generate_diagnosis(recent live)
                      → add_diagnosis                    ← 供下一場自適應(元件④)
```

## 5. 錯誤處理

- `build_lesson` / `pick_target_sentence` 全 try/except → 安全預設 Lesson，live 永不因選教材失敗中斷。
- `build_live_system_prompt` 對 `None`/空欄位向後相容。
- 元件④場末診斷失敗 → logged，不影響關閉（沿用既有 except pattern）。

## 6. 測試（TDD，皆離線可跑）

- 新 `tests/test_lesson.py`：
  - `pick_target_sentence`：topic→句；未熟優先；缺 profile；未知 topic 退化通用句。
  - `build_lesson`：有最新診斷→帶 directive+topic+target；冷啟動無診斷→ curriculum 預設；缺 level_state→退化不炸。
- 更新 `tests/test_scaffold_live_prompt.py`：新 prompt 含教練角色關鍵字、跟讀迴圈、target_sentence、topic 介紹；directive 折入；`None` 參數向後相容。
- 更新 `tests/test_app_live.py`：
  - monkeypatch 驗 `ws_live` 用 `build_lesson` 的 target/directive 建 prompt。
  - 驗場末背景診斷被觸發（元件④）。

## 7. 動到的檔

- 新：`server/lesson.py`、`tests/test_lesson.py`
- 改：`server/scaffold.py`（`_LIVE_STATIC_FRAME` + `build_live_system_prompt`）、`server/app.py`（`ws_live` 接線 + 場末背景診斷）
- 改測試：`tests/test_scaffold_live_prompt.py`、`tests/test_app_live.py`

## 8. 手動驗證步驟

- 單元：`.venv/bin/pytest tests/test_lesson.py tests/test_scaffold_live_prompt.py tests/test_app_live.py -q` 全綠。
- 端到端（真機，沿用喚醒 e2e checklist）：喊「說說學伴」→ Nova 開場即**介紹今日主題並帶讀目標句、邀請跟讀**（非泛聊）；連續兩場之間，若第一場練得順，第二場主題/難度應可見漸進（元件④）。
