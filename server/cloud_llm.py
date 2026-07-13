# -*- coding: utf-8 -*-
"""CloudLLM：對話回覆的雲端腦加值層（Anthropic 相容 Messages API，可接自架中轉）。

契約與 EdgeLLM 一致（available/generate），任何失敗一律回 None 讓 pipeline 降級。
純標準函式庫（urllib），import 期不觸網、不載重依賴。上雲前對學生文字去識別化，
輸出過 guardrails 後置護欄。端點/認證/model 由 anthropic_relay 解析（與 diagnose 共用 env）。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from server import anthropic_relay, guardrails

_log = logging.getLogger(__name__)

# 雲端呼叫逾時（秒）；與 pipeline 外層 LLM_TIMEOUT_S 對齊，雙保險。
_TIMEOUT_S = 8.0
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
    """雲端腦對話加值；憑證可解析即 available，consent/network 由 pipeline 把關。"""

    def available(self) -> bool:
        """憑證可解析（resolve_config 非 None）即 True；任何失敗回 False。"""
        try:
            return anthropic_relay.resolve_config() is not None
        except Exception:
            return False

    def generate(self, student_text: str, scaffold, directive: str | None = None) -> str | None:
        """以雲端腦生成加值回覆；任何失敗、逾時、護欄命中回 None（絕不拋進 pipeline）。"""
        try:
            cfg = anthropic_relay.resolve_config()
            if cfg is None:
                return None
            target = getattr(scaffold, "target_sentence", None)
            safe_text = guardrails.deidentify(student_text)  # 上雲前去識別化
            directive_block = (
                f"\n{directive.strip()}\n" if directive and directive.strip() else ""
            )
            user_prompt = (
                f"學生剛剛說：「{safe_text}」\n"
                f"目標英文句：{target or ''}\n"
                f"{directive_block}"
                "請照規則回覆：先一句繁體中文稱讚鼓勵，"
                "再用「跟我說一遍：<英文句>」帶讀目標英文句。"
            )
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

            text = _extract_text(payload).strip()
            if not text:
                return None
            # 輸出後置護欄（不安全→降級回 edge/scaffold）
            if not guardrails.passes_guardrail(text):
                return None
            # 帶讀不可漏句：目標英文句一定要在回覆中
            if target and target not in text:
                text = f"{text} 跟我說一遍：{target}"
            return text
        except Exception:
            _log.exception("CloudLLM generate 失敗，降級回 edge/scaffold")
            return None
