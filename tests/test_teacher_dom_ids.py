"""教師儀表板：JS 讀取的 DOM id 必須真的存在於 markup。

2026-08-01 整片空白的真正原因就藏在這裡：`53eaba9` 在 renderClass() 加了
`getElementById('clsAttn')`，卻沒有在 KPI 列補上對應的節點。getElementById
回 null，`.textContent = ...` 拋 TypeError，refresh() 的 catch 把它靜靜吞掉——
於是 renderAll() 從此再也沒被呼叫過，整個儀表板一片空白，而 console 乾乾淨淨。

那次查錯先往後端找（診斷資料確實也有一個 student_id 不對稱的真 bug，已於
e502c7d 修掉），修完卻還是空的，因為畫面空白從頭到尾都是前端這一行造成的。

這個測試只做一件事：把 JS 讀的 id 和 HTML 定義的 id 對起來。純文字比對，不需要
瀏覽器，跑起來是毫秒等級——比再花一輪 session 查「為什麼是空的」便宜太多。
"""

import re
from pathlib import Path

import pytest

TEACHER_HTML = Path(__file__).resolve().parent.parent / "web" / "teacher.html"

# 反向（HTML 有、JS 沒 getElementById）不檢查：那些是用 querySelector 從
# 子樹裡取的，例如 trendTip / trendCross 走 box.querySelector('#trendTip')。


def _read_source() -> str:
    return TEACHER_HTML.read_text(encoding="utf-8")


def _defined_ids(src: str) -> set[str]:
    return set(re.findall(r'id="([A-Za-z][\w-]*)"', src))


def _used_ids(src: str) -> set[str]:
    """getElementById 的引數（單雙引號都收）。"""
    single = re.findall(r"getElementById\(\s*'([\w-]+)'\s*\)", src)
    double = re.findall(r'getElementById\(\s*"([\w-]+)"\s*\)', src)
    return set(single) | set(double)


def test_teacher_html_exists() -> None:
    assert TEACHER_HTML.is_file(), f"找不到 {TEACHER_HTML}"


def test_every_getelementbyid_target_exists_in_markup() -> None:
    src = _read_source()
    used = _used_ids(src)
    assert used, "解析不到任何 getElementById，正則可能已與檔案脫節"

    missing = sorted(used - _defined_ids(src))
    assert not missing, (
        "這些 id 有 JS 在讀，但 markup 裡沒有——getElementById 會回 null，"
        f"下一行取屬性就拋 TypeError 並讓整頁停止渲染：{missing}"
    )


@pytest.mark.parametrize(
    "element_id",
    ["clsTopic", "clsSize", "clsDone", "clsAttn", "clsBehind", "clsAvg", "clsRows"],
)
def test_class_overview_kpi_nodes_present(element_id: str) -> None:
    """renderClass() 會逐一寫入這些節點，缺任何一個都會讓整個儀表板空白。"""
    assert f'id="{element_id}"' in _read_source(), (
        f"全班進度卡缺少 id={element_id}；renderClass() 會在這一格拋錯，"
        "而 refresh() 的 catch 會把錯誤吞掉，畫面看起來只是「沒資料」"
    )
