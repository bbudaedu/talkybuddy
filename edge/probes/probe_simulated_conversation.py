# -*- coding: utf-8 -*-
"""模擬對話：不用麥克風，量「玩偶講話會不會很單調」。

## 為什麼需要這支

「生動有趣」是主觀的，但**單調是可以量的**。2026-07-31 真人實測，玩偶四輪回覆
幾乎一模一樣，可是當時沒有任何自動化的東西抓得出來——單元測試全綠，格式檢查
也全過，因為 `ensure_readalong` 把每一輪都補成合格的樣子。

這支把三件事變成數字：

1. **開場重複率** — 每輪回覆前 8 個字有幾種。四輪都「你很棒」就是 1 種。
2. **帶讀佔比** — 有幾輪硬塞了「跟我說一遍：<目標句>」。回合式契約是 100%，
   即時陪聊契約應該明顯低於此。
3. **有沒有回應孩子** — 孩子問問題的那幾輪，玩偶的回覆有沒有碰到問題的主題。
   這是真人測試時最刺眼的缺陷：孩子問「可以跟我練習說英文嗎？」，玩偶回
   「跟我說一遍：I want an apple.」。

## 孩子是誰扮的

同一顆雲端模型，換一個 system prompt 扮台灣國小一年級學生。**刻意讓它會問
問題、會離題**——那正是要測的情境。用真模型而不是寫死的句子，是因為寫死的
腳本測不出「玩偶會不會接住沒預料到的話」。

## 不碰麥克風、不碰喇叭

`TranscriptionFrame` 直接餵進 pipeline，走的是與真人對話**完全相同**的那條路
（教材注入 → context → CloudLLMService → 安全閘門 → 帶讀護欄），只是省掉
ASR 與 TTS。所以它測得到對話品質，測不到辨識與發音——那兩項要真人。

## 用法

    PYTHONPATH=. GEMINI_API_KEY=... python edge/probes/probe_simulated_conversation.py [輪數]
"""

from __future__ import annotations

import asyncio
import os
import sys

from loguru import logger
from pipecat.frames.frames import EndFrame, Frame, LLMTextFrame, TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import WorkerRunner
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.time import time_now_iso8601

from edge.runtime.pipecat_adapters.cloud_llm_service import CloudLLMService
from edge.runtime.pipecat_adapters.lesson_progress import (
    LessonProgress,
    LessonProgressProcessor,
)
from edge.runtime.pipecat_adapters.lesson_prompt import LessonPromptInjector
from edge.runtime.pipecat_adapters.readalong_guard import ReadalongGuardProcessor
from edge.runtime.pipecat_adapters.safety_gate import SafetyGateProcessor

DEFAULT_TURNS = 6

# 每則回覆字數上限。板子中文 TTS 約每秒 4.5 字，40 字≈9 秒。
LIVE_MAX_CHARS = 40

# 孩子的第一句固定，之後由模型接話。固定第一句是為了讓不同次執行可比較。
FIRST_UTTERANCE = "我看到一隻狗"

_CHILD_SYSTEM = (
    "你在扮演一個台灣國小一年級的學生，正在跟一隻會說話的玩偶學英文。"
    "規則：一、只用繁體中文，每次只說一句話，不超過20個字，像小孩子講話。"
    "二、不要當乖學生——有時候跟著念，有時候問玩偶問題（例如某個字的英文怎麼說），"
    "有時候講別的（今天發生的事、你喜歡的東西）。三、直接說那句話，不要加引號或說明。"
)


class Collector(FrameProcessor):
    """收玩偶說的話。"""

    def __init__(self):
        super().__init__()
        self.replies: list[str] = []
        self._buf: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            self._buf.append(frame.text)
        await self.push_frame(frame, direction)

    def take(self) -> str:
        text = "".join(self._buf).strip()
        self._buf.clear()
        if text:
            self.replies.append(text)
        return text


def _child_says(cloud, history: list[dict]) -> str:
    """讓模型扮孩子接話；失敗時回一句通用的，不讓探針停擺。"""
    try:
        out = cloud.generate_chat(
            history, system=_CHILD_SYSTEM, target=None, enforce_readalong=False
        )
        return (out or "").strip().splitlines()[0][:40] or "嗯嗯"
    except Exception:
        logger.exception("孩子模擬失敗")
        return "嗯嗯"


