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
import urllib.error
import urllib.request

from server import anthropic_relay, bedrock_converse, guardrails

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
    """雲端腦對話加值；憑證可解析即 available，consent/network 由 pipeline 把關。"""

    def available(self) -> bool:
        """任一後端（Bedrock 或 relay）可解析即 True；任何失敗回 False。"""
        try:
            if bedrock_converse.resolve_config() is not None:
                return True
            return anthropic_relay.resolve_config() is not None
        except Exception:
            return False

    def generate(self, student_text: str, scaffold, directive: str | None = None) -> str | None:
        """以雲端腦生成加值回覆；任何失敗、逾時、護欄命中回 None（絕不拋進 pipeline）。

        後端優先序：原生 Bedrock Converse → Anthropic 相容 relay。前者需
        ``TALKYBUDDY_CLOUD_PROVIDER=bedrock`` 才啟用，未切換時行為與過去完全一致。
        """
        try:
            # role="chat"：取為 _TIMEOUT_S（1.5s）挑的快模型。若取到診斷用的
            # 大模型，這條路徑會穩定逾時而永遠降級回 edge。
            bedrock_cfg = bedrock_converse.resolve_config(role="chat")
            cfg = anthropic_relay.resolve_config()
            if bedrock_cfg is None and cfg is None:
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
                return None
            # 輸出後置護欄（不安全→降級回 edge/scaffold）
            if not guardrails.passes_guardrail(text):
                return None
            # 先繁化（與 edge 同序）再跑帶讀護欄
            text = guardrails.to_traditional(text)
            # 帶讀恰好一句：漏句要補、格式跑掉要修、不得重複（與 edge 共用）
            return guardrails.ensure_readalong(text, target)
        except Exception:
            _log.exception("CloudLLM generate 失敗，降級回 edge/scaffold")
            return None
