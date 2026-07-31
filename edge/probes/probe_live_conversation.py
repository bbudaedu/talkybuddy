# -*- coding: utf-8 -*-
"""真人一輪：對著玩偶說話，走完整條 pipecat pipeline。

```
麥克風(arecord) → VAD → SenseVoice STT → 教材注入 → llama-server
      → 安全閘門 → 帶讀護欄 → 邊緣 TTS → 簡轉繁 → 喇叭(aplay)
```

## 跑之前要知道的三件事

1. **會佔用麥克風**。`talkybuddy-local-client` 是 active 的、用同一支麥克風，
   跑這支的時候**不要按玩偶的按鍵**，否則兩個行程搶麥（`38aa261`），
   症狀跟麥克風壞掉一模一樣。腳本啟動前會檢查，結束後會確認釋放。

2. **已加 half-duplex 閘門**。喇叭與麥克風同在玩偶內、板子裝不了 AEC
   （見記憶 `project-edge-s2s-tuning`）。2026-07-31 真人實測，不加閘門時
   **自我打斷 4 次**——玩偶把自己的聲音判成使用者開口。現已掛上
   `AlwaysUserMuteStrategy`（玩偶講話時一律不聽）。代價是孩子**無法插話打斷**，
   與現行 `PlaybackGate` 的取捨相同。

3. **對話無狀態**。llama-server `--ctx-size 512`，累積歷史會直接爆
   （實測 516→579→642 tokens）。`StatelessContextProcessor` 每輪把 context
   清成只剩 system，與現行 `EdgeLLM.generate` 的行為一致。代價是玩偶不記得
   上一輪。

4. **不會動決賽路徑**。全部跑在 `/root/pipecat-lab/`，用的是同一份模型檔（symlink）。

## 用法

    cd /root/pipecat-lab
    PYTHONPATH=/root/pipecat-lab ./.venv/bin/python probe_live_conversation.py [秒數]

預設 60 秒，Ctrl-C 可提前結束。

## 換成雲端大腦

    TALKYBUDDY_PIPECAT_CLOUD=1 \
    TALKYBUDDY_CLOUD_PROVIDER=bedrock BEDROCK_REGION=<region> \
    PYTHONPATH=/root/pipecat-lab ./.venv/bin/python probe_live_conversation.py

（或用 Anthropic 相容端點：`ANTHROPIC_API_KEY` ＋可選 `ANTHROPIC_BASE_URL`／
`ANTHROPIC_MODEL`。先用 `probe_cloud_llm_service.py` 確認那條路通了再跑這支，
那支不佔麥克風、失敗成本低得多。）

三件事刻意這樣設計：

1. **預設不變**。沒設 `TALKYBUDDY_PIPECAT_CLOUD` 就是原本那條真人驗證過的
   edge 路徑，一行都沒動。
2. **設了但憑證不全會直接報錯退出**，不會靜默跑成 edge。「以為在跑雲端、
   其實沒有」是這個專案被咬過三次的坑（見 `server/cloud_llm.py` 的 docstring）。
3. **雲端失敗是當輪降級**，不換 pipeline 節點、不轉移麥克風所有權——最壞的
   結果是這一輪回答比較笨，不是玩偶不會講話。

結束時會印 `雲端實際走的`，那是**證據**（真的成功過才不是 `none`），
不是設定讀數。現場要佐證「大腦在雲端」就看那一行。
"""

import asyncio
import os
import subprocess
import sys
import time

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import WorkerRunner
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.turns.user_mute.always_user_mute_strategy import AlwaysUserMuteStrategy

from edge.runtime.live_client import PlaybackGate
from edge.runtime.pipecat_adapters.alsa_transport import AlsaTransport, AlsaTransportParams
from edge.runtime.pipecat_adapters.cloud_llm_service import CloudLLMService
from edge.runtime.pipecat_adapters.edge_tts import EdgeVitsTTSService
from edge.runtime.pipecat_adapters.turn_recorder import TurnRecorderProcessor
from edge.runtime.pipecat_adapters.lesson_progress import (
    LessonProgress,
    LessonProgressProcessor,
)
from edge.runtime.pipecat_adapters.lesson_prompt import LessonPromptInjector
from edge.runtime.pipecat_adapters.opencc_processor import OpenCCProcessor
from edge.runtime.pipecat_adapters.playback_gate import (
    PlaybackGateFilter,
    PlaybackGateSink,
)
from edge.runtime.pipecat_adapters.readalong_guard import ReadalongGuardProcessor
from edge.runtime.pipecat_adapters.safety_gate import SafetyGateProcessor
from edge.runtime.pipecat_adapters.sensevoice_stt import SenseVoiceSTTService
from edge.runtime.pipecat_adapters.stateless_context import StatelessContextProcessor

