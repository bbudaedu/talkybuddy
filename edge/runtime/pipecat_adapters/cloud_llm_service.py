# -*- coding: utf-8 -*-
"""cloud_llm_service.py — 把 `server.cloud_llm.CloudLLM` 包成 pipecat 的 LLMService。

## 為什麼是包既有的 CloudLLM，而不是用 pipecat 原生的雲端 service

pipecat 1.6 有 `services/aws/llm.py`（`AWSBedrockLLMService`，原生 Converse、
會串流），照理說更「原生」。但 `CloudLLM` 帶著四樣**這個專案已經付過學費**、
而原生 service 沒有的東西：

1. **上雲前去識別化**（`guardrails.deidentify`）——孩子的隱私，不是可選項
2. **`verified()` 證據追蹤**——「設定齊全」與「真的跑得動」是兩件事。
   這個專案被同一個坑咬過三次（見 `CloudLLM` 的 docstring），現場要當場
   佐證「大腦在雲端」時，`/api/status` 必須報證據而不是報設定
3. **Bedrock → relay 兩層後端降級**，已在 server 端實戰驗證過
4. 輸出護欄、簡轉繁、帶讀補句與 edge 路徑**逐字共用**同一份 helper

重寫一份會是這個 session 的交接文件第四節列的第六次「自己發明了更差的」。

代價是 `CloudLLM.generate_from_prompt` 是**阻塞、非串流**的：完整回覆會以
單一 `LLMTextFrame` 推下去。第一個字的延遲因此變長（要等整句生成完），
換來的是 `ReadalongGuardProcessor` 那條「串流下只能補不能改」的限制消失
——它拿到的是完整句子。

## 為什麼降級發生在**這一輪**，而不是交給 ServiceSwitcher

`docs/PIPECAT_EDGE_DESIGN.md` 原本寫的是「用官方 `ServiceSwitcher` 做路由」。
實際讀過 `pipeline/service_switcher.py` 之後，那個機制達不到同一份文件自己
訂的目標：

> 切失敗最壞的結果是「這一輪回答比較笨」，而不是「玩偶不會講話了」

`ServiceSwitcher` 是收到非致命 `ErrorFrame` 後**換掉作用中的 service**，
換完之後的 frame 才會走新路。觸發切換的**那一輪已經沒有回覆了**——對著玩偶
講話的孩子聽到的是沉默，而沉默的症狀跟「玩偶壞了」一模一樣。

所以這裡做兩層，各司其職：

| 層 | 誰做 | 管什麼 |
|---|---|---|
| 當輪降級 | 本模組 | 雲端這次沒回 → 立刻改用 `fallback`，這一輪不掉 |
| 跨輪路由 | `FailoverPolicy` | 連續失敗就別再浪費 `CLOUD_LLM_TIMEOUT_S` 去試 |

兩層是互補的：第一層保證不掉輪（代價是這一輪比較慢——雲端逾時 1.5s 之後
還要再等 edge 生成），第二層保證那個代價不會每輪都付。

## 阻塞呼叫一律 to_thread

`CloudLLM` 走 urllib／boto3，都是同步阻塞。pipecat 整條 pipeline 跑在單一
event loop 上——**在那裡阻塞等於同時凍住 VAD、麥克風讀取與喇叭播放**。
理由與 `sensevoice_stt` 相同，那裡寫得更詳細。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService

from edge.runtime.pipecat_adapters.failover import FailoverPolicy

GenerateFromPrompt = Callable[..., "str | None"]
"""``(user_prompt, *, target) -> str | None``。

