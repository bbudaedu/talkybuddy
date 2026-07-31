# -*- coding: utf-8 -*-
"""stateless_context.py — 每輪只送 system + 當前 user message，不累積對話歷史。

## 為什麼需要（2026-07-31 真人測試現場抓到）

pipecat 的 context aggregator 預設會**累積整段對話**。板上的 llama-server
`--ctx-size 512`，於是真人多輪對話第一輪就爆，而且越爆越大：

```
request (516 tokens) exceeds the available context size (512 tokens)
request (579 tokens) exceeds ...
request (642 tokens) exceeds ...
```

單輪測試剛好卡在 512 以內，所以前面幾輪 probe 都沒發現。

**而現行的 `EdgeLLM.generate` 本來就是無狀態的**——它每次組
`messages = [system, user]`，不帶歷史（見 `server/llm.py`）。所以現行架構從來
不會爆 ctx。本 processor 就是把那個行為搬到 pipecat 上。

## 代價要講清楚

無狀態表示玩偶**不記得上一輪講過什麼**。孩子說「再一次」它不會知道指的是什麼。

這不是我們選的，是 `--ctx-size 512` 逼的：光是 system prompt（288 字）加上
帶教材的 user prompt 就快用完了，塞不下歷史。要有記憶就得先把 ctx 加大，
而那要改 llama-server 的啟動參數（`edge/runtime/run_llama_server.py`），
並付出記憶體與速度的代價——**屬於決賽路徑，不在本 worktree 的變更範圍**。

## 擺放位置

必須在 `context_aggregator.user()` **之前**：
先由本 processor 把 context 清成只剩 system，aggregator 再把這一輪的 user
message 加進去，結果正好是 `[system, user]`。
"""

from __future__ import annotations

from loguru import logger
from pipecat.frames.frames import Frame, TranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class StatelessContextProcessor(FrameProcessor):
    """每次使用者說話前，把 LLM context 重設成只有 system message。"""

    def __init__(self, *, context: LLMContext, **kwargs):
        """Initialize the stateless context processor.

        Args:
            context: The shared LLMContext whose history should be dropped each turn.
        """
        super().__init__(**kwargs)
        self._context = context
        self._system_messages = self._snapshot_system()

    def _snapshot_system(self) -> list:
        """開場時把 system message 記下來——之後每輪都還原成這一份。"""
        try:
            messages = self._context.get_messages()
        except Exception:
            logger.exception("讀取 LLMContext 失敗，無狀態重設將不生效")
            return []
        return [m for m in messages if _role_of(m) == "system"]

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Reset conversation history before each user utterance.

        Args:
            frame: The frame flowing through the pipeline.
            direction: Frame direction.
        """
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
            try:
                self._context.set_messages(list(self._system_messages))
            except Exception:
                # 重設失敗就讓它照舊累積——會爆 ctx，但不該在這裡中斷對話。
                logger.exception("重設 LLMContext 失敗，本輪將帶著歷史送出")
        await self.push_frame(frame, direction)


def _role_of(message) -> str | None:
    """取出訊息的 role，dict 與物件兩種形狀都支援。"""
    if isinstance(message, dict):
        return message.get("role")
    return getattr(message, "role", None)
