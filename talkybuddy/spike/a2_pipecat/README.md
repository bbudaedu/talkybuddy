# A2-1 Pipecat 整合 Spike（拋棄式 go/no-go）

隔離於獨立 venv，驗證「批次 SenseVoice + 句級可中止 sherpa」能否共存於 Pipecat 並被程式化 barge-in 句界打斷，全程不碰 pipeline._process_text。

## 建 venv（於 talkybuddy/ 執行）
    python3 -m venv spike/a2_pipecat/.venv
    spike/a2_pipecat/.venv/bin/pip install -r spike/a2_pipecat/requirements.txt

## 跑核心單元測試（真 TDD 部分）
    spike/a2_pipecat/.venv/bin/python -m pytest spike/a2_pipecat/tests -v

## 探查 Pipecat API
    spike/a2_pipecat/.venv/bin/python spike/a2_pipecat/probe_pipecat.py

## 跑整合 spike（判準 #1~#4）
    spike/a2_pipecat/.venv/bin/python spike/a2_pipecat/run_spike.py

結論見 SPIKE-RESULT.md。首次跑會下載 iic/SenseVoiceSmall（~1GB）。

## Step 0 go/no-go 結果（2026-07-08）：GO
- pipecat-ai[funasr] 在專案 Python 3.12.3 乾淨安裝、乾淨 import，無相依衝突。
- 版本：Pipecat 1.5.0 / sherpa-onnx 1.13.4 / numpy 2.4.6。
- sherpa-onnx 1.13.4 的 OfflineTts.generate 仍為含 callback 的 overload（Task 3 實測中止語意）。
- → Task 2~8（probe / TDD 核心 / Pipecat 整合 / 判準 #1~#5）可展開。
