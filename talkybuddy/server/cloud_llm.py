# -*- coding: utf-8 -*-
"""cloud_llm：雲端 LLM 大腦（Bedrock Converse, boto3）。

契約刻意對齊 server.llm.EdgeLLM：
- ``CloudLLM.available() -> bool``：provider=="bedrock" 且 boto3 client 可建。
- ``CloudLLM.generate(student_text, scaffold, directive=None) -> str | None``：
  逾時（>8s）、例外、空輸出、命中 guardrail 一律回 None → pipeline 降級回本地。

重依賴（boto3）lazy import；import 本模組絕不可炸（無 boto3 時仍要能啟動）。
"""
from __future__ import annotations

import logging
import time

from server import guardrails

_log = logging.getLogger(__name__)

_GENERATE_TIMEOUT_S = 8.0


def _cfg():
    from server import config
    return config


def _system_prompt() -> str:
    """重用 EdgeLLM 的鷹架 system prompt，避免兩份漂移（不重構 llm.py）。"""
    from server.llm import EdgeLLM
    return EdgeLLM._SYSTEM_PROMPT


def _get_client():
    """lazy 建 bedrock-runtime client；boto3 缺失/憑證問題一律回 None。"""
    try:
        import boto3  # lazy 重依賴
        cfg = _cfg()
        return boto3.client("bedrock-runtime", region_name=cfg.BEDROCK_REGION)
    except Exception:
        return None


def _extract_text(resp) -> str:
    """從 Converse 回應取第一個 text block；結構不符回空字串。"""
    try:
        for block in resp["output"]["message"]["content"]:
            if "text" in block:
                return block["text"] or ""
    except Exception:
        pass
    return ""


class CloudLLM:
    """Bedrock Converse 陪聊 provider；失敗一律優雅降級（回 None）。"""

    def available(self) -> bool:
        cfg = _cfg()
        if getattr(cfg, "LLM_CLOUD_PROVIDER", "off") != "bedrock":
            return False
        return _get_client() is not None

    def generate(self, student_text, scaffold, directive=None):
        start = time.monotonic()
        try:
            client = _get_client()
            if client is None:
                return None
            cfg = _cfg()
            target = getattr(scaffold, "target_sentence", None)
            safe_student = guardrails.deidentify(str(student_text))
            directive_block = (
                f"\n{directive.strip()}\n" if directive and directive.strip() else ""
            )
            user_prompt = (
                f"學生剛剛說：「{safe_student}」\n"
                f"目標英文句：{target or ''}\n"
                f"{directive_block}"
                "請照規則回覆：先一句繁體中文稱讚鼓勵，"
                "再用「跟我說一遍：<英文句>」帶讀目標英文句。"
            )
            resp = client.converse(
                modelId=cfg.COMPANION_MODEL_ID,
                system=[{"text": _system_prompt()}],
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                inferenceConfig={"maxTokens": 300, "temperature": 0.7},
            )
            if time.monotonic() - start > _GENERATE_TIMEOUT_S:
                return None
            text = _extract_text(resp).strip()
            if not text:
                return None
            if not guardrails.passes_guardrail(text):
                return None
            if target and target not in text:
                text = f"{text} 跟我說一遍：{target}"
            return text
        except Exception:
            _log.exception("CloudLLM.generate 失敗，降級回 scaffold/本地")
            return None
