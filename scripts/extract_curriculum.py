#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""extract_curriculum.py — 從教育部官方 ODT 抽出英語文領綱的四個附錄。

來源（國家教育研究院，教育部 107.04.16 發布）：
    https://www.naer.edu.tw/PageSyllabus?fid=52
      → 領域/科目課程綱要 → 語文領域-英語文、第二外國語文 → ODT

抽出的四張表：
    附錄三  主題與體裁參考表
    附錄四  溝通功能參考表
    附錄五  參考字彙表
              表一 基本 1,200 字（依字母排列）
              表二 其他常用 800 字（依字母排列）
              表三 依**主題**分類的 2,000 字（劃底線者為基本 1,200 字）
    附錄六  國民中學英語文基礎文法句構參考表

表三 是這份資料最有價值的部分：它把 2,000 字掛在 44 個主題底下，正好對應
scaffold.VOCAB 的 cat 欄位。原文用「劃底線」標示基本 1,200 字，底線是排版
樣式不是文字，所以這裡改用**與表一取交集**來判定字級——結果等價，而且
不必解析 ODT 的樣式表。

為什麼寫成腳本而不是手抄成 .py：
- **可稽核**：決賽被問「教材依據是什麼」時，答案是這支腳本 + 官方檔案的
  SHA-256，不是「我記得課綱大概有這些字」。
- **可重跑**：課綱改版（例如 111 學年度修訂）時重跑一次即可，不必人工比對。

用法：
    python3 scripts/extract_curriculum.py            # 從官方網址下載後抽取
    python3 scripts/extract_curriculum.py <odt路徑>  # 用本地已下載的檔案

