# 離線邊緣中英雙語語音 — 本機實測報告

> 日期：2026-07-07　環境：PC x86 CPU（非 Genio 520）　執行：`talkybuddy/bench/*.py`
> 定位：驗證+擴充既有選型（ASR=SenseVoice / TTS=sherpa-onnx+Piper / LLM=Qwen2.5-1.5B）

## 方法與限制（先讀）

- **語料**：用現有 TTS 合成 10 句中英雙語（含破音字/數字/中英夾雜），**原文即 ground truth**，餵給 ASR 算 CER。
- **合成語音 ≠ 真實兒童語音**：TTS 是「標準發音」，測不出真實兒童的口音、發音不標準、語速忽快忽慢、背景噪音。`speed=1.25×` 僅近似兒童快語速。
- **數字定位**：不同方案在**同一組合成語料上的相對表現與延遲**，非真實兒童準確率。真實兒童語音仍建議日後錄音補測。
- **延遲是 PC x86 熱延遲**，Genio 520 NPU 實機延遲須上板才知。
- **TTS 音色自然度**須人耳聽 `bench/out/*.wav`，本報告不主觀評分。

## 一、ASR / STT

| 指標 | SenseVoice（現用） | faster-whisper small |
|---|---|---|
| 平均 CER | **2.4%** | 5.2% |
| 中文 CER | **2.8%** | 6.8% |
| 英文 CER | 3.0% | **2.0%** |
| 平均延遲 | **91 ms** | 384 ms（慢 4.2×） |

- SenseVoice 破音字全對（重新讀 chóng、長大/長得）；whisper 中文出現同音錯（讀一「篇」、長「的」很高）。
- whisper 英文略優，但中文差 + 延遲慢 4 倍，對「中文為主的邊緣玩偶」不划算。
- **裁決：維持 SenseVoice（信心高）。** whisper 僅作既有 fallback，無需扶正。

## 二、TTS

| 指標 | sherpa-onnx+Piper（現用） | Kokoro 多語 |
|---|---|---|
| 平均延遲 | **184 ms** | 705 ms（慢 3.8×） |
| 音色數 | 單一 voice | **53 speakers** |
| 授權 | Apache runtime | Apache，但同樣 espeak-ng 音素化 |

- Kokoro 音色選擇多（利於找童聲/親和音色），但延遲近 4 倍、**espeak-ng GPL 殘留兩者皆未解**。
- **裁決：即時走 sherpa-onnx 現用（延遲優）；Kokoro 列為「童聲增補候選」**，待人耳比較 `bench/out/kokoro_*.wav` 決定是否值得換延遲。

## 三、邊緣 LLM

| 指標 | Qwen2.5-1.5B（現用） | Qwen3-1.7B |
|---|---|---|
| 平均延遲 | **4.56 s** | 5.12 s（慢 12%） |
| tok/s | 19.1 | 18.9 |
| 對話品質 | 簡短、親切、到位 | 引導更豐富、台灣用語自然 |
| 特性 | 純對話 | **hybrid thinking mode** |

- 兩者即時對話品質都好；Qwen3 引導深度略勝但更慢。
- **裁決：即時對話維持 Qwen2.5（延遲優）；Qwen3 列增補**——教案/鷹架等非即時場景可開 thinking 提升規劃品質。
- ⚠️ **最大痛點：LLM 4–6 秒延遲**（ASR/TTS 皆毫秒級，瓶頸在 LLM）。雲端優先架構（research/08）正好用雲端強 LLM 消除此延遲，本地僅斷網簡單功能。

## 總結

| 類別 | 裁決 | 增補候選 |
|---|---|---|
| ASR/STT | **維持 SenseVoice** | —（whisper 續作 fallback） |
| TTS | **維持 sherpa-onnx** | Kokoro（童聲，需人耳定奪 sid） |
| LLM | **本地 fallback 建議改 Qwen2.5-0.5B**（延遲 4.6s→1.9s） | Qwen2.5-1.5B（較完整）、Qwen3（thinking/非即時） |

- ASR/TTS 既有選型站得住，與 research/03~04 裁決一致。
- **LLM 是唯一有實質改進空間者**：0.5B 把本地延遲砍半且品質對簡單功能足夠，直接改善雙網混成的本地 fallback 體驗。
- 最該關注的不是「換更強的模型」，而是 **LLM 本地延遲**——雙網混成（雲端優先 + 本地用 0.5B）兩路並進解此痛點。

## 待補（本機測不到）

1. 真實兒童中英雙語錄音 → 算真實 CER（合成語料無法反映）。
2. Genio 520 上板實測 NPU 量化延遲（三類皆是）。
3. Kokoro 童聲 vs 現用 voice 的人耳 A/B。
4. Qwen3 thinking-on 在教案規劃的品質增益量化。

## 復現

```
talkybuddy/.venv/bin/python bench/bench_baseline.py        # 三件套 baseline
talkybuddy/.venv/bin/python bench/bench_asr_challenger.py  # SenseVoice vs whisper
talkybuddy/.venv/bin/python bench/bench_challengers2.py    # Qwen3 + Kokoro
```
產出：`bench/out/results_*.json`、各句 `.wav`。