def _report(replies: list[str], child_lines: list[str], target: str) -> bool:
    """把單調度變成數字；回傳「這次算不算通過」。"""
    print("\n" + "=" * 66)
    print("量測")
    print("=" * 66)

    openings = {r[:8] for r in replies}
    # 有沒有進度：帶讀過幾種不同的句子。十輪都同一句是 2026-07-31 抓到的
    # 問題——孩子第一輪就唸對了，玩偶還在重複，孩子自己抗議「你怎麼一直
    # 叫我唸一樣的啦」。開場白變化度**看不出**這件事，所以要單獨量。
    import re as _re
    practised = set()
    for r in replies:
        for m in _re.findall(r"跟我說一遍：\s*([A-Za-z][^。\n]*)", r):
            practised.add(m.strip().rstrip(".!?"))
    readalong = sum(1 for r in replies if f"跟我說一遍：{target}" in r)
    # 孩子問問題的輪次：句尾有問號或含「怎麼」「什麼」「嗎」
    asked = [
        i for i, c in enumerate(child_lines)
        if any(k in c for k in ("？", "?", "怎麼", "什麼", "嗎"))
    ]
    # 玩偶有沒有碰到孩子那句話的內容：取孩子句中 2 字詞看是否出現在回覆
    def _echoes(child: str, reply: str) -> bool:
        grams = {child[i:i + 2] for i in range(len(child) - 1)}
        return any(g in reply for g in grams if g.strip())

    answered = [i for i in asked if i < len(replies) and _echoes(child_lines[i], replies[i])]

    # 回覆長度 → 孩子被靜音多久。玩偶是半雙工的：它講話時麥克風關著，
    # 教練 prompt 自己就寫「你多講一句，他就多一句話的時間不能開口」。
    # 板子 TTS 即時率約 0.25x（合成快，但唸出來仍是真實時間），中文約
    # 每秒 4.5 字——這是用板子實測的 chunk 數回推的粗估，抓數量級用。
    lens = [len(r) for r in replies]
    speak_s = [round(n / 4.5, 1) for n in lens]
    print(f"總輪數　　　　：{len(replies)}")
    print(f"回覆長度（字）：{lens}　中位 {sorted(lens)[len(lens) // 2]}")
    print(f"估計唸出時間　：{speak_s} 秒（這段期間孩子講話會被吃掉）")
    print(f"不同開場白　　：{len(openings)} / {len(replies)}"
          f"（越接近總輪數越好；全部一樣就是 1）")
    print(f"練過的不同句子：{len(practised)} 句 → {sorted(practised)}")
    print(f"硬塞帶讀的輪數：{readalong} / {len(replies)}"
          f"（回合式契約會是 100%，即時陪聊應明顯較低）")
    if asked:
        print(f"孩子提問輪次　：{[i + 1 for i in asked]}")
        print(f"其中有被回應的：{[i + 1 for i in answered]}"
              f" → {len(answered)}/{len(asked)}")
    else:
        print("孩子提問輪次　：這次沒問問題（模擬的隨機性，可再跑一次）")

    print("\n判定")
    ok_variety = len(openings) >= max(2, len(replies) - 1)
    ok_answer = (not asked) or len(answered) >= len(asked)
    # 60 字 ≈ 13 秒。孩子等 13 秒才輪到自己講話，注意力早就跑掉了。
    ok_length = max(lens) <= 60
    # 超過 4 輪還只練一句，就是卡住了
    ok_progress = len(replies) <= 4 or len(practised) >= 2
    print(f"  開場白有變化　：{'✅' if ok_variety else '❌ 玩偶在重複自己'}")
    print(f"  有回應孩子的話：{'✅' if ok_answer else '❌ 玩偶無視了孩子的問題'}")
    print(f"  回覆夠短　　　：{'✅' if ok_length else f'❌ 最長 {max(lens)} 字，孩子要等 {max(speak_s)} 秒才能開口'}")
    print(f"  有換句子（進度）：{'✅' if ok_progress else '❌ 一直重複同一句，孩子會失去興趣'}")
    return ok_variety and ok_answer and ok_length and ok_progress


