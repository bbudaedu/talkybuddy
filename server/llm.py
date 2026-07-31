# -*- coding: utf-8 -*-
"""EdgeLLM：邊緣 LLM 加值層（llama-server 獨立行程 + Qwen2.5-1.5B GGUF Q4，經 HTTP client 呼叫）。

契約（CONTRACTS.md）：
- ``available() -> bool``：llama-server /health 短逾時 GET 回 200 **且** body 是
  帶 ``status`` 鍵的 JSON 物件才回 True（光看 200 會把佔用同一埠的其他服務
  誤判成 llama-server）；連線被拒/逾時/任何例外一律回 False，絕不拋出。
- ``generate(student_text, scaffold) -> str | None``：
  逾時（>8 秒）、例外、未載入、或輸出命中 ``scaffold.safety_check`` 一律回 None；
  pipeline 以 scaffold.reply_text 為準，LLM 只是加值。

llama-server 為交叉編譯出的獨立 OS 行程（非 in-process Python 模型物件），
本模組只負責以 stdlib urllib 打 localhost HTTP（比照 server/cloud_llm.py 慣例，
不新增 requests/httpx 依賴）。server.config / server.guardrails 全部 lazy import，
import 本模組絕不可觸網、不可炸（無 llama-server 時伺服器仍要能啟動＝降級路徑）。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from server import guardrails

_log = logging.getLogger(__name__)

# LLM 生成逾時上限（秒），超過即放棄並回 None
_GENERATE_TIMEOUT_S = 8.0

# available() /health 探測逾時（秒）；短逾時因 pipeline 每輪都呼叫一次
_HEALTH_TIMEOUT_S = 0.5

# _call_llama_server /v1/chat/completions 逾時（秒）；略小於 _GENERATE_TIMEOUT_S，
# 為外層 time.monotonic() 逾時檢查留餘裕。
_CALL_TIMEOUT_S = 7.5


def _llama_server_base_url() -> str:
    """組出 llama-server base URL；config 尚未就緒時仍回一個字串（lazy import 保護）。"""
    from server import config
    return f"http://{config.LLM_SERVER_HOST}:{config.LLM_SERVER_PORT}"


def build_user_prompt(
    student_text: str, target: str | None, directive: str | None = None
) -> str:
    """組出帶讀教學的 user prompt（學生的話 + 目標英文句 + 可選教學策略）。

    抽成模組層級函式，因為現在有兩個消費者：`EdgeLLM.generate`，以及 pipecat
    pipeline 的 `LessonPromptInjector`（`edge/runtime/pipecat_adapters/`）。
    **兩邊必須共用同一份模板**——2026-07-31 實測，pipecat 把 ASR 逐字稿直接當成
    user message 送進 LLM，回覆變成「跟我說一遍：我想要蘋果」：目標句從英文
    掉成中文，因為模型根本沒收到目標英文句。

    Args:
        student_text: 學生剛剛說的話（ASR 逐字稿）。
        target: 本輪的目標英文句；None／空字串時該行留空。
        directive: 已格式化的「本輪教學策略」中文區塊；None／空白則不注入。

    Returns:
        完整的 user prompt 字串。
    """
    directive_block = f"\n{directive.strip()}\n" if directive and directive.strip() else ""
    return (
        f"學生剛剛說：「{student_text}」\n"
        f"目標英文句：{target or ''}\n"
        f"{directive_block}"
        "請照規則回覆：先一句繁體中文稱讚鼓勵，"
        "再用「跟我說一遍：<英文句>」帶讀目標英文句。"
    )


class EdgeLLM:
    """llama-server HTTP client，失敗一律優雅降級（不 in-process 載入模型）。"""

    # 台灣國小英語鷹架家教 system prompt
    _SYSTEM_PROMPT = (
        "你是台灣國小學生的英語鷹架家教，只用繁體中文和英文回覆。"
        "嚴格遵守以下規則："
        "一、第一句先用繁體中文稱讚或鼓勵學生。"
        "二、接著帶讀指定的目標英文句，格式必須是「跟我說一遍：<英文句>」，"
        "英文句必須逐字使用我提供的目標句，不可改寫。"
        "三、全部回覆總長不超過60個字。"
        "四、禁止使用 markdown 符號、emoji 表情、以及英文以外的其他外語。"
    ) + guardrails.CHILD_SAFETY_CLAUSE

    def available(self) -> bool:
        """對 llama-server /health 發短逾時 GET；**回應必須長得像 llama-server** 才回 True。

        只看 HTTP 200 會產生假綠燈：LLM_SERVER_PORT 預設 8080 是最常被別的
        服務佔走的埠，而任何 SPA / 反向代理對未知路徑都回 200 + HTML。
        2026-07-30 開發機上實際發生過——`/api/status` 的 `llm` 是綠的，但每一輪
        都在 `_call_llama_server` 的 `json.loads` 炸掉、靜默降級成 scaffold 罐頭
        回覆，畫面上完全看不出來。這是與 `cloud_tts.verified()` 同一類的問題：
        **接得上 ≠ 跑得動**。

        判準：200 + body 可解析成 JSON 物件 + 帶 ``status`` 鍵（llama.cpp 的
        /health 契約）。故意不比對 status 的值，因為 llama.cpp 在載入模型期間
        會回 ``"loading model"``，那仍是「真的是 llama-server」——只是還沒好，
        由 generate() 那條路降級即可，不該讓 available() 在這裡誤判成別的服務。
        """
        try:
            base = _llama_server_base_url()
            req = urllib.request.Request(f"{base}/health", method="GET")
            with urllib.request.urlopen(req, timeout=_HEALTH_TIMEOUT_S) as resp:
                if resp.status != 200:
                    return False
                payload = json.loads(resp.read().decode("utf-8"))
            return isinstance(payload, dict) and "status" in payload
        except Exception:
            return False

    def _call_llama_server(self, messages: list[dict]) -> str:
        """對 llama-server 發 /v1/chat/completions POST，回傳回覆文字（唯一 HTTP 呼叫點）。

        任何 urllib 例外（連線被拒/逾時/HTTP 錯誤）皆不在此攔截，交由呼叫端
        generate() 的外層 try/except Exception 統一處理並降級為 None。
        """
        base = _llama_server_base_url()
        body = json.dumps(
            {
                "messages": messages,
                "max_tokens": 120,
                "temperature": 0.7,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_CALL_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload["choices"][0]["message"]["content"]

    def generate(
        self,
        student_text: str,
        scaffold: "ScaffoldResult",  # noqa: F821
        directive: str | None = None,
    ) -> str | None:
        """依 scaffold 結果生成加值回覆；任何失敗、逾時或安全命中回 None。

        directive（可選）：已格式化的「本輪教學策略」中文區塊，由 pipeline 端
        提供。None 或空白 → 完全不注入，行為與現況一致。護欄：target 帶讀句
        仍由 scaffold 決定，directive 只影響稱讚語與延伸問句。
        """
        start = time.monotonic()
        try:
            target = getattr(scaffold, "target_sentence", None)

            user_prompt = build_user_prompt(student_text, target, directive)
            # PR #7 在這裡對 create_chat_completion 加了一把鎖，因為 llama.cpp 的
            # 單一 context 被兩個執行緒同時呼叫會在 native 層 segfault。**這條路徑
            # 已經不需要那把鎖**：Phase 8 之後 EdgeLLM 改走 HTTP 打獨立的
            # llama-server 行程（見 _call_llama_server），本行程內沒有共用的
            # native context 可以被併發踩到，序列化由 llama-server 自己負責。
            # PR #7 對 ASR/TTS 單例的同類修復仍然適用，那幾個引擎確實是 in-process。
            messages = [
                {"role": "system", "content": self._SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
            content = self._call_llama_server(messages)
            if time.monotonic() - start > _GENERATE_TIMEOUT_S:
                return None

            text = (content or "").strip()
            if not text:
                return None

            # 輸出仍須通過安全護欄（edge/雲端共用 helper；不安全→降級回 scaffold）
            if not guardrails.passes_guardrail(text):
                return None

            # 先繁化再跑帶讀護欄：這樣護欄補上的目標英文句是逐字的，
            # 不會被繁化流程碰到（OpenCC 只動漢字，但順序寫死比較不會被改壞）。
            text = guardrails.to_traditional(text)
            # 確保回覆恰好含一句合規帶讀（漏句要補、格式跑掉要修、不得重複）
            return guardrails.ensure_readalong(text, target)
        except Exception:
            _log.exception("EdgeLLM generate 失敗，降級回 scaffold 回覆")
            return None
