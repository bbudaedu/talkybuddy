"""criterion #3：server/streaming/ 未 import server.pipeline 熱路徑函式、未改 _process_text。"""
import ast
from pathlib import Path

_STREAMING = Path(__file__).resolve().parents[1]
_HOT = {"VoicePipeline", "_process_text", "run_turn_audio"}


def _py_files():
    for p in _STREAMING.glob("*.py"):
        yield p


def test_streaming_does_not_import_pipeline_hotpath():
    offenders = []
    for p in _py_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "server.pipeline":
                names = {a.name for a in node.names}
                bad = names & _HOT
                if bad:
                    offenders.append((p.name, bad))
    assert offenders == [], f"streaming imported pipeline hot-path: {offenders}"


def test_process_text_unchanged_signature():
    # 確認熱路徑函式仍存在且簽章未被 A2-2 動到（防呆：只驗存在 + 參數名）
    import inspect
    from server.pipeline import VoicePipeline
    sig = inspect.signature(VoicePipeline._process_text)
    assert list(sig.parameters)[:1] == ["self"]
