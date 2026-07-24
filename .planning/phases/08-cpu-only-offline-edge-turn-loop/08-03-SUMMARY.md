---
phase: 08-cpu-only-offline-edge-turn-loop
plan: 03
subsystem: infra
tags: [alsa, arecord, aplay, websockets, edge, offline, wav, subprocess]

# Dependency graph
requires: []
provides:
  - "edge/runtime/audio_io.py — capture_16k_mono_wav()/play_wav_bytes()/wait_for_trigger() 統一 ALSA I/O 介面"
  - "edge/runtime/local_client.py — 離線對話 WebSocket client（沿用 /ws/talk 協定）"
  - "edge/__init__.py、edge/runtime/__init__.py — edge.runtime 可作為 Python package import/以 -m 執行"
affects: [08-04, 08-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "sounddevice-as-upgrade-path degrade idiom（try: import sounddevice / except Exception: return None，比照 CloudLLM.available()）"
    - "子行程一律固定 argv 串列，絕不 shell 字串插值（命令注入防線）"

key-files:
  created:
    - edge/runtime/audio_io.py
    - edge/runtime/local_client.py
    - tests/test_audio_io.py
    - edge/__init__.py
    - edge/runtime/__init__.py
  modified: []

key-decisions:
  - "sounddevice 僅作為授權升級路徑：_import_sounddevice() 是唯一載入邊界，import 失敗/缺 PortAudio 一律降級回 arecord/aplay，不拋例外；裝置預設不安裝 sounddevice（provision_device.sh 未列入）"
  - "local_client 的 /ws/talk token 取得方式：呼叫既有 /api/login 搭配 server/auth.py _SEED 的 device seed 帳號（device:GENIO-520-X992），可用 TALKYBUDDY_EDGE_DEVICE_EMAIL/PASSWORD 覆寫"
  - "server 就緒探測改用既有 /api/status（無 auth、啟動即回應），因 server/app.py 目前無專用 /health 路由"
  - "本回合結束訊號取 tts_audio/tts_unavailable（不是 RESEARCH.md 骨架假設的 idle——實讀 server/app.py::ws_talk 後確認該路徑不會送 idle 事件）"

requirements-completed: [ELOOP-01]

coverage:
  - id: D1
    description: "audio_io.capture_16k_mono_wav() 產出 16k mono S16_LE WAV bytes，命中 server/pipeline.py RIFF-sniff fast path"
    requirement: ELOOP-01
    verification:
      - kind: unit
        ref: "tests/test_audio_io.py#test_capture_returns_bytes_that_hit_riff_fast_path"
        status: pass
      - kind: unit
        ref: "tests/test_audio_io.py#test_capture_arecord_argv_uses_fixed_list_with_16k_mono_s16le"
        status: pass
    human_judgment: false
  - id: D2
    description: "sounddevice 不可用時自動降級 arecord/aplay，不拋例外；子行程一律固定 argv（無 shell=True/ffmpeg）"
    requirement: ELOOP-01
    verification:
      - kind: unit
        ref: "tests/test_audio_io.py#test_capture_degrades_to_arecord_when_sounddevice_unavailable"
        status: pass
      - kind: unit
        ref: "tests/test_audio_io.py#test_play_wav_bytes_does_not_raise_on_subprocess_failure"
        status: pass
      - kind: unit
        ref: "tests/test_audio_io.py#test_no_shell_true_or_ffmpeg_in_module_source"
        status: pass
    human_judgment: false
  - id: D3
    description: "local_client.py 以既有 /ws/talk 協定送收音訊（binary WAV + audio_end → tts_audio wav_b64 播放），语法正確、可作為獨立行程啟動"
    requirement: ELOOP-01
    verification:
      - kind: unit
        ref: "python -c \"import ast; ast.parse(open('edge/runtime/local_client.py').read())\""
        status: pass
      - kind: integration
        ref: "手動端到端：本機啟動 uvicorn server.app:app，local_client health-check/login/WS round-trip 均無例外（見 Issues Encountered）"
        status: pass
    human_judgment: true
    rationale: "真機（Genio 520）完整聽→想→說迴圈與喇叭/麥克風實際發聲，需留待 08-05 硬體 checkpoint 驗證，本 plan 僅驗證協定層與語法層。"

# Metrics
duration: 15min
completed: 2026-07-25
status: complete
---

# Phase 8 Plan 3: Edge Audio I/O + Offline WebSocket Turn Loop Summary

**arecord/aplay 主路徑的 ALSA 擷取/播放統一介面（sounddevice 為授權升級路徑）+ 沿用既有 /ws/talk 協定的離線對話 Python WebSocket client**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-07-25T06:50:00+08:00
- **Completed:** 2026-07-25T06:56:00+08:00
- **Tasks:** 2
- **Files modified:** 5 (3 new + 2 package `__init__.py`)

## Accomplishments
- `edge/runtime/audio_io.py`：`capture_16k_mono_wav()`/`play_wav_bytes()`/`wait_for_trigger()`，arecord/aplay 子行程為預設路徑（固定 argv 串列，零 pip 編譯風險），sounddevice 為安全降級的授權升級路徑
- `edge/runtime/local_client.py`：`async run_loop()` 沿用既有 `/ws/talk` wire protocol（binary WAV frame + `{"type":"audio_end"}` → 收 `tts_audio`/`wav_b64` 播放），不改動 `server/app.py`/`server/pipeline.py` 任何契約
- `tests/test_audio_io.py`：7 個單元測試全綠，涵蓋 argv 格式、RIFF fast path 命中、sounddevice 降級不拋、aplay 失敗不拋、原始碼層級 shell=True/ffmpeg 防線
- 全套件回歸測試（`tests/` 311 個測試）全綠，本 plan 新增內容零回歸

## Task Commits

Each task was committed atomically:

1. **Task 1: edge/runtime/audio_io.py（TDD）**
   - RED: `40c5cfa` test(08-03): add failing test for edge audio_io capture/playback
   - GREEN: `9f63150` feat(08-03): implement edge audio_io ALSA capture/playback
2. **Task 2: edge/runtime/local_client.py** - `3a6a59d` feat(08-03): implement edge local_client offline WS turn loop

_Note: Task 1 為 TDD 任務，RED（test 全紅，`ImportError: cannot import name 'audio_io'`）先於 GREEN（實作後 7/7 綠）提交，符合 Plan-Level TDD Gate。_

## Files Created/Modified
- `edge/runtime/audio_io.py` - ALSA 擷取/播放統一介面（arecord/aplay 主、sounddevice 授權升級路徑）
- `edge/runtime/local_client.py` - 離線對話 WebSocket client 主迴圈
- `tests/test_audio_io.py` - audio_io 單元測試（7 tests，不需真麥克風/喇叭）
- `edge/__init__.py` - 新增，使 `edge` 成為可 import 的 Python package
- `edge/runtime/__init__.py` - 新增，使 `edge.runtime` 成為可 import 的 Python package（支援 `python3 -m edge.runtime.local_client`）

## Decisions Made
- `sounddevice` 僅作為授權升級路徑：`_import_sounddevice()` 是唯一載入邊界，import 失敗（未安裝）或 `OSError`（缺 PortAudio）一律靜默降級回 `arecord`/`aplay`、不拋例外；裝置端 `provision_device.sh` 目前未安裝 `sounddevice`，符合 T-08-SC 的「預設路徑不安裝任何新套件」緩解方案
- `local_client.py` 取得 `/ws/talk` 合法 token 的方式：呼叫既有 `/api/login` REST 端點，帶 `server/auth.py` 既有 `_SEED` 中的 `device:GENIO-520-X992` 裝置帳號；帳密可用 `TALKYBUDDY_EDGE_DEVICE_EMAIL`/`TALKYBUDDY_EDGE_DEVICE_PASSWORD` 環境變數覆寫，不硬編死值
- server 就緒探測改用既有 `/api/status`（免 auth、啟動即可回應）而非 RESEARCH.md 骨架假設的 `/health` 路由——`server/app.py` 目前沒有專用 `/health` 端點，讀原始碼後確認 `/api/status` 是最接近的既有就緒訊號
- 一輪對話結束訊號實作為收到 `tts_audio` 或 `tts_unavailable` 即跳出接收迴圈（而非 RESEARCH.md Pattern 1 骨架假設的 `"idle"` 事件）——實讀 `server/app.py::ws_talk::send_turn_result` 後確認該路徑只送 `asr_result`→`reply`→`tts_audio`/`tts_unavailable`，沒有額外的 `idle` 訊號

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Docstring 意外命中 verification 的字面 grep 檢查（`ffmpeg`/`shell=True` 字樣）**
- **Found during:** Task 1（GREEN 階段，跑 `grep -n "shell=True\|ffmpeg" edge/runtime/audio_io.py` 驗收指令）
- **Issue:** 為了說明「絕不用 shell=True」「不呼叫 ffmpeg」而在 docstring/comment 中寫出這兩個字面字串，導致 plan 明訂的 grep-based 驗收（`grep -n "ffmpeg" edge/runtime/audio_io.py` 應無輸出）失敗——這是文件層級誤踩驗收自身条件的 bug，而非邏輯錯誤
- **Fix:** 改寫 docstring 用語（「絕不使用 shell 字串插值模式」「不呼叫任何外部音訊轉檔工具」），避免出現這兩個字面字串，同時保留原意清晰度
- **Files modified:** `edge/runtime/audio_io.py`
- **Verification:** `grep -n "shell=True\|ffmpeg" edge/runtime/audio_io.py` 確認無輸出；`tests/test_audio_io.py::test_no_shell_true_or_ffmpeg_in_module_source` 綠
- **Committed in:** `9f63150`（併入 Task 1 GREEN commit，未產生額外 commit）

**2. [Rule 1 - Bug] `websockets` 16.0 的 `ConnectionClosed` 屬性路徑與骨架假設不符**
- **Found during:** Task 2（手動端到端驗證，import 模組時）
- **Issue:** 初版程式碼寫 `websockets.exceptions.ConnectionClosed`，但已安裝的 `websockets==16.0` 版本此屬性路徑會拋 `AttributeError: module 'websockets' has no attribute 'exceptions'`（新版 lazy-import 機制）；改用頂層 `websockets.ConnectionClosed`（該版本仍導出）
- **Fix:** 改為 `except websockets.ConnectionClosed:`
- **Files modified:** `edge/runtime/local_client.py`
- **Verification:** `python -c "import edge.runtime.local_client"` 成功；手動端到端測試（見下）WS 連線/斷線均正常
- **Committed in:** `3a6a59d`（Task 2 唯一 commit，修正在提交前完成）

---

**Total deviations:** 2 auto-fixed（2 bug，皆在提交前修正，無額外 commit）
**Impact on plan:** 兩項修正皆為驗收指令/依賴版本層級的必要修正，不影響架構或範圍，無 scope creep。

## Issues Encountered
- 手動端到端驗證：在本機以 `TALKYBUDDY_PIPELINE_PROFILE=edge` 啟動 `uvicorn server.app:app --host 127.0.0.1 --port 18787`，monkeypatch `audio_io.capture_16k_mono_wav`/`play_wav_bytes`/`wait_for_trigger`（避免依賴真麥克風/喇叭），跑 `local_client.wait_for_server_ready()` → `fetch_token()` → `_handle_turn(ws)` 全程無例外；server 端 log 顯示 `WebSocket /ws/talk?token=... [accepted]` → `connection open` → `connection closed` 乾淨結束（本機 ASR/TTS 引擎未預熱模型權重，回覆走 `tts_unavailable` 分支，但這驗證的是本 plan 負責的協定層，非 ASR/TTS 品質，符合 plan 範圍）。真機喇叭/麥克風實際發聲與完整 ASR→LLM→TTS 品質留待 08-04/08-05。

## User Setup Required
None - no external service configuration required.（真機部署與硬體驗證見 08-04/08-05）

## Next Phase Readiness
- `edge/runtime/audio_io.py`/`local_client.py` 已就緒，可供 08-04（llama-server 整合／run_edge.sh 啟動順序）與 08-05（真機 checkpoint:human-verify）直接引用
- 08-05 執行時需確認裝置端 `run_edge.sh` 啟動 `local_client.py` 的順序需晚於 uvicorn health-check 通過（`wait_for_server_ready()` 已內建輪詢，但 08-05 的 shell 啟動腳本仍需正確串接兩個行程）
- 若未來確需啟用 `sounddevice`（例如量測發現 arecord 子行程延遲不可接受），需先過 08-05 的 blocking-human 套件核可（T-08-SC），程式碼層面已就緒（`_import_sounddevice()` 自動偵測）

---
*Phase: 08-cpu-only-offline-edge-turn-loop*
*Completed: 2026-07-25*
