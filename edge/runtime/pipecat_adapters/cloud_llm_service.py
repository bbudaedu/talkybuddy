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
from pipecat.services.settings import LLMSettings

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

    Settings = LLMSettings
    """沿用基底的 settings 形狀。

    這些欄位**必須全部初始化**，否則 pipeline 啟動時 pipecat 會印一行紅色
    `ERROR: LLMSettings: the following fields are NOT_GIVEN: ...`。功能不受
    影響，但決賽現場有人在讀那份 log，一行 ERROR 就得花時間解釋它不是問題。
    不支援的欄位一律填 `None`，那是基底類別給的正式表達方式（同
    `sensevoice_stt` 對 `language=None` 的處理）。

    這裡幾乎全是 None，因為取樣參數（temperature / top_p / seed…）由
    `server.cloud_llm` 那一層決定，各後端各自對應到自己的 API；
    `system_instruction` 也一樣——它是 `cloud_llm._SYSTEM_PROMPT`，不由
    pipecat 這層管。
    """

    def __init__(
        self,
        *,
        cloud=None,
        fallback: GenerateFromPrompt | None = None,
        policy: FailoverPolicy | None = None,
        target_provider: TargetProvider | None = None,
        system_provider: Callable[[], str] | None = None,
        warmup: bool = True,
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
            system_provider: Returns the system prompt for the live-chat
                contract. Pass ``scaffold.build_live_system_prompt(...)`` to
                switch the cloud path from the rigid turn-based scaffold to the
                coach persona. Omit to keep the previous single-turn behaviour
                exactly. See :meth:`_generate`.
            warmup: Make one throwaway cloud call when the pipeline starts, so
                the child's first sentence does not pay for the TLS handshake.
                See :meth:`_warmup`.
        """
        kwargs.setdefault(
            "settings",
            self.Settings(
                model="talkybuddy-cloud",  # 僅供 log/metrics 辨識；實際 model 由 cloud_llm 決定
                system_instruction=None,
                temperature=None,
                max_tokens=None,
                top_p=None,
                top_k=None,
                frequency_penalty=None,
                presence_penalty=None,
                seed=None,
                filter_incomplete_user_turns=False,
                user_turn_completion_config=None,
            ),
        )
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
        self._system_provider = system_provider
        self._warmup = warmup

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

    async def start(self, frame):
        """Start the service, optionally warming the cloud connection first.

        Args:
            frame: The StartFrame that opened the pipeline.
        """
        await super().start(frame)
        if self._warmup:
            await self._warmup_call()

    async def _warmup_call(self) -> None:
        """打一次丟棄的雲端呼叫，把冷啟動成本挪到 pipeline 啟動時。

        2026-07-31 板子實測（Gemini 直連，連續 10 輪）：

            第 1 輪 1121ms ← TLS handshake
            第 2-10 輪 799-962ms，中位 827ms

        同一支探針更早一次量到第一輪 **1599ms，超過 `CLOUD_LLM_TIMEOUT_S` 的
        1.5s 上界**。也就是說穩態明明只用掉一半預算，卻會在孩子講的**第一句
        話**上逾時、降級成本機的笨回覆——而第一印象正是決賽現場最貴的那一輪。

        與 `probe_live_conversation` 對 TTS 做 `synth([("zh", "暖機")])` 是
        同一個處置，不是新發明。

        暖機的結果**刻意不進 policy、也不推進 pipeline**：
        - 不進 policy：暖機失敗多半是「還沒連上」而不是「雲端壞了」，
          拿它去累積失敗次數會讓玩偶一開機就誤判成降級。
        - 不推 frame：那句回覆會被 TTS 唸出來。
        """
        try:
            await asyncio.to_thread(
                self._cloud.generate_from_prompt, "暖機", target=None
            )
            logger.debug("雲端暖機完成")
        except Exception:
            # 起不來的雲端不該讓玩偶起不來。真正的判斷留給第一輪。
            logger.warning("雲端暖機失敗（不影響啟動，第一輪會照常嘗試並降級）")

    def _current_target(self) -> str | None:
        """取本輪目標句；provider 壞掉不可以讓對話中斷。"""
        if self._target_provider is None:
            return None
        try:
            return self._target_provider()
        except Exception:
            logger.exception("target_provider 失敗，本輪不做帶讀補句")
            return None

    def _call_cloud(self, prompt: str, target: str | None, messages: list) -> str | None:
        """依有沒有 `system_provider` 決定走哪一種契約（同步，跑在工作執行緒裡）。

        **回合式（預設）**：單輪、內建 60 字 system prompt、事後強制補帶讀。
        給 512 ctx 的小模型用的契約，也是 edge 降級那顆吃的東西。

        **即時陪聊（給了 system_provider）**：多輪、教練 prompt、不強制帶讀。
        2026-07-31 真人實測，回合式契約下玩偶四輪回覆幾乎一模一樣——孩子問
        「可以跟我練習說英文嗎？」它還是回「跟我說一遍：I want an apple.」。
        `scaffold.build_live_system_prompt` 那份 prompt 明寫「孩子如果問你別的，
        一定要先回應他…絕對不可以假裝沒聽到孩子的話」，而 `server/app.py` 的
        `/ws/live` 一直是這樣跑的。這裡是讓 pipecat 接上同一套既有契約。
        """
        if self._system_provider is None:
            return self._cloud.generate_from_prompt(prompt, target=target)
        return self._cloud.generate_chat(
            messages,
            system=self._system_provider(),
            target=target,
            enforce_readalong=False,
        )

    async def _generate(
        self, prompt: str, target: str | None, messages: list | None = None
    ) -> str | None:
        """跑完一輪：先照 policy 決定要不要試雲端，失敗就當輪降級。"""
        if self._policy.should_try_primary():
            try:
                text = await asyncio.to_thread(
                    self._call_cloud, prompt, target, messages or []
                )
            except Exception:
                # CloudLLM 自己不拋（任何失敗都回 None），但這個參數是注入的，
                # 別家的實作可能會拋。**拋出來一樣是「雲端這輪失敗」**，不可以
                # 讓它一路炸到 process_frame 而跳過當輪降級——那樣孩子就聽到
                # 沉默了，正好是這整個設計要避免的事。
                logger.exception("雲端呼叫拋出例外，視同本輪失敗")
                text = None
            if text:
                self._policy.record_success()
                return text
            # None ＝「雲端這輪沒給出可用回覆」；原因 CloudLLM 已經記在
            # status_detail()（逾時、護欄命中、回覆被截斷…）。
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
            text = await self._generate(
                prompt, self._current_target(), list(frame.context.messages)
            )
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
