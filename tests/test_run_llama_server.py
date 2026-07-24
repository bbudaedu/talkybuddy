# -*- coding: utf-8 -*-
"""test_run_llama_server.py — build_llama_server_argv() 純函式單元測試（08-01）。

只測純函式，不呼叫 main()、不起真的 llama-server binary。驗證：
- --ctx-size 值 == 傳入的 ctx_size
- --host 值 == 傳入的 host（127.0.0.1）；host 省略時預設 127.0.0.1（T-08-01 防線）
- --model / --port / --threads 引數正確
- 純函式無副作用（呼叫不觸網、不啟動子行程、不讀檔）
"""

from __future__ import annotations

from edge.runtime.run_llama_server import build_llama_server_argv


def test_ctx_size_argv_matches_input():
    argv = build_llama_server_argv(model_path="/m.gguf", ctx_size=999, host="127.0.0.1", port=8080, threads=4)
    assert argv[argv.index("--ctx-size") + 1] == "999"


def test_host_argv_matches_input():
    argv = build_llama_server_argv(model_path="/m.gguf", ctx_size=999, host="127.0.0.1", port=8080, threads=4)
    assert "--host" in argv
    assert argv[argv.index("--host") + 1] == "127.0.0.1"


def test_host_defaults_to_loopback_when_omitted():
    """T-08-01：host 參數省略時必須預設 127.0.0.1（loopback），絕不對外可路由。"""
    argv = build_llama_server_argv(model_path="/m.gguf", ctx_size=512)
    assert argv[argv.index("--host") + 1] == "127.0.0.1"


def test_model_port_threads_argv_correct():
    argv = build_llama_server_argv(model_path="/m.gguf", ctx_size=999, host="127.0.0.1", port=8080, threads=4)
    assert argv[argv.index("--model") + 1] == "/m.gguf"
    assert argv[argv.index("--port") + 1] == "8080"
    assert argv[argv.index("--threads") + 1] == "4"


def test_returns_list_of_str():
    argv = build_llama_server_argv(model_path="/m.gguf", ctx_size=999, host="127.0.0.1", port=8080, threads=4)
    assert isinstance(argv, list)
    assert all(isinstance(item, str) for item in argv)


def test_pure_function_no_side_effects(monkeypatch):
    """呼叫 build_llama_server_argv 不得觸發任何子行程或網路呼叫。"""
    import subprocess
    import urllib.request

    def _boom(*args, **kwargs):
        raise AssertionError("build_llama_server_argv must not perform I/O")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    argv = build_llama_server_argv(model_path="/m.gguf", ctx_size=512, host="127.0.0.1", port=8080, threads=4)
    assert "--ctx-size" in argv