輸出：data/curriculum/moe_english_2018.json
"""

from __future__ import annotations

import datetime
import hashlib
import html
import json
import re
import sys
import zipfile
from pathlib import Path

ODT_URL = (
    "https://www.naer.edu.tw/upload/1/16/doc/812/"
    "%E5%8D%81%E4%BA%8C%E5%B9%B4%E5%9C%8B%E6%B0%91%E5%9F%BA%E6%9C%AC%E6%95%99%E8%82%B2"
    "%E8%AA%B2%E7%A8%8B%E7%B6%B1%E8%A6%81%E5%9C%8B%E6%B0%91%E4%B8%AD%E5%B0%8F%E5%AD%B8"
    "%E6%9A%A8%E6%99%AE%E9%80%9A%E5%9E%8B%E9%AB%98%E7%B4%9A%E4%B8%AD%E7%AD%89%E5%AD%B8"
    "%E6%A0%A1%E8%AA%9E%E6%96%87%E9%A0%98%E5%9F%9F%E2%94%80%E8%8B%B1%E8%AA%9E%E6%96%87.odt"
)

OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "curriculum" / "moe_english_2018.json"

# 段落／儲存格結束才斷行。若把所有標籤都換成換行，同一個詞被拆進兩個
# <text:span>（ODT 為了記錄格式常這麼做）就會斷成 "ca" + "mpus"。
_BLOCK_END = re.compile(r"</(text:p|text:h|table:table-cell|text:list-item)>")
_TAG = re.compile(r"<[^>]+>")


def odt_to_lines(odt_bytes: bytes) -> list[str]:
    """ODT → 以段落／儲存格為單位的文字行。"""
    with zipfile.ZipFile(__import__("io").BytesIO(odt_bytes)) as zf:
        xml = zf.read("content.xml").decode("utf-8")
    xml = xml.replace("<text:s/>", " ").replace("<text:tab/>", "\t")
    xml = xml.replace("<text:line-break/>", "\n")
    text = _BLOCK_END.sub("\n", xml)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def _slice(lines: list[str], start_pat: str, end_pat: str) -> list[str]:
    """取出 [start_pat, end_pat) 之間的行。找不到起點直接失敗，不猜。"""
    starts = [i for i, ln in enumerate(lines) if re.match(start_pat, ln)]
    if not starts:
        raise SystemExit(f"找不到段落起點：{start_pat}（課綱格式可能已改版）")
    start = starts[-1]
    ends = [i for i in range(start + 1, len(lines)) if re.match(end_pat, lines[i])]
    end = ends[0] if ends else len(lines)
    return lines[start + 1:end]


def _split_words(chunk: list[str]) -> list[str]:
    """字彙表：逗號分隔，跳過 "A-" 這種字母分節標題與 "---" 分組線。

    括號在原文裡有兩種用法，逗號切開後都會斷掉，必須接回去：
      airplane (plane)              → 同義／縮寫，括號內是另一個寫法
      you (your, yours, yourself)   → 一整組相關詞

    判準是括號配對：只要左括號還沒閉合，下一段就接在同一個詞上。
    """
    words: list[str] = []
    for line in chunk:
        s = line.strip()
        if not s or s == "---" or re.fullmatch(r"[A-Z]-", s):
            continue
        s = s.lstrip("-").strip()
        pending = ""
        for raw in s.split(","):
            part = raw.strip().strip("、")
            if not part or re.fullmatch(r"[A-Z]-", part):
                continue
            cur = f"{pending}, {part}" if pending else part
            if cur.count("(") > cur.count(")"):
                pending = cur          # 括號還沒收，繼續黏下一段
                continue
            pending = ""
            words.append(cur)
        if pending:
            words.append(pending)
    return words


_TOPIC_HEAD = re.compile(r"^(\d{1,2})\.\s*(.+)$")


def _parse_topic_vocab(chunk: list[str]) -> list[dict]:
    """表三：「N. 主題名」開頭，之後每個 --- 行是該主題的一組字彙。"""
    out: list[dict] = []
    current: dict | None = None
    for line in chunk:
        head = _TOPIC_HEAD.match(line.strip())
        if head:
            current = {"index": int(head.group(1)), "topic": head.group(2).strip(),
                       "words": []}
            out.append(current)
            continue
        if current is None:
            continue
        current["words"].extend(_split_words([line]))
    return out


def main() -> None:
    if len(sys.argv) > 1:
        raw = Path(sys.argv[1]).read_bytes()
        origin = str(Path(sys.argv[1]).resolve())
    else:
        import urllib.request
        with urllib.request.urlopen(ODT_URL, timeout=60) as resp:
            raw = resp.read()
        origin = ODT_URL

    digest = hashlib.sha256(raw).hexdigest()
    lines = odt_to_lines(raw)

    topics_block = _slice(lines, r"^主題：", r"^體裁：")
    genres_block = _slice(lines, r"^體裁：", r"^附錄四")
    functions = _slice(lines, r"^附錄四", r"^附錄五")
    basic_block = _slice(lines, r"^表一、基本", r"^表二、")
    extra_block = _slice(lines, r"^表二、", r"^表三、")
    topic_block = _slice(lines, r"^表三、", r"^附錄六")
    grammar = _slice(lines, r"^附錄六", r"^$")

    basic = _split_words(basic_block)
    basic_set = {w.lower() for w in basic}
    topic_vocab = _parse_topic_vocab(topic_block)
    for entry in topic_vocab:
        entry["words"] = [
            {"en": w, "basic": w.lower() in basic_set} for w in entry["words"]
        ]

    data = {
        "source": {
            "title": "十二年國民基本教育課程綱要 國民中小學暨普通型高級中等學校 語文領域－英語文",
            "publisher": "教育部（國家教育研究院發布頁）",
            "published": "2018-04-16",
            "url": ODT_URL,
            "origin": origin,
            "sha256": digest,
            "extracted_at": datetime.date.today().isoformat(),
            "note": (
                "附錄五 是國中小共用的參考字彙表；領綱本文載明國小畢業時"
                "口語應至少會應用 300 個字詞、書寫至少 180 個。"
                "領綱**不提供**逐年級／逐學期的字彙切分，也不含各版本教科書的單元主題。"
            ),
        },
        "elementary_targets": {"spoken_words": 300, "written_words": 180},
        "topics": [t for t in topics_block if t],
        "genres": [g for g in genres_block if g],
        "communicative_functions": [f for f in functions if not f.startswith("附錄")],
        "vocab_basic_1200": basic,
        "vocab_extra_800": _split_words(extra_block),
        "vocab_by_topic": topic_vocab,
        "junior_high_grammar": [g for g in grammar if not g.startswith("附錄")],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"寫出 {OUT_PATH}")
    for key in ("topics", "genres", "communicative_functions",
                "vocab_basic_1200", "vocab_extra_800", "vocab_by_topic",
                "junior_high_grammar"):
        print(f"  {key}: {len(data[key])}")
    total = sum(len(t["words"]) for t in topic_vocab)
    basic_hits = sum(1 for t in topic_vocab for w in t["words"] if w["basic"])
    print(f"  主題字彙總數: {total}（其中基本 1,200 字: {basic_hits}）")
    print(f"  sha256: {digest}")


if __name__ == "__main__":
    main()
