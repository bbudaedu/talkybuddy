# -*- coding: utf-8 -*-
"""CloudLLM：對話回覆的雲端腦加值層（Anthropic 相容 Messages API，可接自架中轉）。

契約與 EdgeLLM 一致（available/generate），任何失敗一律回 None 讓 pipeline 降級。
純標準函式庫（urllib），import 期不觸網、不載重依賴。上雲前對學生文字去識別化，
輸出過 guardrails 後置護欄。端點/認證/model 由 anthropic_relay 解析（與 diagnose 共用 env）。
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

from server import anthropic_relay, bedrock_converse, guardrails
from server.llm import build_user_prompt

_log = logging.getLogger(__name__)

# 雲端 LLM 呼叫逾時（秒）；斷網示範（NETCUT-02／D-03）的快速失敗上界——
# 中途斷網時，降級到 edge 引擎的等待時間就是這個值。
# 刻意「不」與 server/pipeline.py::LLM_TIMEOUT_S 對齊：後者是 cloud/edge
# 共用的外層包裝，必須維持寬鬆才能撐住 edge 引擎的真機生成時間，兩者是
# 刻意解耦的（內層短、外層寬）。
# 預設 1.5s 是為了滿足 ROADMAP「恢復 <1–2 秒」門檻而選的偏緊值，代價是
# 真正連線良好時雲端 LLM 也可能來不及回覆而降級到 edge 品質——現場若要
# 換回品質優先，設環境變數 CLOUD_LLM_TIMEOUT_S=4 即可，最終值待 09-04
# 彩排實測確認。
_TIMEOUT_S: float = float(os.environ.get("CLOUD_LLM_TIMEOUT_S", "1.5"))
_MAX_TOKENS = 160

# 台灣國小英語鷹架家教 system prompt（安全條款重用 guardrails 共用常數）
_SYSTEM_PROMPT = (
    "你是台灣國小學生的英語鷹架家教，只用繁體中文和英文回覆。"
    "嚴格遵守以下規則："
    "一、第一句先用繁體中文稱讚或鼓勵學生。"
    "二、接著帶讀指定的目標英文句，格式必須是「跟我說一遍：<英文句>」，"
    "英文句必須逐字使用我提供的目標句，不可改寫。"
    "三、全部回覆總長不超過60個字。"
    "四、禁止使用 markdown 符號、emoji 表情、以及英文以外的其他外語。"
) + guardrails.CHILD_SAFETY_CLAUSE


def _extract_text(payload: dict) -> str:
    """從 Anthropic Messages 回應取第一段 text；格式不符回空字串。"""
    try:
        for block in payload.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "") or ""
    except Exception:
        pass
    return ""


class CloudLLM:
    """雲端腦對話加值；憑證可解析即 available，consent/network 由 pipeline 把關。

    `available()` 與 `verified()` 是兩件不同的事，不要混用（與 `CloudTTS` 同構）：

    - `available()`：**設定**齊全嗎。pipeline 每輪都問，必須便宜、不碰網路。
    - `verified()`：**實際**跑得動嗎。依最近一次 `generate()` 的結果回答，
      沒跑過就是 False。`/api/status` 用這個。

    分開的理由不是理論潔癖，是這個專案已經被同一個坑咬過三次：

    - 2026-07-29：裝置 `.env` 的 `ANTHROPIC_BASE_URL` 指向反向隧道，隧道沒建
      →`generate()` 26ms 就 Connection refused，但 `cloud_llm` 一路是 true。
      斷網彩排量到「M1 ≈ 0」，那個 0 沒有意義——不是降級很快，是從頭到尾沒上過雲。
      （記載於 `edge/NETCUT_REHEARSAL_CHECKLIST.md` 與 `edge/probes/README.md`。）
    - 2026-07-30：`cloud_tts` 同型問題，已於 commit 6fc96d7 用 `verified()` 修掉。
    - 2026-07-30：裝置 `/api/status` 回 `cloud_provider="relay"`，而 8317 上
      根本沒有行程在聽。

    **自檢說謊比自檢說沒設定更危險**，因為前者不會有人去查。
    """

    def __init__(self) -> None:
        # 最近一次 generate() 的結果：None＝還沒跑過（＝還沒有證據，不得報綠燈）
        self._last: dict | None = None

    def available(self) -> bool:
        """任一後端（Bedrock 或 relay）可解析即 True；任何失敗回 False。

        ⚠️ 這只代表「設定齊全」，不代表跑得動。判斷能不能用請看 `verified()`。
        """
        try:
            if bedrock_converse.resolve_config() is not None:
                return True
            return anthropic_relay.resolve_config() is not None
        except Exception:
            return False

    def configured_backend(self) -> str:
        """設定上**會走**哪條後端："bedrock" | "relay" | "none"。

        優先序與 generate() 一致。這是設定讀數，不是證據——要證據看
        `verified_backend()`。
        """
        try:
            if bedrock_converse.resolve_config() is not None:
                return "bedrock"
            if anthropic_relay.resolve_config() is not None:
                return "relay"
        except Exception:
            pass
        return "none"

    def _record(self, ok: bool, backend: str, reason: str, ms: int = 0) -> None:
        """記下最近一次 generate() 的實際結果（覆蓋式，不做失敗次數平滑）。

        理由同 CloudTTS._record：demo 前要看的是**此刻**能不能用，不是歷史平均。
        """
        self._last = {"ok": ok, "backend": backend, "reason": reason, "ms": ms}

    def verified(self) -> bool:
        """最近一次雲端生成真的成功了嗎。沒跑過 → False（沒證據就不報綠燈）。"""
        return bool(self._last and self._last["ok"])

    def verified_backend(self) -> str:
        """最近一次**成功**的呼叫實際走的後端；沒有成功紀錄回 "none"。

        `/api/status` 的 `cloud_provider` 用這個。現場要當場佐證「大腦在
        Bedrock」，那個欄位就必須是證據而不是設定讀數——否則配額用盡、隧道
        沒建、憑證過期時它照樣說 "bedrock"，而每一輪都在悄悄降級回 edge。
        """
        if self._last and self._last["ok"]:
            return str(self._last["backend"])
        return "none"

    def status_detail(self) -> str:
        """一句話講清楚現在是什麼狀態、依據是什麼（給 /api/status 與 preflight）。"""
        configured = self.configured_backend()
        if configured == "none":
            return (
                "未啟用：既沒有 TALKYBUDDY_CLOUD_PROVIDER=bedrock（＋AWS 憑證），"
                "也沒有 ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN"
            )
        if self._last is None:
            return f"尚未驗證：設定走 {configured}，但這次啟動後還沒實際生成過"
        if self._last["ok"]:
            return f"可用：上次經 {self._last['backend']} 生成成功（{self._last['ms']}ms）"
        return (
            f"設定齊全（{configured}）但上次生成失敗 → 已靜默降級回 edge/scaffold"
            f"（{self._last['reason']}）"
        )

    def generate(self, student_text: str, scaffold, directive: str | None = None) -> str | None:
        """以雲端腦生成加值回覆；任何失敗、逾時、護欄命中回 None（絕不拋進 pipeline）。

        後端優先序：原生 Bedrock Converse → Anthropic 相容 relay。前者需
        ``TALKYBUDDY_CLOUD_PROVIDER=bedrock`` 才啟用，未切換時行為與過去完全一致。

        本方法只負責「把原始學生文字變成 prompt」，實際呼叫交給
        :meth:`generate_from_prompt`。拆開的理由見該方法的 docstring。
        """
        target = getattr(scaffold, "target_sentence", None)
        # 去識別化**只能**套在學生文字上，不可套在整段 prompt 上：deidentify
        # 會把詞庫外的 Title-case 專名遮成 [名字]，而目標句本身就常有專名
        # （`My name is Tom.` → `My name is [名字]`），玩偶就會帶讀錯。
        safe_text = guardrails.deidentify(student_text)  # 上雲前去識別化
        # 模板向 server.llm 借，與 EdgeLLM、LessonPromptInjector 共用同一份。
        # 這裡曾經有一份一字不差的複製品，兩邊各改一次就會悄悄漂移。
        return self.generate_from_prompt(
            build_user_prompt(safe_text, target, directive), target=target
        )

    def generate_from_prompt(self, user_prompt: str, *, target: str | None) -> str | None:
        """以**已組好的** user prompt 呼叫雲端腦；任何失敗回 None。

        為 pipecat pipeline 開的進入點。那條 pipeline 上游的
        ``LessonPromptInjector`` 已經用同一份 ``build_user_prompt`` 把 prompt
        組好了（它必須這麼做，因為 edge 那顆 LLM 也吃同一個 context），雲端這
        一層**不可以再組一次**，否則會變成雙重包裝的 prompt。

        ⚠️ 本方法**不做去識別化**——呼叫端必須已經對學生文字做過。理由同
        :meth:`generate`：對整段 prompt 做會遮掉目標句裡的專名。

        Args:
            user_prompt: 已組好、已去識別化的完整 user message。
            target: 本輪目標英文句，供帶讀護欄補句用；None 表示不檢查。

        Returns:
            通過護欄的回覆文字；失敗、逾時或護欄命中時回 None。
        """
        t0 = time.monotonic()
        backend = "none"
        try:
            # role="chat"：取為 _TIMEOUT_S（1.5s）挑的快模型。若取到診斷用的
            # 大模型，這條路徑會穩定逾時而永遠降級回 edge。
            bedrock_cfg = bedrock_converse.resolve_config(role="chat")
            cfg = anthropic_relay.resolve_config()
            if bedrock_cfg is None and cfg is None:
                self._record(False, "none", "未設定任何雲端後端")
                return None
            backend = "bedrock" if bedrock_cfg is not None else "relay"
            if bedrock_cfg is not None:
                # 原生 Bedrock Converse。timeout 刻意傳 _TIMEOUT_S 而非讓
                # bedrock_converse 用它自己的 12s 預設——斷網橋段（D-03）
                # 的「恢復 <1-2 秒」全靠這個上界，用錯就直接破功。
                text = bedrock_converse.converse_text(
                    _SYSTEM_PROMPT,
                    user_prompt,
                    cfg=bedrock_cfg,
                    max_tokens=_MAX_TOKENS,
                    timeout_s=_TIMEOUT_S,
                )
            else:
                body = json.dumps(
                    {
                        "model": cfg["model"],
                        "max_tokens": _MAX_TOKENS,
                        "system": _SYSTEM_PROMPT,
                        "messages": [{"role": "user", "content": user_prompt}],
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    cfg["url"], data=body, headers=cfg["headers"], method="POST",
                )
                with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                text = _extract_text(payload)

            text = text.strip()
            if not text:
                self._record(False, backend, "雲端回了空字串")
                return None
            # 輸出後置護欄（不安全→降級回 edge/scaffold）
            if not guardrails.passes_guardrail(text):
                # 護欄命中不算「雲端壞掉」——連線與模型都正常，是內容被擋下。
                # 記成失敗會讓 /api/status 對著一次髒字誤報雲端不可用。
                self._record(True, backend, "護欄命中（雲端本身正常）",
                             int((time.monotonic() - t0) * 1000))
                return None
            # 先繁化（與 edge 同序）再跑帶讀護欄
            text = guardrails.to_traditional(text)
            self._record(True, backend, "ok", int((time.monotonic() - t0) * 1000))
            # 帶讀恰好一句：漏句要補、格式跑掉要修、不得重複（與 edge 共用）
            return guardrails.ensure_readalong(text, target)
        except Exception as exc:
            _log.exception("CloudLLM generate 失敗，降級回 edge/scaffold")
            self._record(False, backend, f"{type(exc).__name__}: {str(exc)[:120]}",
                         int((time.monotonic() - t0) * 1000))
            return None