async def main() -> int:
    turns = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TURNS
    from server import scaffold
    from server.cloud_llm import CloudLLM

    cloud = CloudLLM()
    if not cloud.available():
        print(f"❌ 沒有雲端設定：{cloud.status_detail()}")
        return 2

    # 教材與真人對話那條路一致
    try:
        from server import lesson as lesson_mod, store

        store.init_db()
        lesson = lesson_mod.build_lesson(store.list_diagnoses(), store.get_profile())
    except Exception:
        lesson = None
    target = (lesson.target_sentence if lesson else None) or "I want an apple."
    directive = lesson.directive if lesson else None
    topic = lesson.topic if lesson else None

    print("=" * 66)
    print(f"模擬對話（{turns} 輪，不用麥克風）")
    print(f"  後端　：{cloud.configured_backend()}")
    print(f"  主題　：{topic or '(預設)'}　目標句：{target}")
    print("=" * 66)

    # max_chars=40：半雙工玩偶講越久，孩子越久不能開口。40 字約 9 秒。
    try:
        from server import lesson as _lm

        more = _lm.topic_sentences(topic, limit=5) if topic else []
    except Exception:
        more = []
    # 進度由狀態機決定，不是由模型在 prompt 裡數數（見 lesson_progress 的
    # docstring：交給模型判斷時要第 7 輪才換，決賽鏡頭只有 3～4 輪）。
    progress = LessonProgress(more or [target])

    def _system() -> str:
        return scaffold.build_live_system_prompt(
            progress.current or target, directive, topic,
            max_chars=LIVE_MAX_CHARS,
            # 刻意**不**傳 more_sentences：進度已經由 LessonProgress 決定，
            # 模型不需要知道接下來有哪些句子。2026-07-31 實測，把清單給它會
            # 讓它自己去點名別句（「試試看這句：I see a rabbit.」）而護欄再補
            # 一句舊的，孩子當場抓包「為什麼又要說狗狗啦？」。
            # 它每輪只拿到「現在該練哪一句」，其餘由狀態機負責。
        )

    system_prompt = _system()
    context = LLMContext(messages=[{"role": "system", "content": system_prompt}])
    agg = LLMContextAggregatorPair(context)
    collector = Collector()
    service = CloudLLMService(
        cloud=cloud,
        target_provider=lambda: progress.current or target,
        system_provider=_system,
        warmup=False,
    )

    worker = PipelineWorker(
        Pipeline([
            # 進度觀察要在教材注入**之前**：後者會把逐字稿覆寫成整段 prompt。
            LessonProgressProcessor(progress),
            LessonPromptInjector(
                lesson_provider=lambda: (progress.current or target, directive),
                deidentify=True,
            ),
            agg.user(),
            service,
            SafetyGateProcessor(),
            ReadalongGuardProcessor(
                target_provider=lambda: progress.current or target,
                allow_variation=True,
            ),
            collector,
            agg.assistant(),
        ])
    )
    runner = WorkerRunner()

    child_lines: list[str] = []
    # 孩子自己的對話史（角色相反：玩偶說的話對孩子而言是 user）
    child_history: list[dict] = []

    async def converse():
        await asyncio.sleep(0.4)
        cloud.generate_from_prompt("暖機", target=None)  # 冷啟動不算進逐輪
        says = FIRST_UTTERANCE
        for i in range(turns):
            child_lines.append(says)
            print(f"\n👧 孩子 {i + 1}：{says}")
            before = len(collector.replies)
            await worker.queue_frames(
                [TranscriptionFrame(says, "child", time_now_iso8601())]
            )
            for _ in range(300):
                await asyncio.sleep(0.1)
                if len(collector._buf) and len(collector.replies) == before:
                    pass
                if len(collector.replies) > before:
                    break
                # LLMTextFrame 進來但還沒被 take
                if collector._buf:
                    collector.take()
            reply = collector.replies[-1] if len(collector.replies) > before else "(無回覆)"
            print(f"   [狀態機] current={progress.current!r} upcoming={progress.upcoming}")
            print(f"🧸 玩偶 {i + 1}：{reply}")
            child_history.append({"role": "user", "content": reply})
            if i < turns - 1:
                says = await asyncio.to_thread(_child_says, cloud, child_history)
                child_history.append({"role": "assistant", "content": says})
        await worker.queue_frames([EndFrame()])

    await asyncio.gather(runner.run(worker), converse())
    return 0 if _report(collector.replies, child_lines, target) else 1


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    sys.exit(asyncio.run(main()))
