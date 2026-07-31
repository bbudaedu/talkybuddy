# -*- coding: utf-8 -*-
"""真網路驗證：CloudLLMService 在 pipecat pipeline 裡真的接得上雲端嗎。

單元測試把 CloudLLM 換成假物件，所以它證明的是**接線正確**，不是**雲端通**。
這支探針補的就是後者：真的送出 HTTP／Bedrock 請求，真的等回覆，真的量時間。

## 為什麼要有這支（而不是等決賽當天再說）

`server/cloud_llm.py` 的 docstring 記著同一個坑咬過三次：**「設定齊全」不等於
「跑得動」**，而自檢說謊比自檢說沒設定更危險，因為前者不會有人去查。
這支探針的存在就是為了讓「雲端通了」這句話有證據可以拿出來。

## 用法

三種後端都跑得起來，選一種設好環境變數即可。

**A. Bedrock（決賽主線）**

    PYTHONPATH=. TALKYBUDDY_CLOUD_PROVIDER=bedrock BEDROCK_REGION=ap-east-2 \
        python edge/probes/probe_cloud_llm_service.py

region 與 model 的可用組合**每個帳號都不同**，換帳號務必先跑：

    PYTHONPATH=. python -m server.bedrock_converse   # 列出該帳號實際可用的 model ID

**B. Anthropic 相容中轉／官方 API**

    PYTHONPATH=. ANTHROPIC_API_KEY=... ANTHROPIC_BASE_URL=http://<relay>:8317 \
        ANTHROPIC_MODEL=<model> python edge/probes/probe_cloud_llm_service.py

⚠️ 2026-07-31 實測：把 `cli-proxy-api` 這類**轉發到 Claude Code 訂閱**的中轉
接上 Claude 系列模型時，我們送的 `system` 欄位會被上游的 Claude Code system
prompt 蓋掉——玩偶會回「我是 Claude Code，Anthropic 的官方 CLI 工具」並拒絕
教英文。那種中轉要驗證請改用**非 Anthropic 的上游模型**（Gemini／GPT 等），
它們不會被注入。這不是本專案的 bug，是那個中轉的性質。

## 讀輸出的方式

- `雲端` 那幾行必須有內容，而且格式要是「稱讚 + 跟我說一遍：<英文句>」
- `延遲` 要跟 `CLOUD_LLM_TIMEOUT_S` 對照：超過就代表真機上每輪都會降級，
  雲端等於白接（預設 1.5s，用環境變數放寬）
- 最後的「降級演練」段落證明的是**雲端掛掉時這一輪不會掉**
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

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
from edge.runtime.pipecat_adapters.failover import FailoverPolicy
from edge.runtime.pipecat_adapters.lesson_prompt import LessonPromptInjector
from edge.runtime.pipecat_adapters.stateless_context import StatelessContextProcessor

TARGET = "I want an apple."

# 孩子可能說的話。第三句刻意帶個人資訊，用來當場證明去識別化真的有作用。
UTTERANCES = [
    "我想要蘋果",
    "老師我不會念",
    "我是 Tom 我家電話 0912345678",
]

try:
    from server.llm import EdgeLLM

    SYSTEM_PROMPT = EdgeLLM._SYSTEM_PROMPT
except Exception:  # pragma: no cover - 只在 server 套件缺失時
    SYSTEM_PROMPT = "你是陪伴孩子學英文的玩偶。"


class Collector(FrameProcessor):
    """把 LLM 吐出來的文字收起來，並記下每一輪花了多久。"""

    def __init__(self):
        super().__init__()
        self.replies: list[str] = []
        self.started_at: float | None = None
        self.latencies_ms: list[int] = []

    def mark_turn_start(self) -> None:
        self.started_at = time.monotonic()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            if self.started_at is not None:
                self.latencies_ms.append(int((time.monotonic() - self.started_at) * 1000))
                self.started_at = None
            self.replies.append(frame.text)
        await self.push_frame(frame, direction)


def _describe_backend(cloud) -> str:
    """一句話講清楚這次會走哪條後端，以及依據是什麼。"""
    return f"configured={cloud.configured_backend()} | {cloud.status_detail()}"


async def _run_turns(service: CloudLLMService, collector: Collector, utterances) -> None:
    """把幾句話依序送進一條最小 pipeline，逐輪印出結果。"""
    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    agg = LLMContextAggregatorPair(context)

    worker = PipelineWorker(
        Pipeline(
            [
                StatelessContextProcessor(context=context),
                # deidentify=True：上雲前遮個資。目標句不受影響（見 lesson_prompt）。
                LessonPromptInjector(target=TARGET, deidentify=True),
                agg.user(),
                service,
                collector,
                agg.assistant(),
            ]
        )
    )
    # WorkerRunner 而非 PipelineRunner：後者自 pipecat 1.3.0 起 deprecated。
    runner = WorkerRunner()

    async def feed():
        # 等 pipeline 起來再餵，否則第一個 frame 會落在 StartFrame 之前。
        await asyncio.sleep(0.5)
        for text in utterances:
            print(f"\n👧 孩子說：{text}")
            collector.mark_turn_start()
            before = len(collector.replies)
            await worker.queue_frames([TranscriptionFrame(text, "child", time_now_iso8601())])
            # 等這一輪的回覆落地再送下一句（最多 40 秒）。
            for _ in range(400):
                await asyncio.sleep(0.1)
                if len(collector.replies) > before:
                    break
            new = collector.replies[before:]
            print(f"🧸 玩偶說：{''.join(new) or '(這一輪沒有回覆)'}")
        await worker.queue_frames([EndFrame()])

    await asyncio.gather(runner.run(worker), feed())


async def main() -> int:
    from server.cloud_llm import CloudLLM

    cloud = CloudLLM()
    timeout_s = os.environ.get("CLOUD_LLM_TIMEOUT_S", "1.5")

    print("=" * 66)
    print("雲端接線驗證（真網路）")
    print(f"  後端　　：{_describe_backend(cloud)}")
    print(f"  逾時上界：{timeout_s}s（CLOUD_LLM_TIMEOUT_S）")
    print(f"  目標句　：{TARGET}")
    print("=" * 66)

    if not cloud.available():
        print("\n❌ 沒有可用的雲端設定，這支探針無事可做。")
        print("   Bedrock：TALKYBUDDY_CLOUD_PROVIDER=bedrock + AWS 憑證 + BEDROCK_REGION")
        print("   中轉／官方：ANTHROPIC_API_KEY（＋可選 ANTHROPIC_BASE_URL / ANTHROPIC_MODEL）")
        return 2

    # --- 第一段：雲端真的通嗎 ---
    collector = Collector()
    service = CloudLLMService(cloud=cloud, target_provider=lambda: TARGET)
    await _run_turns(service, collector, UTTERANCES)

    print("\n" + "-" * 66)
    print(f"雲端後端（證據）：verified={cloud.verified()} backend={cloud.verified_backend()}")
    print(f"　　　　　　　　　{cloud.status_detail()}")
    if collector.latencies_ms:
        worst = max(collector.latencies_ms)
        print(f"每輪延遲 ms　　：{collector.latencies_ms}（最慢 {worst}）")
        if worst > float(timeout_s) * 1000:
            print(
                f"⚠️  最慢一輪超過逾時上界 {timeout_s}s —— 真機上這種輪次會降級回 edge，"
                "雲端等於白接。要嘛放寬 CLOUD_LLM_TIMEOUT_S，要嘛換更快的模型／region。"
            )

    ok_cloud = cloud.verified() and len(collector.replies) > 0
    print(f"雲端可用　　　　：{'✅' if ok_cloud else '❌'}")

    # --- 第二段：雲端掛掉時，這一輪會不會掉 ---
    print("\n" + "-" * 66)
    print("降級演練：雲端全數失敗，孩子仍然要聽到回覆")

    class _DeadCloud:
        def generate_from_prompt(self, user_prompt, *, target):
            return None

    fallback_collector = Collector()
    fallback_service = CloudLLMService(
        cloud=_DeadCloud(),
        fallback=lambda prompt, *, target: f"沒關係，我們再試一次！跟我說一遍：{target}",
        policy=FailoverPolicy(failure_threshold=2, cooldown_s=30.0),
        target_provider=lambda: TARGET,
    )
    await _run_turns(fallback_service, fallback_collector, UTTERANCES[:2])

    no_silent_turn = len(fallback_collector.replies) >= 2
    print(f"\n每一輪都有回覆　：{'✅' if no_silent_turn else '❌ 有輪次掉了（孩子聽到沉默）'}")
    print(f"路由狀態　　　　：{fallback_service.policy.route.value}"
          f"（degraded={fallback_service.policy.degraded}）")

    print("=" * 66)
    return 0 if (ok_cloud and no_silent_turn) else 1


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    sys.exit(asyncio.run(main()))
