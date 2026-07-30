# -*- coding: utf-8 -*-
"""兩個 edge client 不能同時佔用麥克風。

**這支測試存在的理由**（2026-07-30 實機事故）：`local_client`（回合式 /ws/talk）
與 `live_client`（S2S /ws/live）搶同一支 USB 麥克風。ALSA 對 capture 是獨佔的，
後起的那個拿到：

    arecord: audio open error: Device or resource busy

症狀是上行 0 bytes、玩偶完全沒反應——**與「按鍵故障」「收音門檻擋掉聲音」
長得一模一樣**。當天連續三輪測試因此全部無效，查了很久才發現兇手是另一個
service。而 `talkybuddy-local-client.service` 是 enabled 的，開機必定自動起來，
所以這不是「記得別同時開」就能避免的，必須由機制保證。

三道防線，各自對應一種啟動方式：

1. `Conflicts=`  → 用 `systemctl start` 起任一個，systemd 自動停掉另一個
2. 啟動守衛      → 手動 `python -m edge.runtime.live_client` 時 Conflicts 管不到
3. PDEATHSIG    → 父行程被 `pkill` 掉之後，arecord 不該變孤兒繼續佔著麥克風
"""

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from edge.runtime import audio_io, live_client

_DEPLOY = Path(__file__).resolve().parents[1] / "edge" / "deploy"
_LOCAL_UNIT = _DEPLOY / "talkybuddy-local-client.service"
_LIVE_UNIT = _DEPLOY / "talkybuddy-live-client.service"
_INSTALL_SH = _DEPLOY / "install_services.sh"


# ---------------------------------------------------------------------------
# 防線 1：systemd Conflicts=
# ---------------------------------------------------------------------------

def test_live_client_has_a_unit_at_all():
    """沒有 unit 檔就沒辦法用 Conflicts= 互斥。

    live_client 先前只能手動 `python -m` 啟動，systemd 對它一無所知，
    自然也無從幫忙擋掉衝突。
    """
    assert _LIVE_UNIT.is_file()


def test_the_two_clients_declare_each_other_as_conflicts():
    """兩邊都要宣告，不能只寫單邊。

    systemd 的 Conflicts= 雖然會隱含加上反向關係，但只寫一邊的話，讀另一個
    unit 檔的人看不到這個約束——而「不知道有另一個 client」正是當天踩坑的原因。
    """
    assert "Conflicts=talkybuddy-live-client.service" in _LOCAL_UNIT.read_text()
    assert "Conflicts=talkybuddy-local-client.service" in _LIVE_UNIT.read_text()


def test_live_client_does_not_start_at_boot():
    """開機預設必須是回合式那條可 demo 的路。

    S2S 體驗尚未收斂（自我迴音、半雙工閘門），不該在插電開機後自己跑起來。
    要用時再 `systemctl start talkybuddy-live-client`，Conflicts= 會順手把
    回合式停掉。
    """
    body = _INSTALL_SH.read_text()
    assert "ENABLE_UNITS=" in body, "安裝與 enable 必須是兩份清單"
    # live-client 要被安裝（否則沒得 start），但不得出現在 enable 清單裡
    assert "talkybuddy-live-client" in body
    enable_line = next(ln for ln in body.splitlines() if ln.startswith("ENABLE_UNITS="))
    assert "talkybuddy-live-client" not in enable_line


# ---------------------------------------------------------------------------
# 防線 2：啟動守衛（手動 python -m 時 Conflicts= 幫不上忙）
# ---------------------------------------------------------------------------

class _FakeRun:
    """假的 subprocess.run，只記下 argv 並回傳指定的 systemctl 輸出。"""

    def __init__(self, stdout="", returncode=0, raises=None):
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.argv = None

    def __call__(self, argv, **kwargs):
        self.argv = argv
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_guard_detects_the_running_turn_based_client():
    """`systemctl is-active` 回 active 就代表麥克風已被佔走。"""
    run = _FakeRun(stdout="active\n", returncode=0)
    assert live_client.local_client_is_active(run=run) is True
    assert "is-active" in run.argv
    assert "talkybuddy-local-client.service" in run.argv


