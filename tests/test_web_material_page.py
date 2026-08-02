# -*- coding: utf-8 -*-
"""教材上傳頁 `/material`：路由與頁面骨架。

這頁的價值是**把既有的 `POST /api/material` 那條鏈變成看得見的**：
老師丟一份 .md → material agent 萃取 → 詞條併進 `scaffold.VOCAB` →
聊天玩偶下一輪就會用。功能早就通了，先前完全沒有介面。

本檔只驗「頁面送得出去、關鍵區塊在」；萃取邏輯本身由
`tests/test_agent_material.py` 與 `tests/test_app_material.py` 涵蓋，
前端組裝邏輯由 `web/material-skill.test.mjs` 涵蓋。
"""
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from server.app import app

WEB_DIR = Path(__file__).resolve().parents[1] / "web"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_material_page_is_served(client):
    """`/material` 要回得出 HTML——沒有這條路由，整頁等於不存在。"""
    resp = client.get("/material")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_material_page_loads_the_skill_module(client):
    """頁面必須載入 `material-skill.js`。

    SKILL.md 與 metadata 的組裝邏輯刻意抽成獨立模組（才測得到），
    頁面忘了引用的話那些邏輯就是死碼，而畫面上看起來只是「按了沒反應」。
    """
    assert "material-skill.js" in resp_text(client)


def test_material_page_has_the_three_output_sections(client):
    """三段產出（萃取結果／metadata／SKILL.md）的容器都要在。

    這三段是這頁存在的理由。少任何一段，demo 就講不完
    「教材 → 萃取 → 聊天 agent 可調用」這條鏈。
    """
    html = resp_text(client)
    for anchor in ("data-section=\"entries\"",
                   "data-section=\"metadata\"",
                   "data-section=\"skill\""):
        assert anchor in html, f"缺少產出區塊：{anchor}"


def test_material_page_has_upload_and_probe_controls(client):
    """檔案輸入與「試講一句」按鈕都要在。

    試講是「聊天 agent 真的可調用」的唯一硬證據——它打的是真正的
    `/ws/talk`，不是模擬。沒有這顆按鈕，這頁就只是個表單。
    """
    html = resp_text(client)
    assert 'id="mdFile"' in html
    assert 'accept=".md' in html
    assert 'id="probeBtn"' in html


def test_login_overlay_can_actually_be_hidden(client):
    """`.hidden` 必須贏得過 `.overlay` 的 display 宣告。

    第一版寫成 `.hidden{display:none}` 在前、`.overlay{display:flex}` 在後，
    兩者 specificity 相同（都是單一 class），後定義的贏——所以登入成功後
    `classList.add('hidden')` 完全沒有效果，遮罩永遠蓋在畫面上，整頁不能用。

    這個 bug 所有結構測試都抓不到（class 有加上去、元素也都在），是實際
    開瀏覽器看畫面才發現的。唯一與宣告順序無關的修法是用複合選擇器
    `.overlay.hidden`（specificity 0,2,0）壓過 `.overlay`（0,1,0）。
    """
    html = resp_text(client)
    assert ".overlay.hidden" in html, (
        "缺少 .overlay.hidden 規則——.hidden 會被 .overlay 的 display 蓋掉")


def test_page_explains_why_entries_get_rejected(client):
    """頁面要說明「退回」是什麼意思。

    `_is_valid_material_entry` 對 `zh` 已存在於 VOCAB 的詞條一律拒絕——
    教材只能新增、不能覆蓋課綱詞。所以同一份教材上傳第二次會出現
    「採用 1／退回 5」（實測就是這個數字）。台上看到一個大大的退回數
    會像是壞掉，但它其實是保護機制在運作。沒有這段說明，正常行為會被
    當成故障。
    """
    html = resp_text(client)
    assert "退回" in html
    assert "已在字庫" in html or "已存在" in html, "頁面沒有解釋退回的原因"


def test_login_prefill_is_a_real_tutor_account(client):
    """登入框預填的帳號必須真的存在，而且角色是 tutor。

    寫錯了不會有任何測試失敗，症狀是現場按下登入顯示「帳號或密碼錯誤」
    ——上台前最不想 debug 的東西。第一版就寫成 `teacher@demo`（實際是
    `tutor@demo`），靠這條抓到。
    """
    from server import auth

    html = resp_text(client)
    tutors = [row[0] for row in auth._SEED if row[3] == "tutor"]
    assert tutors, "auth 沒有任何 tutor 帳號，測試前提不成立"
    assert any(f'value="{acct}"' in html for acct in tutors), (
        f"登入框預填的帳號不在 tutor 帳號清單 {tutors} 內")


def test_teacher_dashboard_links_to_the_material_page(client):
    """教師儀表板要有進得去這一頁的入口。

    這頁本身有「← 回教師儀表板」，但反方向沒有連結的話，老師（與台上的
    demo）只能靠手打網址 `/material` 才進得來——等於這個功能藏起來了。
    """
    html = client.get("/teacher").text
    assert 'href="/material"' in html, "教師儀表板沒有連到教材上傳頁的入口"


def test_material_skill_module_file_exists():
    """`web/material-skill.js` 要真的存在（頁面引用得到）。"""
    assert (WEB_DIR / "material-skill.js").is_file()


def resp_text(client) -> str:
    return client.get("/material").text