MIC_DEVICE = "plughw:1,0"
SPEAKER_DEVICE = "plughw:0,0"
STT_RATE = 16000
TTS_RATE = 22050
LLAMA_BASE_URL = "http://127.0.0.1:8080/v1"
TARGET_SENTENCE = "I want an apple."

# 走雲端大腦要**明確打開**，預設仍是已經真人驗證過的純 edge 路徑。
# 刻意不做「偵測到憑證就自動切」：這條 probe 是用來判斷玩偶行為的，
# 大腦悄悄換人會讓所有觀察失去意義。設了但憑證不全時直接報錯退出，
# 不靜默跑成 edge——「以為在跑雲端、其實沒有」正是這個專案被咬過三次的坑。
CLOUD_ENV = "TALKYBUDDY_PIPECAT_CLOUD"

# 每則回覆字數上限（半雙工：這等於孩子不能開口的時間）。約 9 秒。
LIVE_MAX_CHARS = 40

try:
    from server.llm import EdgeLLM

    SYSTEM_PROMPT = EdgeLLM._SYSTEM_PROMPT
except Exception:
    SYSTEM_PROMPT = "你是陪伴孩子學英文的玩偶。用一句話回答。"


class Narrator(FrameProcessor):
    """把對話過程即時印出來，讓人看得懂玩偶在想什麼。"""

    def __init__(self, tag: str):
        super().__init__()
        self._tag = tag
        self._llm_buf: list[str] = []
        self.turns = 0
        self.audio_chunks = 0
        self.said: list[str] = []
        self.self_interrupts = 0
        self._bot_speaking_since: float | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStartedSpeakingFrame):
            if self._bot_speaking_since is not None:
                self.self_interrupts += 1
                print("   ⚠️  玩偶還在講話時偵測到「使用者開始說話」——可能是聽到自己的聲音")
            print("🎤 偵測到你開始說話…")
        elif isinstance(frame, UserStoppedSpeakingFrame):
            print("🎤 你說完了，辨識中…")
        elif isinstance(frame, TranscriptionFrame):
            original = frame.result if isinstance(frame.result, str) else frame.text
            print(f"👂 聽成：{original}")
            self.turns += 1
        elif isinstance(frame, LLMTextFrame):
            self._llm_buf.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            text = "".join(self._llm_buf).strip()
            if text:
                print(f"🗣  玩偶說：{text}")
                self.said.append(text)
            self._llm_buf.clear()
        elif isinstance(frame, TTSAudioRawFrame):
            if self.audio_chunks == 0 or self._bot_speaking_since is None:
                self._bot_speaking_since = time.perf_counter()
                text = "".join(self._llm_buf).strip()
                if text:
                    print(f"🗣  玩偶說：{text}")
                self._llm_buf.clear()
            self.audio_chunks += 1
        await self.push_frame(frame, direction)


def _pids(name: str) -> list[str]:
    r = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True)
    return [p for p in r.stdout.split() if p]


def _child_memory():
    """開場取一次孩子畫像；沒有資料或取不到都回 None（不假裝認識他）。

    刻意只在開場呼叫一次：對話路徑的預算是 CLOUD_LLM_TIMEOUT_S（1.5s），
    多一次 I/O 就少一分餘裕。畫像進了 system prompt 之後，每輪成本是零。
    """
    try:
        from server import child_brief, store

        store.init_db()
        return child_brief.build_child_brief(
            store.get_profile(), store.list_due_word_reviews(store.default_student_id()),
            store.list_diagnoses(),
        )
    except Exception:
        logger.warning("取不到孩子畫像，玩偶用預設開場（對話仍可進行）")
        return None


def _refresh_profile() -> str | None:
    """對話結束後重算長期 profile；回一句可印的結果或 None。

    這是記憶迴圈的最後一環。少了它，互動紀錄只是躺在資料庫裡的原始資料，
    `child_brief` 下次還是組不出東西——玩偶永遠是第一次見到這個孩子。

    沿用 `server/app.py:391` 同步時的做法（全量重算 + save_profile），
    只是把時機從「同步」改成「對話結束」。非同步、慢一點無所謂，
    絕不能影響剛才那場對話。
    """
    try:
        from server import profile as profile_mod, store

        prof = profile_mod.build_profile(
            store.list_interactions(limit=500), store.list_diagnoses(),
            store.get_profile(),
        )
        store.save_profile(prof)
        return (
            f"互動 {prof.get('interaction_count', 0)} 次｜"
            f"正在學 {len(prof.get('learning_vocab') or [])} 個字｜"
            f"已熟 {len(prof.get('mastered_vocab') or [])} 個字"
        )
    except Exception:
        logger.exception("profile 重算失敗（不影響剛才那場對話）")
        return None


