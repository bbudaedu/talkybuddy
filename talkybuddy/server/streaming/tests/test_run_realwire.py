"""run_realwire.py 純組裝/錯誤處理 smoke test（不起真實音訊裝置）。"""
from server.streaming import run_realwire


def test_check_prerequisites_returns_list():
    missing = run_realwire.check_prerequisites()
    assert isinstance(missing, list)
    # 每個缺項需是可讀字串（非 traceback）
    assert all(isinstance(m, str) and m for m in missing)


def test_build_processors_shape():
    # 用假 transport（提供 input()/output() 回傳可辨識 sentinel），驗組裝順序不 crash、
    # 不需真裝置或 pyaudio。
    class _FakeProc:
        def __init__(self, tag):
            self.tag = tag

    class _FakeTransport:
        def input(self):
            return _FakeProc("input")
        def output(self):
            return _FakeProc("output")

    procs = run_realwire.build_processors(_FakeTransport())
    assert len(procs) == 5
    assert procs[0].tag == "input"
    assert procs[-1].tag == "output"
    # 中間三個＝STT、manager、TTS（型別非 None 即可）
    assert all(p is not None for p in procs)
