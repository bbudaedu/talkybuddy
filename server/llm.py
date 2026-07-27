# -*- coding: utf-8 -*-
"""EdgeLLM：邊緣 LLM 加值層（llama-server 獨立行程 + Qwen2.5-1.5B GGUF Q4，經 HTTP client 呼叫）。

契約（CONTRACTS.md）：
- ``available() -> bool``：llama-server /health 短逾時 GET 回 200 才回 True；
  連線被拒/逾時/任何例外一律回 False，絕不拋出。
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
        """對 llama-server /health 發短逾時 GET，200 才回 True；任何失敗回 False。"""
        try:
            base = _llama_server_base_url()
            req = urllib.request.Request(f"{base}/health", method="GET")
            with urllib.request.urlopen(req, timeout=_HEALTH_TIMEOUT_S) as resp:
                return resp.status == 200
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

            directive_block = (
                f"\n{directive.strip()}\n" if directive and directive.strip() else ""
            )
            user_prompt = (
                f"學生剛剛說：「{student_text}」\n"
                f"目標英文句：{target or ''}\n"
                f"{directive_block}"
                "請照規則回覆：先一句繁體中文稱讚鼓勵，"
                "再用「跟我說一遍：<英文句>」帶讀目標英文句。"
            )
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

            # 確保目標英文句一定出現在回覆中（帶讀不可漏句）
            if target and target not in text:
                text = f"{text} 跟我說一遍：{target}"
            return text
        except Exception:
            _log.exception("EdgeLLM generate 失敗，降級回 scaffold 回覆")
            return None
