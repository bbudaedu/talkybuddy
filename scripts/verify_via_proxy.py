# -*- coding: utf-8 -*-
"""用「Anthropic 相容中轉」當 LLM 後端，把 cloud_llm 的語義邏輯跑通。

用途：Bedrock daily token 配額用盡時，先用自架/中轉（Anthropic Messages API）
驗證「這套 prompt 設計 + 前後處理」在真 LLM 上會產出正確的陪聊回覆與導師 JSON。
前後處理全部呼叫 production 共用函式（deidentify / passes_guardrail /
_build_diagnosis_prompt / _validate_diagnosis / EdgeLLM._SYSTEM_PROMPT），
只有「呼叫 LLM 的傳輸層」換成中轉 —— 故驗的是真實邏輯，非 Bedrock code path。

環境變數（由呼叫端提供，本腳本不碰 token 明文）：
    ANTHROPIC_BASE_URL          中轉 base（如 http://192.168.100.200:8317）
    ANTHROPIC_AUTH_TOKEN        中轉 token（Bearer / x-api-key）
    ANTHROPIC_DEFAULT_HAIKU_MODEL   陪聊模型（預設 claude-haiku-4-5-20251001）
    ANTHROPIC_DEFAULT_SONNET_MODEL  導師模型（預設 claude-sonnet-5）
"""
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import guardrails, diagnose
from server.llm import EdgeLLM

_BASE = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
_TOKEN = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
_COMPANION_MODEL = os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5-20251001")
_TUTOR_MODEL = os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")


class _Scaffold:
    def __init__(self, target):
        self.target_sentence = target


def _hr(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def _messages_call(model, system, user_text, max_tokens=512, timeout=30):
    """打中轉的 Anthropic Messages API，回 parsed dict。"""
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_text}],
    }
    if system:
        body["system"] = system
    req = urllib.request.Request(
        _BASE + "/v1/messages",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": _TOKEN,
            "authorization": "Bearer " + _TOKEN,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _text_from(payload):
    for block in payload.get("content", []):
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def _show_config():
    _hr("設定（中轉）")
    print(f"ANTHROPIC_BASE_URL = {_BASE or '(未設)'}")
    print(f"AUTH_TOKEN         = {'已設(不回顯)' if _TOKEN else '(未設)'}")
    print(f"陪聊模型            = {_COMPANION_MODEL}")
    print(f"導師模型            = {_TUTOR_MODEL}")


def verify_companion():
    """複製 CloudLLM.generate 的邏輯，LLM 呼叫換成中轉。"""
    _hr("① 陪聊邏輯（deidentify → 中轉生成 → passes_guardrail → 帶讀補句）")
    target = "I see a cat"
    student = "我看到一隻貓，我的電話是0912345678"
    safe_student = guardrails.deidentify(str(student))
    print(f"原始學生話          = {student!r}")
    print(f"deidentify 後送雲    = {safe_student!r}")
    pii_masked = "0912345678" not in safe_student
    print(f"PII 遮罩檢查        = {pii_masked}")
    user_prompt = (
        f"學生剛剛說：「{safe_student}」\n"
        f"目標英文句：{target}\n"
        "請照規則回覆：先一句繁體中文稱讚鼓勵，"
        "再用「跟我說一遍：<英文句>」帶讀目標英文句。"
    )
    t0 = time.monotonic()
    try:
        payload = _messages_call(_COMPANION_MODEL, EdgeLLM._SYSTEM_PROMPT, user_prompt, max_tokens=300)
    except Exception as e:
        print(f"❌ 中轉呼叫失敗：{type(e).__name__}: {str(e)[:200]}")
        return False
    dt = time.monotonic() - t0
    text = _text_from(payload).strip()
    print(f"耗時 = {dt:.2f}s")
    print(f"LLM 原始回覆        = {text!r}")
    if not text:
        print("❌ 空回覆。")
        return False
    passes = guardrails.passes_guardrail(text)
    print(f"passes_guardrail    = {passes}")
    if not passes:
        print("❌ 命中護欄（真實情境會降級回本地）。")
        return False
    if target not in text:
        text = f"{text} 跟我說一遍：{target}"
        print(f"帶讀補句後          = {text!r}")
    ok = (target in text) and pii_masked and passes
    print("✅ 陪聊邏輯跑通：真回覆過護欄、含帶讀目標句、PII 已遮罩。" if ok else "⚠️ 部分檢查未過，見上。")
    return ok


def verify_tutor():
    """走 production _build_diagnosis_prompt + _validate_diagnosis，LLM 換中轉。"""
    _hr("② 導師邏輯（_build_diagnosis_prompt → 中轉 JSON → _validate_diagnosis）")
    interactions = [
        {"student_text": "I see a cat", "ai_response_text": "很好！跟我說一遍：I see a cat",
         "asr_confidence": 0.82, "scores": {"pronunciation": 60, "fluency": 55}},
        {"student_text": "The dog is big", "ai_response_text": "很棒！跟我說一遍：The dog is big",
         "asr_confidence": 0.75, "scores": {"pronunciation": 58, "fluency": 60}},
    ]
    prompt = diagnose._build_diagnosis_prompt(interactions, None)
    t0 = time.monotonic()
    try:
        payload = _messages_call(_TUTOR_MODEL, None, prompt, max_tokens=2000)
    except Exception as e:
        print(f"❌ 中轉呼叫失敗：{type(e).__name__}: {str(e)[:200]}")
        return False
    dt = time.monotonic() - t0
    text = _text_from(payload).strip()
    print(f"耗時 = {dt:.2f}s")
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    try:
        raw = json.loads(text)
    except Exception as e:
        print(f"❌ JSON 解析失敗：{e}；原始前 300 字：{text[:300]!r}")
        return False
    out = diagnose._validate_diagnosis(raw)
    print("_validate_diagnosis 後：")
    print(json.dumps(out, ensure_ascii=False, indent=2)[:1200])
    ok = (
        isinstance(out.get("scores"), dict)
        and all(k in out["scores"] for k in ("pronunciation", "fluency", "vocabulary", "grammar"))
        and isinstance(out.get("instructions"), dict)
        and all(k in out["instructions"] for k in ("classroom", "device", "peer"))
    )
    print(f"schema 必填欄位檢查 = {ok}")
    print("✅ 導師邏輯跑通：真 JSON 過 _validate_diagnosis、schema 完整。" if ok else "❌ schema 缺欄位。")
    return ok


def main():
    _show_config()
    if not _BASE or not _TOKEN:
        print("\n❌ 缺 ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN，無法呼叫中轉。")
        return 1
    results = {"陪聊邏輯": verify_companion(), "導師邏輯": verify_tutor()}
    _hr("結果總結")
    all_ok = all(results.values())
    for name, r in results.items():
        print(f"  {'✅' if r else '❌'} {name}")
    print("\n" + ("🎉 中轉語義邏輯全數跑通。" if all_ok else "⚠️ 有項目未過，見上方細節。"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