`CloudLLM.generate_from_prompt` 與 `EdgeLLM.generate_from_prompt` 都是這個形狀，
所以兩者可以直接互相替換——這正是降級那一輪需要的性質。
"""

TargetProvider = Callable[[], "str | None"]
"""回傳本輪目標英文句，供帶讀護欄補句用。應與 `LessonPromptInjector` 同源。"""


def _last_user_text(context: LLMContext) -> str | None:
    """取出 context 裡最後一則 user 訊息的純文字；取不到回 None。

    上游 `LessonPromptInjector` 已經把它換成完整的 user prompt，所以這裡拿到
    的就是要直接送出去的東西——**不再組一次**。

    content 可能是字串，也可能是 pipecat 的多段格式（list of parts）；後者
    只取 text 段落串起來，圖片之類的非文字段落忽略。
    """
    for message in reversed(context.messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content or None
        if isinstance(content, list):
            parts = [
                p["text"]
                for p in content
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            ]
            return "".join(parts) or None
        return None
    return None


class CloudLLMService(LLMService):
    """以既有 `CloudLLM` 為大腦的 pipecat LLM 服務，帶當輪降級與跨輪路由。"""

    def __init__(
        self,
        *,
        cloud=None,
        fallback: GenerateFromPrompt | None = None,
        policy: FailoverPolicy | None = None,
        target_provider: TargetProvider | None = None,
        **kwargs,
    ):
        """Initialize the cloud LLM service.

        Args:
            cloud: Object exposing ``generate_from_prompt(prompt, *, target)``.
                Defaults to a fresh :class:`server.cloud_llm.CloudLLM`.
            fallback: Same-shaped callable used when the cloud fails **this
                turn**. Pass ``EdgeLLM().generate_from_prompt`` in production.
                When omitted, a failed turn simply produces no reply.
            policy: Cross-turn routing policy. Defaults to a fresh
                :class:`FailoverPolicy`.
            target_provider: Returns the current target sentence. Should read
                from the same lesson source as ``LessonPromptInjector``.
        """
        super().__init__(**kwargs)
        if cloud is None:
            # lazy import：`server.cloud_llm` 會拉進 boto3 解析路徑，import 期
            # 不該讓它影響板子的啟動時間（沿用 bedrock_converse 的慣例）。
            from server.cloud_llm import CloudLLM

            cloud = CloudLLM()
        self._cloud = cloud
        self._fallback = fallback
        self._policy = policy or FailoverPolicy()
        self._target_provider = target_provider

    @property
    def policy(self) -> FailoverPolicy:
        """The cross-turn routing policy.

        Returns:
            The live policy object, so callers can read ``degraded`` for
            status reporting or pin the route for a demo.
        """
        return self._policy

    def can_generate_metrics(self) -> bool:
        """Report that this service produces TTFB/processing metrics.

        Returns:
            True.
        """
        return True

    def _current_target(self) -> str | None:
        """取本輪目標句；provider 壞掉不可以讓對話中斷。"""
        if self._target_provider is None:
            return None
        try:
            return self._target_provider()
        except Exception:
            logger.exception("target_provider 失敗，本輪不做帶讀補句")
            return None

    async def _generate(self, prompt: str, target: str | None) -> str | None:
        """跑完一輪：先照 policy 決定要不要試雲端，失敗就當輪降級。"""
        if self._policy.should_try_primary():
            text = await asyncio.to_thread(
                self._cloud.generate_from_prompt, prompt, target=target
            )
            if text:
                self._policy.record_success()
                return text
            # CloudLLM 任何失敗都回 None（它自己不拋），所以這裡看到 None
            # 就是「雲端這輪沒給出可用回覆」——原因它已經記在 status_detail()。
            self._policy.record_failure()
            logger.warning(
                "雲端這一輪沒有回覆，改用本機降級（route={}）", self._policy.route.value
            )

        if self._fallback is None:
            return None
        return await asyncio.to_thread(self._fallback, prompt, target=target)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames, running an inference for each LLMContextFrame.

        Args:
            frame: The frame to process.
            direction: The direction of frame processing.
        """
        await super().process_frame(frame, direction)

        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        prompt = _last_user_text(frame.context)
        if not prompt:
            # 沒有使用者訊息就沒有這一輪——別送空 prompt 上雲燒配額。
            logger.debug("context 裡沒有 user 訊息，跳過本輪生成")
            return

        await self.push_frame(LLMFullResponseStartFrame())
        await self.start_processing_metrics()
        await self.start_ttfb_metrics()
        try:
            text = await self._generate(prompt, self._current_target())
            await self.stop_ttfb_metrics()
            if text:
                await self._push_llm_text(text)
        except Exception as exc:
            # 走到這裡代表連降級都炸了。玩偶這一輪不會講話，但 pipeline 要活著
            # ——麥克風所有權絕不因為一次生成失敗而轉移。
            logger.exception("CloudLLMService 生成失敗，本輪無回覆")
            await self.push_error(error_msg=f"LLM 生成失敗：{exc}", exception=exc)
        finally:
            await self.stop_processing_metrics()
            await self.push_frame(LLMFullResponseEndFrame())
