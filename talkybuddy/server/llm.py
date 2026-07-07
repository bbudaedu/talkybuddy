# -*- coding: utf-8 -*-
"""EdgeLLM：邊緣 LLM 加值層（llama.cpp + Qwen2.5-1.5B GGUF Q4）。

契約（CONTRACTS.md）：
- ``available() -> bool``：模型檔存在且 llama_cpp 可載入才回 True。
- ``generate(student_text, scaffold) -> str | None``：
  逾時（>8 秒）、例外、未載入、或輸出命中 ``scaffold.safety_check`` 一律回 None；
  pipeline 以 scaffold.reply_text 為準，LLM 只是加值。

重依賴（llama_cpp）與 server.config / server.scaffold 全部 lazy import，
import 本模組絕不可炸（無模型時伺服器仍要能啟動＝降級路徑）。
"""

from __future__ import annotations

import logging
import threading
import time

_log = logging.getLogger(__name__)

# LLM 生成逾時上限（秒），超過即放棄並回 None
_GENERATE_TIMEOUT_S = 8.0


def _get_gguf_path():
    """取得 GGUF 模型路徑；config 尚未就緒時回 None（lazy import 保護）。"""
    try:
        from server import config
        return config.LLM_GGUF
    except Exception:
        return None


class EdgeLLM:
    """llama.cpp 邊緣 LLM，單例懶載入，失敗一律優雅降級。"""

    # 模型單例（全類別共享，避免重複載入 1.1GB 權重）
    _model = None
    _model_failed = False
    _lock = threading.Lock()

    # 台灣國小英語鷹架家教 system prompt
    _SYSTEM_PROMPT = (
        "你是台灣國小學生的英語鷹架家教，只用繁體中文和英文回覆。"
        "嚴格遵守以下規則："
        "一、第一句先用繁體中文稱讚或鼓勵學生。"
        "二、接著帶讀指定的目標英文句，格式必須是「跟我說一遍：<英文句>」，"
        "英文句必須逐字使用我提供的目標句，不可改寫。"
        "三、全部回覆總長不超過60個字。"
        "四、禁止使用 markdown 符號、emoji 表情、以及英文以外的其他外語。"
    )

    def available(self) -> bool:
        """llama_cpp 可匯入且模型檔存在時回 True；任何失敗回 False。"""
        if EdgeLLM._model is not None:
            return True
        if EdgeLLM._model_failed:
            return False
        try:
            import llama_cpp  # noqa: F401  # lazy import，僅驗證可載入
        except Exception:
            return False
        gguf = _get_gguf_path()
        try:
            return gguf is not None and gguf.exists()
        except Exception:
            return False

    def _get_model(self):
        """單例懶載入 llama.cpp 模型；失敗記錄旗標避免重複嘗試。"""
        if EdgeLLM._model is not None:
            return EdgeLLM._model
        if EdgeLLM._model_failed:
            return None
        with EdgeLLM._lock:
            # double-checked locking：拿到鎖後再確認一次
            if EdgeLLM._model is not None:
                return EdgeLLM._model
            if EdgeLLM._model_failed:
                return None
            try:
                from llama_cpp import Llama  # lazy import 重依賴
                gguf = _get_gguf_path()
                if gguf is None or not gguf.exists():
                    EdgeLLM._model_failed = True
                    return None
                EdgeLLM._model = Llama(
                    model_path=str(gguf),
                    # PC 原型記憶體充足，維持 1024；PLAN.md 要求 Genio 520 板上
                    # 移植時應降為 512 tokens，避免 CPU 端 LLM context 過大導致 OOM 崩潰。
                    n_ctx=1024,
                    n_threads=4,
                    verbose=False,
                )
                return EdgeLLM._model
            except Exception:
                _log.exception("EdgeLLM 模型載入失敗，之後將降級為 scaffold-only")
                EdgeLLM._model_failed = True
                return None

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
            model = self._get_model()
            if model is None:
                return None
            # 載入若已吃掉逾時額度就直接放棄
            if time.monotonic() - start > _GENERATE_TIMEOUT_S:
                return None

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
            result = model.create_chat_completion(
                messages=[
                    {"role": "system", "content": self._SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=120,
                temperature=0.7,
            )
            if time.monotonic() - start > _GENERATE_TIMEOUT_S:
                return None

            text = (result["choices"][0]["message"]["content"] or "").strip()
            if not text:
                return None

            # 輸出仍須通過安全檢查（lazy import，避免循環依賴）
            try:
                from server import scaffold as scaffold_mod
                if scaffold_mod.safety_check(text):
                    return None
            except Exception:
                # 安全模組不可用時保守起見放棄 LLM 輸出
                return None

            # 確保目標英文句一定出現在回覆中（帶讀不可漏句）
            if target and target not in text:
                text = f"{text} 跟我說一遍：{target}"
            return text
        except Exception:
            _log.exception("EdgeLLM generate 失敗，降級回 scaffold 回覆")
            return None