def test_guard_is_quiet_when_the_other_client_is_stopped():
    """inactive 是正常狀態，不該擋。"""
    run = _FakeRun(stdout="inactive\n", returncode=3)
    assert live_client.local_client_is_active(run=run) is False


def test_guard_does_not_block_startup_when_systemctl_is_missing():
    """開發機或容器裡沒有 systemd 時，守衛要讓路而不是讓程式起不來。

    守衛的職責是「發現已知衝突就明講」，不是「無法確認就一律拒絕」——
    後者會讓這支程式在沒有 systemd 的環境完全不能跑。
    """
    run = _FakeRun(raises=FileNotFoundError("systemctl"))
    assert live_client.local_client_is_active(run=run) is False


def test_startup_refuses_with_an_actionable_message(monkeypatch):
    """擋下來的時候必須直接給出解法。

    當天真正浪費時間的不是「被擋住」，而是**根本不知道被誰擋住**。錯誤訊息
    裡要有可以直接複製貼上的指令。
    """
    monkeypatch.setattr(live_client, "local_client_is_active", lambda **_: True)
    with pytest.raises(RuntimeError) as exc:
        live_client.assert_exclusive_mic()
    msg = str(exc.value)
    assert "talkybuddy-local-client" in msg
    assert "systemctl stop" in msg


def test_startup_passes_when_nothing_else_holds_the_mic(monkeypatch):
    monkeypatch.setattr(live_client, "local_client_is_active", lambda **_: False)
    live_client.assert_exclusive_mic()  # 不該拋


# ---------------------------------------------------------------------------
# 防線 3：PR_SET_PDEATHSIG——子行程不該比父行程活得久
# ---------------------------------------------------------------------------

def test_mic_and_speaker_subprocesses_ask_to_die_with_the_parent(monkeypatch):
    """arecord/aplay 都要帶上 PDEATHSIG 的 preexec_fn。

    少了它，`pkill -f live_client` 之後 arecord 會變孤兒繼續獨佔麥克風，
    下一次啟動就拿到 Device or resource busy——看起來像硬體壞了。
    """
    seen = []

    class _FakeProc:
        stdout = stdin = None

        def poll(self):
            return None

    def fake_popen(argv, **kwargs):
        seen.append(kwargs.get("preexec_fn"))
        return _FakeProc()

    monkeypatch.setattr(live_client.subprocess, "Popen", fake_popen)
    live_client.MicSource("plughw:1,0")
    live_client.SpeakerSink("plughw:0,0")

    assert len(seen) == 2
    assert all(fn is audio_io.die_with_parent for fn in seen), \
        "arecord 與 aplay 都必須帶 preexec_fn=die_with_parent"


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="PR_SET_PDEATHSIG 是 Linux-only 的 prctl")
def test_child_actually_dies_when_the_parent_is_killed(tmp_path):
    """真的殺父行程，確認子行程跟著死。

    這是行為測試而非介面測試：`preexec_fn` 有傳不代表 prctl 真的生效
    （libc 名稱、常數值、signal 編號任一寫錯都會靜默失效）。當天的孤兒
    `arecord` 就是這條路徑的實際後果，值得用真行程驗一次。

    用 SIGKILL 殺父行程：這模擬 `pkill -9`，父行程沒有機會做任何清理，
    所以子行程活不活得下來完全取決於 PDEATHSIG。
    """
    marker = tmp_path / "child.pid"
    script = textwrap.dedent(f"""
        import subprocess, sys, time
        sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
        from edge.runtime.audio_io import die_with_parent
        p = subprocess.Popen(["sleep", "60"], preexec_fn=die_with_parent)
        open({str(marker)!r}, "w").write(str(p.pid))
        time.sleep(60)
    """)
    parent = subprocess.Popen([sys.executable, "-c", script])
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.05)
        assert marker.exists(), "子行程沒有起來，測試前提不成立"
        child_pid = int(marker.read_text())

        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except OSError:
                return  # 子行程已消失——PDEATHSIG 生效
            time.sleep(0.05)
        pytest.fail(f"父行程死了但子行程 {child_pid} 還活著——PDEATHSIG 沒生效")
    finally:
        if parent.poll() is None:
            parent.kill()
