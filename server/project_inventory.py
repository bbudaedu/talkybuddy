# -*- coding: utf-8 -*-
"""project_inventory.py — 盤點任一專案根目錄（預設 ``~/hackathon``）的檔案組成。

提供三個核心函式：

- :func:`iter_files` — 遞迴列出根目錄底下所有一般檔案（自動略過 ``.git``／
  ``.venv``／``__pycache__`` 等雜訊目錄）。
- :func:`summarize_project` — 統計檔案總數／總大小／副檔名分布，並擷取所有
  README 檔案的開頭摘要，組成 :class:`ProjectSummary`。
- :func:`format_summary` — 把 :class:`ProjectSummary` 轉成人類可讀的文字報告。

也提供 :func:`main` 作為 CLI 進入點：``python -m server.project_inventory``
會列出 ``~/hackathon`` 底下的檔案並印出摘要報告。
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# 掃描時預設跳過的目錄名稱：版本控制／虛擬環境／快取等與「專案內容」無關
# 的雜訊，跳過可大幅加速掃描（例如 talkybuddy/.venv 或 models/ 內動輒數百 MB）。
DEFAULT_SKIP_DIR_NAMES: frozenset[str] = frozenset({
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".gstack",
})

# 視為 README 的檔名（不分大小寫比對）。
README_FILENAMES: frozenset[str] = frozenset({"readme.md", "readme.txt", "readme", "readme.rst"})

# README 摘要擷取的最大字元數，避免大檔案整包塞進報告。
README_EXCERPT_CHARS: int = 500

# format_summary 預設顯示的副檔名排行數量。
TOP_EXTENSIONS_DISPLAYED: int = 15

# 找不到副檔名（例如 Dockerfile、LICENSE）時使用的標籤。
NO_EXTENSION_LABEL: str = "(no extension)"

# 人類可讀檔案大小的進位單位。
_SIZE_UNITS: tuple[str, ...] = ("B", "KB", "MB", "GB", "TB")
_BYTES_PER_UNIT: float = 1024.0

# CLI 預設掃描根目錄。
DEFAULT_ROOT: Path = Path.home() / "hackathon"


@dataclass(frozen=True)
class ReadmeExcerpt:
    """單一 README 檔案的相對路徑與內容摘要。"""

    relative_path: str
    excerpt: str


@dataclass(frozen=True)
class ProjectSummary:
    """某個專案根目錄的檔案盤點結果。"""

    root: Path
    total_files: int
    total_dirs: int
    total_size_bytes: int
    extension_counts: dict[str, int]
    top_level_entries: list[str]
    readmes: list[ReadmeExcerpt]


def _walk_pruned(
    root: Path, skip_dir_names: frozenset[str]
) -> list[tuple[Path, list[str], list[str]]]:
    """``os.walk`` 包裝：依 ``skip_dir_names`` 原地剪枝子目錄。

    回傳 ``(目錄路徑, 子目錄名稱, 檔案名稱)`` 的清單，剪枝後就不會再往
    ``.venv``／``__pycache__`` 等目錄底下遞迴，避免掃描到大量無關檔案。
    """
    entries: list[tuple[Path, list[str], list[str]]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in skip_dir_names)
        entries.append((Path(dirpath), dirnames, sorted(filenames)))
    return entries


def _validate_root(root: Path) -> None:
    """確認 ``root`` 存在且為目錄，否則丟出對應例外。"""
    if not root.exists():
        raise FileNotFoundError(f"root directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"root is not a directory: {root}")


def iter_files(
    root: Path, *, skip_dir_names: frozenset[str] = DEFAULT_SKIP_DIR_NAMES
) -> list[Path]:
    """遞迴列出 ``root`` 底下所有一般檔案（略過 ``skip_dir_names`` 中的目錄）。

    回傳結果依路徑排序，確保輸出穩定、方便測試比對。

    Raises:
        FileNotFoundError: ``root`` 不存在。
        NotADirectoryError: ``root`` 存在但不是目錄。
    """
    _validate_root(root)
    files = [
        dirpath / name
        for dirpath, _dirnames, filenames in _walk_pruned(root, skip_dir_names)
        for name in filenames
    ]
    return sorted(files)


def _safe_file_size(path: Path) -> int:
    """回傳檔案大小（bytes）；讀取失敗（例如壞掉的符號連結）時回傳 0。"""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _find_readmes(root: Path, files: list[Path]) -> list[ReadmeExcerpt]:
    """從 ``files`` 中找出 README 檔案並擷取開頭摘要。"""
    readmes: list[ReadmeExcerpt] = []
    for file_path in files:
        if file_path.name.lower() not in README_FILENAMES:
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        readmes.append(
            ReadmeExcerpt(
                relative_path=str(file_path.relative_to(root)),
                excerpt=text[:README_EXCERPT_CHARS].strip(),
            )
        )
    return sorted(readmes, key=lambda r: r.relative_path)


def summarize_project(
    root: Path, *, skip_dir_names: frozenset[str] = DEFAULT_SKIP_DIR_NAMES
) -> ProjectSummary:
    """掃描 ``root`` 並回傳檔案總數／大小／副檔名分布／README 摘要。

    ``root`` 會先展開 ``~`` 並轉為絕對路徑，因此可直接傳入
    ``Path("~/hackathon")`` 這類含使用者目錄縮寫的路徑。
    """
    resolved_root = root.expanduser().resolve()
    _validate_root(resolved_root)

    files = iter_files(resolved_root, skip_dir_names=skip_dir_names)
    extension_counts: Counter[str] = Counter(
        file_path.suffix.lower() or NO_EXTENSION_LABEL for file_path in files
    )
    total_size = sum(_safe_file_size(file_path) for file_path in files)
    total_dirs = sum(
        len(dirnames) for _dirpath, dirnames, _filenames in _walk_pruned(resolved_root, skip_dir_names)
    )
    top_level_entries = sorted(
        entry.name
        for entry in resolved_root.iterdir()
        if entry.name not in skip_dir_names
    )
    readmes = _find_readmes(resolved_root, files)

    return ProjectSummary(
        root=resolved_root,
        total_files=len(files),
        total_dirs=total_dirs,
        total_size_bytes=total_size,
        extension_counts=dict(extension_counts.most_common()),
        top_level_entries=top_level_entries,
        readmes=readmes,
    )


def _human_readable_size(num_bytes: int) -> str:
    """把位元組數轉成 ``KB``／``MB``／``GB`` 等易讀字串。"""
    size = float(num_bytes)
    for unit in _SIZE_UNITS[:-1]:
        if size < _BYTES_PER_UNIT:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= _BYTES_PER_UNIT
    return f"{size:.1f} {_SIZE_UNITS[-1]}"


def format_summary(
    summary: ProjectSummary, *, top_extensions: int = TOP_EXTENSIONS_DISPLAYED
) -> str:
    """把 :class:`ProjectSummary` 轉成人類可讀的多行文字報告。"""
    lines = [
        f"Project root: {summary.root}",
        f"Total files: {summary.total_files}",
        f"Total directories: {summary.total_dirs}",
        f"Total size: {_human_readable_size(summary.total_size_bytes)}",
        "",
        f"Top-level entries ({len(summary.top_level_entries)}):",
    ]
    lines.extend(f"  - {name}" for name in summary.top_level_entries)

    lines.append("")
    lines.append(f"Top {top_extensions} file extensions:")
    for extension, count in list(summary.extension_counts.items())[:top_extensions]:
        lines.append(f"  {extension}: {count}")

    if summary.readmes:
        lines.append("")
        lines.append("README files found:")
        lines.extend(f"  - {readme.relative_path}" for readme in summary.readmes)

    return "\n".join(lines)


def _build_arg_parser() -> argparse.ArgumentParser:
    """建立 CLI 參數解析器。"""
    parser = argparse.ArgumentParser(
        description="List files under a project root and print a summary report."
    )
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=DEFAULT_ROOT,
        help="Root directory to scan (default: ~/hackathon).",
    )
    parser.add_argument(
        "--list-files",
        action="store_true",
        help="Also print every discovered file path before the summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 進入點：掃描指定目錄（預設 ``~/hackathon``）並印出摘要報告。

    Returns:
        程序結束碼：成功為 0，根目錄不存在／不是目錄則為 1。
    """
    args = _build_arg_parser().parse_args(argv)
    root = args.root.expanduser().resolve()

    try:
        summary = summarize_project(root)
    except (FileNotFoundError, NotADirectoryError) as exc:
        print(f"Error: {exc}")
        return 1

    if args.list_files:
        for file_path in iter_files(root):
            print(file_path)
        print()

    print(format_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