def _todays_lesson():
    """從既有的 SQLite 取本場教材；任何失敗回 None，退回寫死的預設。

    `server/lesson.py::build_lesson` 依最新診斷 + 學生 profile 從
    `scaffold.VOCAB` 挑主題與目標句，並給出本輪教學策略（directive）。
    這條 probe 過去寫死 `I want an apple.`——那是 2026-07-31 真人實測
    「四輪回覆幾乎一模一樣」的原因之一。

    一場只取一次：診斷資料不會在對話中途變，每輪重讀只是白花 I/O。
    """
    try:
        from server import lesson as lesson_mod, store

        # init_db 是冪等的（CREATE TABLE IF NOT EXISTS）。少了這行，全新的
        # lab 目錄第一次跑會拿到 `no such table: diagnoses` 而靜默退回寫死的
        # 目標句——症狀是「教材功能好像沒接上」，很難聯想到是資料庫沒建表。
        store.init_db()
        return lesson_mod.build_lesson(store.list_diagnoses(), store.get_profile())
    except Exception:
        logger.warning("取不到今日教材，改用寫死的預設目標句（對話仍可進行）")
        return None


def _build_llm(lesson=None, progress=None):
    """組出這一跑要用的大腦，回 `(service, cloud_or_None, 一句話說明)`。

    雲端關閉時回傳與過去完全相同的 `OpenAILLMService`——那條路徑已經真人
    驗證過 5 輪，不該因為加了雲端而動到。
    """
    if (os.environ.get(CLOUD_ENV) or "").strip() not in ("1", "true", "yes"):
        return (
            OpenAILLMService(model="qwen", api_key="none", base_url=LLAMA_BASE_URL),
            None,
            "本機 llama-server（edge）",
        )

    from server.cloud_llm import CloudLLM
    from server.llm import EdgeLLM as _EdgeLLM

    cloud = CloudLLM()
    if not cloud.available():
        raise SystemExit(
            f"❌ 設了 {CLOUD_ENV} 但沒有可用的雲端設定：\n"
            f"   {cloud.status_detail()}\n"
            "   寧可現在就停，也不要靜默跑成 edge 卻以為在跑雲端。"
        )
    # fallback 用 EdgeLLM 而不是上面那顆 OpenAILLMService：當輪降級發生在
    # service 內部，換不了 pipeline 上的節點，所以要一個同形狀的可呼叫物件。
    edge = _EdgeLLM()
    # warmup=False：這裡改由 main() 在印出「開始了」**之前**明確暖機。
    # service 內建的暖機發生在 pipeline 啟動時，而本 probe 是先印提示、
    # 才啟動 pipeline——孩子看到提示就開口，第一輪會排在還沒飛完的暖機後面。
    target = (lesson.target_sentence if lesson else None) or TARGET_SENTENCE
    directive = lesson.directive if lesson else None
    topic = lesson.topic if lesson else None

    # 雲端走「即時陪聊」契約：教練企鵝 prompt、看得到對話歷史、不強制帶讀。
    # 那份 prompt 明寫「孩子如果問你別的，一定要先回應他…絕對不可以假裝沒聽到
    # 孩子的話」，正是回合式契約下四輪回覆一模一樣的解藥。`server/app.py` 的
    # /ws/live 一直是這樣跑的——這裡是接上既有契約，不是新發明。
    brief = _child_memory()
    if brief:
        logger.info("已載入孩子畫像（{} 字）", len(brief))

    def _live_system() -> str:
        from server import scaffold

        current = (progress.current if progress else None) or target
        # max_chars：玩偶講話時孩子的麥克風是關的（半雙工），所以回覆長度
        # 直接等於「孩子不能開口的秒數」。實測不加這個上限會講到 76 字≈17 秒。
        try:
            from server import lesson as _lm

            more = _lm.topic_sentences(topic, limit=5) if topic else []
        except Exception:
            more = []
        return scaffold.build_live_system_prompt(
            current, directive, topic, max_chars=LIVE_MAX_CHARS,
            child_brief=brief,
        )

    service = CloudLLMService(
        cloud=cloud,
        fallback=edge.generate_from_prompt,
        target_provider=lambda: (progress.current if progress else None) or target,
        system_provider=_live_system,
        warmup=False,
    )
    return service, cloud, f"雲端 {cloud.configured_backend()}（失敗當輪降級回 llama-server）"


