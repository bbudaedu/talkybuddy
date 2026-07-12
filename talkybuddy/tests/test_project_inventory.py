# -*- coding: utf-8 -*-
"""test_project_inventory.py — server/project_inventory.py 的檔案盤點工具測試。

以 ``tmp_path`` 建立一個假的專案目錄結構（含一個 README、一個要略過的
``.venv`` 目錄、以及數個不同副檔名的檔案），驗證：

- ``iter_files`` 正確列出檔案且略過雜訊目錄。
- ``summarize_project`` 統計數字（檔案數／目錄數／大小／副檔名分布／README）正確。
- ``format_summary`` 產生的報告內容涵蓋關鍵資訊。
- ``main`` CLI 進入點在正常與根目錄不存在兩種情境下的行為。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server import project_inventory


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    """建立一個小型假專案目錄樹，回傳其根目錄路徑。

    結構::

        root/
            README.md
            main.py
            notes.txt
            sub/
                app.py
                data.json
            .venv/
                lib/ignored.py      # 應被略過
            __pycache__/
                ignored.pyc         # 應被略過
    """
    root = tmp_path / "fake_project"
    (root / "sub").mkdir(parents=True)
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / "__pycache__").mkdir(parents=True)

    (root / "README.md").write_text("# Fake Project\n\nA tiny demo project for tests.", encoding="utf-8")
    (root / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (root / "notes.txt").write_text("some notes", encoding="utf-8")
    (root / "sub" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "sub" / "data.json").write_text("{}", encoding="utf-8")
    (root / ".venv" / "lib" / "ignored.py").write_text("ignored", encoding="utf-8")
    (root / "__pycache__" / "ignored.pyc").write_text("ignored", encoding="utf-8")

    return root


# ---------------------------------------------------------------------------
# iter_files
# ---------------------------------------------------------------------------


def test_iter_files_lists_all_non_skipped_files(fake_project: Path):
    """iter_files 應列出所有真實檔案，且略過 .venv / __pycache__ 底下的內容。"""
    files = project_inventory.iter_files(fake_project)
    relative = sorted(str(f.relative_to(fake_project)) for f in files)

    assert relative == [
        "README.md",
        "main.py",
        "notes.txt",
        str(Path("sub") / "app.py"),
        str(Path("sub") / "data.json"),
    ]


def test_iter_files_missing_root_raises_file_not_found(tmp_path: Path):
    """根目錄不存在時應丟出 FileNotFoundError。"""
    missing = tmp_path / "does_not_exist"
    with pytest.raises(FileNotFoundError):
        project_inventory.iter_files(missing)


def test_iter_files_root_is_a_file_raises_not_a_directory(tmp_path: Path):
    """根路徑存在但是檔案（非目錄）時應丟出 NotADirectoryError。"""
    file_as_root = tmp_path / "just_a_file.txt"
    file_as_root.write_text("x", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        project_inventory.iter_files(file_as_root)


# ---------------------------------------------------------------------------
# summarize_project
# ---------------------------------------------------------------------------


def test_summarize_project_counts_files_and_extensions(fake_project: Path):
    """summarize_project 統計的檔案數／副檔名分布應正確，且略過 .venv/__pycache__。"""
    summary = project_inventory.summarize_project(fake_project)

    assert summary.total_files == 5
    assert summary.extension_counts[".py"] == 2
    assert summary.extension_counts[".md"] == 1
    assert summary.extension_counts[".txt"] == 1
    assert summary.extension_counts[".json"] == 1
    assert summary.total_size_bytes > 0
    # .venv 與 __pycache__ 本身仍是頂層目錄項目之一？不，應該被排除在 top_level_entries 外。
    assert ".venv" not in summary.top_level_entries
    assert "__pycache__" not in summary.top_level_entries
    assert "README.md" in summary.top_level_entries
    assert "sub" in summary.top_level_entries


def test_summarize_project_finds_readme_excerpt(fake_project: Path):
    """summarize_project 應找到 README.md 並擷取其內容摘要。"""
    summary = project_inventory.summarize_project(fake_project)

    assert len(summary.readmes) == 1
    readme = summary.readmes[0]
    assert readme.relative_path == "README.md"
    assert "Fake Project" in readme.excerpt


def test_summarize_project_expands_user_and_resolves_path(fake_project: Path):
    """summarize_project 回傳的 root 應為展開後的絕對路徑。"""
    summary = project_inventory.summarize_project(fake_project)
    assert summary.root == fake_project.resolve()


def test_summarize_project_missing_root_raises(tmp_path: Path):
    """根目錄不存在時 summarize_project 也應丟出 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        project_inventory.summarize_project(tmp_path / "nope")


# ---------------------------------------------------------------------------
# format_summary
# ---------------------------------------------------------------------------


def test_format_summary_contains_key_sections(fake_project: Path):
    """format_summary 的輸出應包含檔案數、副檔名統計與 README 區塊。"""
    summary = project_inventory.summarize_project(fake_project)
    report = project_inventory.format_summary(summary)

    assert "Total files: 5" in report
    assert "Top" in report and "file extensions" in report
    assert ".py: 2" in report
    assert "README files found:" in report
    assert "README.md" in report


def test_format_summary_respects_top_extensions_limit(fake_project: Path):
    """top_extensions 參數應限制列出的副檔名筆數上限。"""
    summary = project_inventory.summarize_project(fake_project)
    report = project_inventory.format_summary(summary, top_extensions=1)

    extension_lines = [line for line in report.splitlines() if line.startswith("  .")]
    assert len(extension_lines) == 1


# ---------------------------------------------------------------------------
# main (CLI)
# ---------------------------------------------------------------------------


def test_main_prints_summary_and_returns_zero(fake_project: Path, capsys: pytest.CaptureFixture):
    """main 對存在的目錄應回傳 0，並印出摘要報告。"""
    exit_code = project_inventory.main([str(fake_project)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Total files: 5" in captured.out


def test_main_with_list_files_prints_each_file(fake_project: Path, capsys: pytest.CaptureFixture):
    """--list-files 應在摘要之前逐一印出每個檔案路徑。"""
    exit_code = project_inventory.main(["--list-files", str(fake_project)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "main.py" in captured.out
    assert "Total files: 5" in captured.out


def test_main_missing_root_prints_error_and_returns_one(tmp_path: Path, capsys: pytest.CaptureFixture):
    """根目錄不存在時 main 應回傳 1 並印出錯誤訊息，而不是丟例外。"""
    missing = tmp_path / "no_such_dir"
    exit_code = project_inventory.main([str(missing)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Error" in captured.out
