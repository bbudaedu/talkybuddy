# -*- coding: utf-8 -*-
"""fetch_sherpa_wasm.py 的純函式（URL 組裝＋資產完整性驗證）單元測試。

重點：驗證器必須把「下載失敗但回 200 的 stub」擋下——例如 HF 的
`Invalid username or password.`（401 內文）或 ModelScope 阿里雲 WAF 的
HTML 挑戰頁——避免這類非資產內容被當成 .js/.wasm/.data 靜默寫入 vendor。
不碰網路。
"""
from __future__ import annotations

import importlib.util
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.normpath(os.path.join(_HERE, "..", "scripts", "fetch_sherpa_wasm.py"))


def _load():
    spec = importlib.util.spec_from_file_location("fetch_sherpa_wasm", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load()


# --- build_url ---------------------------------------------------------------

def test_build_url_joins_with_single_slash():
    assert mod.build_url("https://h/base/", "a.js") == "https://h/base/a.js"


def test_build_url_tolerates_missing_trailing_slash():
    assert mod.build_url("https://h/base", "a.js") == "https://h/base/a.js"


# --- sources / files 契約 -----------------------------------------------------

def test_files_are_the_four_expected_assets():
    assert mod.FILES == [
        "sherpa-onnx-kws.js",
        "sherpa-onnx-wasm-kws-main.js",
        "sherpa-onnx-wasm-kws-main.wasm",
        "sherpa-onnx-wasm-kws-main.data",
    ]


def test_sources_include_hf_and_modelscope_mirror():
    names = [s["name"] for s in mod.SOURCES]
    assert "huggingface" in names
    assert "modelscope" in names


# --- validate_asset：擋 stub ---------------------------------------------------

def test_validate_rejects_hf_401_body():
    with pytest.raises(ValueError):
        mod.validate_asset("sherpa-onnx-kws.js", b"Invalid username or password.\n")


def test_validate_rejects_html_challenge_page():
    html = b"<!DOCTYPE html>\n<html><head><title>x</title></head><body>wall</body></html>"
    with pytest.raises(ValueError):
        mod.validate_asset("sherpa-onnx-wasm-kws-main.js", html + b" " * 5000)


# --- validate_asset：glue 必須含 createKws -------------------------------------

def test_validate_glue_requires_createKws():
    good = b"// header\n" + b"x" * 3000 + b"\nfunction createKws(Module, cfg) { return 1; }\n"
    mod.validate_asset("sherpa-onnx-kws.js", good)  # 不應 raise
    bad = b"// header\n" + b"x" * 3000  # 夠大但沒有 factory
    with pytest.raises(ValueError):
        mod.validate_asset("sherpa-onnx-kws.js", bad)


# --- validate_asset：wasm magic ----------------------------------------------

def test_validate_wasm_requires_magic_bytes():
    good = b"\x00asm\x01\x00\x00\x00" + b"\x00" * 200000
    mod.validate_asset("sherpa-onnx-wasm-kws-main.wasm", good)  # 不應 raise
    bad = b"NOTWASM!" + b"\x00" * 200000
    with pytest.raises(ValueError):
        mod.validate_asset("sherpa-onnx-wasm-kws-main.wasm", bad)


# --- validate_asset：.data 需夠大（內含打包模型） -------------------------------

def test_validate_data_rejects_too_small():
    with pytest.raises(ValueError):
        mod.validate_asset("sherpa-onnx-wasm-kws-main.data", b"\x00" * 1024)


def test_validate_data_accepts_large_binary():
    mod.validate_asset("sherpa-onnx-wasm-kws-main.data", b"\x00" * 600000)


# --- validate_asset：main.js 合理大小的 JS ------------------------------------

def test_validate_main_js_accepts_plausible_js():
    js = b"var Module=typeof Module!=='undefined'?Module:{};" + b"a" * 60000
    mod.validate_asset("sherpa-onnx-wasm-kws-main.js", js)  # 不應 raise
