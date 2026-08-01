# -*- coding: utf-8 -*-
"""pytest 共用 fixture。

- ``tmp_db``：每個測試都把 ``server.config.DB_PATH`` monkeypatch 到 tmp_path
  下的獨立 SQLite 檔案，並呼叫 ``store.init_db()`` 建表，避免測試互相汙染、
  也避免動到正式的 data/talkybuddy.db。autouse=True，所有測試都套用。
- ``anyio_backend``：把 anyio pytest 外掛（fastapi/starlette 帶入）鎖定在
  asyncio，本專案未安裝 trio，避免 anyio 額外嘗試 trio backend 而報錯。
"""

from __future__ import annotations

import pytest

from server import config, store


def pytest_configure(config):  # noqa: F811 - pytest hook
    """註冊自訂 marker，避免 PytestUnknownMarkWarning。"""
    config.addinivalue_line("markers", "slow: 需載入重模型或長時間執行的測試")


@pytest.fixture(autouse=True)
def _relax_aws_only_gate(monkeypatch):
    """測試期間關掉競賽合規閘門（``server/aws_only.py`` 預設是開的）。

    閘門是**部署期政策**，不是程式正確性。「Gemini／relay／ElevenLabs 這幾條
    路徑本身會不會正常運作」仍然必須測——它們沒有壞掉，只是競賽期間不准用，
    而且賽後要一行恢復。

    閘門自己的行為由 ``tests/test_aws_only_gate.py`` 專門驗證；那個檔會
    ``delenv`` 把環境變數移掉，藉此把「預設就是開」這個性質一起測到。
    """
    monkeypatch.setenv("TALKYBUDDY_AWS_ONLY", "0")


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """把 DB_PATH 導向 tmp 目錄，並建立乾淨的資料表。"""
    db_path = tmp_path / "talkybuddy_test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    store.init_db()
    yield db_path


@pytest.fixture(autouse=True)
def isolate_vocab():
    """每個測試後把全域 ``scaffold.VOCAB`` 還原成測試前的樣子。

    ``VOCAB`` 是模組層級的 dict，``register_material_vocab`` 刻意原地 mutate 它
    （homework/games/profile 都 ``from server.scaffold import VOCAB`` 拿同一個
    參照，原地改才不用動那些模組）。代價是它會**跨測試檔殘留**——只要有一條
    測試載入了 Unit 3~6 的教材種子，之後所有測試看到的詞庫就多出二十幾個
    課綱外的字。``tmp_db`` 只管 SQLite，管不到這個 dict。

    症狀是一批「單獨跑會過、整套跑就紅」的測試（test_curriculum_data 斷言
    詞庫幾乎全是官方字表、test_scaffold_vocab 斷言每個字都在官方清單裡），
    紅的原因與被測程式無關，純粹是前面某個檔案留下的殘留。

    連 ``_ZH_KEYS_BY_LEN`` 與 ``guardrails._safe_en_words`` 一起還原：
    register_material_vocab 會重建這兩份衍生快取，只還原 VOCAB 會讓它們
    停在含教材詞的版本，比沒還原更難察覺。
    """
    from server import guardrails, scaffold

    snapshot = {zh: dict(v) for zh, v in scaffold.VOCAB.items()}
    zh_keys = list(scaffold._ZH_KEYS_BY_LEN)
    yield
    scaffold.VOCAB.clear()
    scaffold.VOCAB.update(snapshot)
    scaffold._ZH_KEYS_BY_LEN[:] = zh_keys
    try:
        guardrails._safe_en_words.cache_clear()
    except AttributeError:  # 不是 lru_cache 包的版本，沒有快取要清
        pass


@pytest.fixture(autouse=True)
def clear_active_game():
    """清掉裝置級的遊戲狀態（``server.pipeline._active_game``）。

    遊戲狀態刻意掛在行程上而不是 pipeline 實例上（老師用 ``/api/game`` 開的局，
    孩子那條 ``/ws/talk`` 連線要看得到）。代價就是它會跨測試殘留——一條測試開的
    局漏給下一條，斷言「預設沒有遊戲」的測試會莫名其妙地紅。
    """
    from server import pipeline as pipeline_mod

    pipeline_mod._active_game = None
    yield
    pipeline_mod._active_game = None


@pytest.fixture
def anyio_backend():
    """限定 anyio 測試只跑 asyncio backend（未安裝 trio）。"""
    return "asyncio"