async def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0

    busy = _pids("arecord")
    if busy:
        print(f"❌ 已有 arecord 在跑（pid {busy}）——很可能是 local-client 正在錄音。")
        print("   請等它結束，或先確認沒有人在按玩偶按鍵。")
        return 2

    print("載入模型中（SenseVoice 約 2 秒、TTS voice 約 2 秒）…")
    from server.tts import TTSEngine

    tts_engine = TTSEngine()
    tts_engine.synth([("zh", "暖機")])  # 把冷啟動吃掉，不要讓第一輪特別慢

    transport = AlsaTransport(
        AlsaTransportParams(
            audio_in_enabled=True,
            audio_in_sample_rate=STT_RATE,
            input_device=MIC_DEVICE,
            audio_out_enabled=True,
            audio_out_sample_rate=TTS_RATE,
            output_device=SPEAKER_DEVICE,
        )
    )
    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer())
    stt = SenseVoiceSTTService(sample_rate=STT_RATE)
    lesson = _todays_lesson()
    # 「孩子會了就換下一句」由狀態機決定，不由模型在 prompt 裡數數。
    # 決賽鏡頭 1 只有 60 秒約 3～4 輪，交給模型判斷會慢到台上換不了句子
    # （實測要第 7 輪）。見 lesson_progress 的 docstring。
    try:
        from server import lesson as _lm

        _sents = _lm.topic_sentences(lesson.topic, limit=5) if lesson else []
    except Exception:
        _sents = []
    progress = LessonProgress(_sents or [])
    llm, cloud, brain_desc = _build_llm(lesson, progress)
    # 教材決定目標句；取不到就用寫死的預設（對話仍可進行）。
    target_sentence = (
        progress.current or (lesson.target_sentence if lesson else None) or TARGET_SENTENCE
    )
    lesson_directive = lesson.directive if lesson else None
    tts = EdgeVitsTTSService(engine=tts_engine)
    narrator = Narrator("out")
    narrator_in = Narrator("in")
    narrator_llm = Narrator("llm")   # LLMTextFrame 會被 TTS 消費，探針必須在 TTS 之前
    # 上下行共享同一個 gate：sink 記下播放時長，filter 立刻據此關閘。
    gate = PlaybackGate(rate=TTS_RATE)

    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    # AlwaysUserMuteStrategy：玩偶講話時一律不聽使用者。
    # 2026-07-31 真人實測，沒有它會自我打斷 4 次——喇叭與麥克風同在玩偶內、
    # 板子裝不了 AEC，玩偶會把自己的聲音判成使用者開口。
    agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_mute_strategies=[AlwaysUserMuteStrategy()]
        ),
    )

    worker = PipelineWorker(
        Pipeline(
            [
                transport.input(),
                PlaybackGateFilter(gate),   # 玩偶講話時上行換靜音，攔在 VAD 之前
                vad,
                stt,
                narrator_in,        # 探針要在 agg.user() 之前，否則看不到逐字稿
                # 無狀態只留給 edge：llama-server --ctx-size 512 塞不下歷史
                # （實測 516→579→642 tokens 就爆）。雲端 context 遠大於此，
                # 留著它玩偶就永遠不記得上一輪——那正是要修的單調問題。
                *([] if cloud is not None else
                  [StatelessContextProcessor(context=context)]),
                # 走雲端才遮個資：edge 是本機推論，孩子的話沒有離開玩偶，
                # 遮了只會讓 llama-server 看到 [名字] 而降低回覆品質。
                # 進度觀察要在教材注入**之前**：後者會把逐字稿覆寫成整段 prompt。
                LessonProgressProcessor(progress),
                LessonPromptInjector(
                    lesson_provider=lambda: (
                        progress.current or target_sentence, lesson_directive
                    ),
                    deidentify=cloud is not None,
                ),
                agg.user(),
                llm,
                SafetyGateProcessor(),
                # allow_variation 只在雲端打開：即時陪聊契約下玩偶可以順著
                # 孩子換句子（孩子說想練貓，帶讀 I see a cat. 是對的）。
                # edge 仍要嚴格對齊教材的目標句。
                ReadalongGuardProcessor(
                    target_provider=lambda: progress.current or target_sentence,
                    allow_variation=cloud is not None,
                ),
                # 落地要在 LLM 之後（才看得到玩偶說了什麼）。孩子的原話由
                # LessonProgress 保留——TranscriptionFrame 在上游已被
                # LessonPromptInjector 覆寫成整段 prompt。
                TurnRecorderProcessor(
                    student_text_provider=lambda: progress.last_utterance
                ),
                narrator_llm,
                tts,
                PlaybackGateSink(gate),     # 記錄下行時長給 gate
                OpenCCProcessor(),
                narrator,
                transport.output(),
                agg.assistant(),
            ]
        )
    )
    # WorkerRunner 而非 PipelineRunner：後者自 pipecat 1.3.0 起 deprecated，
    # 會在 log 印警告。現場有人在讀這份 log，雜訊要清掉。兩者 run() 簽章相同
    # （1.5.0 與板子的 1.6.0 都查證過）。
    runner = WorkerRunner()

    if cloud is not None:
        # 雲端也要暖機，理由與上面 TTS 那行完全相同，只是成本更大。
        # 板子實測（Gemini 直連）：第一次呼叫 1209-1905ms，穩態 691-950ms。
        # 冷的那一次**超過 CLOUD_LLM_TIMEOUT_S 的 1.5s 上界**，會讓孩子講的
        # 第一句話降級成本機的笨回覆——而第一印象是最貴的一輪。
        #
        # 一定要在下面那句「開始了」**之前**做完：孩子看到提示就開口，
        # 暖機還在飛的話第一輪就排在它後面，等於沒暖。
        # 印成完整一行而不是 end=""：ALSA 與 loguru 會往同一個終端寫東西，
        # 半行的進度提示會被插斷成讀不懂的樣子（板子實測）。現場有人在讀
        # 這份 log 判斷雲端到底通了沒，它必須一眼看得懂。
        _t0 = time.perf_counter()
        cloud.generate_from_prompt("暖機", target=None)
        _ms = int((time.perf_counter() - _t0) * 1000)
        _verdict = "成功" if cloud.verified() else "失敗，第一輪會照常嘗試並降級"
        print(f"雲端暖機：{_ms}ms（{_verdict}）")

    print("=" * 62)
    print(f"🟢 開始了，請對著玩偶說話（{seconds:.0f} 秒後自動結束，Ctrl-C 可提前停）")
    print(f"   大腦　　　　：{brain_desc}")
    print(f"   今天的主題　：{(lesson.topic if lesson else '(預設)')}")
    print(f"   今天的目標句：{target_sentence}")
    print("   建議說：我想要蘋果")
    print("=" * 62)

    async def stop_after():
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            pass
        await worker.queue_frames([EndFrame()])

    try:
        await asyncio.gather(runner.run(worker), stop_after())
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C，收尾中…")
    finally:
        await asyncio.sleep(0.5)
        for name in ("arecord", "aplay"):
            leftover = _pids(name)
            if leftover:
                print(f"⚠️  {name} 仍在跑（pid {leftover}），強制收掉")
                subprocess.run(["kill", "-9", *leftover])

    print("=" * 62)
    print(f"完成的對話輪數　：{narrator_in.turns}")
    print(f"玩偶回覆　　　　：{' | '.join(narrator_llm.said) or '(無)'}")
    print(f"輸出音訊 chunk　：{narrator.audio_chunks}")
    print(f"疑似自我打斷次數：{narrator.self_interrupts + narrator_in.self_interrupts}")
    summary = _refresh_profile()
    if summary:
        print(f"畫像已更新　　　：{summary}")
        print("　　　　　　　　　（下一場玩偶就會記得這些）")
    if cloud is not None:
        # 報**證據**不是報設定：verified_backend() 只在真的成功過才不是 "none"。
        # 這一行就是現場「大腦在雲端」那句話的憑據。
        print(f"雲端實際走的　　：{cloud.verified_backend()}")
        print(f"雲端狀態　　　　：{cloud.status_detail()}")
        print(f"路由　　　　　　：{llm.policy.route.value}"
              f"（degraded={llm.policy.degraded}）")
    mic_left, spk_left = _pids("arecord"), _pids("aplay")
    if mic_left or spk_left:
        print(f"❌ 裝置未釋放：arecord={mic_left} aplay={spk_left}")
        return 1
    print("✅ 麥克風與喇叭都已釋放")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="WARNING")  # 只留警告，免得刷版蓋掉對話
    sys.exit(asyncio.run(main()))
