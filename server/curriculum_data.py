# -*- coding: utf-8 -*-
"""curriculum_data.py — 教育部英語文領綱的官方參考資料（唯讀查詢層）。

資料來源
--------
教育部《十二年國民基本教育課程綱要 國民中小學暨普通型高級中等學校
語文領域－英語文》107.04.16 發布版，由 ``scripts/extract_curriculum.py``
從官方 ODT 抽出，連同來源網址與檔案 SHA-256 一起存進
``data/curriculum/moe_english_2018.json``。

被問「你們的教材依據是什麼」時，答案是這份檔案裡的 ``source`` 區塊——
官方網址加雜湊值，可以當場重跑腳本驗證，而不是「我們參考了課綱」。

誠實的邊界（很重要，不要在簡報上講過頭）
----------------------------------------
- 領綱的參考字彙表是**國中小共用**的，官方**沒有**逐年級或逐學期的切分。
  它只載明：國小畢業時口語應至少會應用 300 個字詞、書寫至少 180 個。
- 各版本教科書（康軒／翰林／南一）的單元主題是出版社的著作，領綱不提供，
  本模組也不猜。要對應版本進度，得另外取得授權資料。
- 附錄六的文法句構表是**國中**階段的。領綱明文寫國小「僅止於簡易、常用的
  句型結構，避免過度解釋或分析文法」，所以那張表在這裡只當作上限參考，
  不是國小教學目標。

與既有 curriculum.py 的關係
---------------------------
``curriculum.py`` 的 CEFR 微階梯（BAND_CUTS / WEIGHTS）是本專案自己的
難度模型，不動它。本模組只提供「官方資料怎麼查」，兩者在
``vocab_for_band()`` 交會——**那個對應是本專案的推論，不是課綱的規定**，
函式文件裡也這麼寫。
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

_log = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "curriculum" / "moe_english_2018.json"


@lru_cache(maxsize=1)
def load() -> dict:
    """讀進官方資料（快取）。檔案缺失或壞掉時回空結構，不拋。

    這一層絕不能拋：出題與診斷都可能碰到它，而課綱資料是加值資訊，
    缺了應該退化成原本的行為，不是讓孩子的練習中斷。
    """
    try:
        with DATA_PATH.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        _log.warning("讀取課綱資料失敗（%s），本次以空資料運作", DATA_PATH, exc_info=True)
        return {}


def source_citation() -> str:
    """一句話的出處，可直接寫進簡報或 API 回應。"""
    src = load().get("source") or {}
    if not src:
        return "（課綱資料未載入）"
    return (
        f"{src.get('title', '')}（{src.get('publisher', '')}，"
        f"{src.get('published', '')} 發布）"
    )


def source_meta() -> dict:
    """完整來源資訊：網址、SHA-256、擷取日期、適用範圍的但書。"""
    return dict(load().get("source") or {})


@lru_cache(maxsize=1)
def basic_vocab() -> tuple[str, ...]:
    """附錄五 表一：基本 1,200 字。教材應優先從這裡選。"""
    return tuple(load().get("vocab_basic_1200") or ())


@lru_cache(maxsize=1)
def extra_vocab() -> tuple[str, ...]:
    """附錄五 表二：其他常用 800 字（加深加廣時才用）。"""
    return tuple(load().get("vocab_extra_800") or ())


@lru_cache(maxsize=1)
def _basic_index() -> frozenset:
    """基本字的查詢索引：小寫、去掉括號內的同義寫法。

    ``airplane (plane)`` 這種條目在表裡是一筆，但孩子講的是 airplane
    或 plane，兩個都該算命中。
    """
    out: set[str] = set()
    for entry in basic_vocab():
        # 整筆原樣也要查得到：表三是用同一種寫法列字的
        # （`father (dad, daddy)`），只收 head 會讓兩張表對不起來。
        out.add(entry.strip().lower())
        head = entry.split("(")[0].strip().lower()
        if head:
            out.add(head)
        inner = entry[entry.find("(") + 1:entry.rfind(")")] if "(" in entry else ""
        for alt in inner.split(","):
            alt = alt.strip().lower()
            if alt:
                out.add(alt)
    return frozenset(out)


def is_basic(word: str) -> bool:
    """這個字是否在基本 1,200 字內（大小寫、括號同義寫法都算）。"""
    return str(word or "").strip().lower() in _basic_index()


@lru_cache(maxsize=1)
def topics() -> tuple[str, ...]:
    """附錄三：主題參考表（教材編寫用的 40 個主題）。"""
    return tuple(load().get("topics") or ())


@lru_cache(maxsize=1)
def genres() -> tuple[str, ...]:
    """附錄三：體裁參考表。"""
    return tuple(load().get("genres") or ())


@lru_cache(maxsize=1)
def communicative_functions() -> tuple[str, ...]:
    """附錄四：溝通功能參考表。出題時的「這題在練什麼」可以直接引它。"""
    return tuple(load().get("communicative_functions") or ())


@lru_cache(maxsize=1)
def junior_high_grammar() -> tuple[str, ...]:
    """附錄六：**國中**基礎文法句構參考表（國小只當上限參考）。"""
    return tuple(load().get("junior_high_grammar") or ())


@lru_cache(maxsize=1)
def vocab_topics() -> tuple[str, ...]:
    """附錄五 表三的主題分類（與附錄三的教材主題是兩套分類，不要混用）。"""
    return tuple(t["topic"] for t in (load().get("vocab_by_topic") or []))


def vocab_for_topic(topic: str, *, basic_only: bool = True) -> tuple[str, ...]:
    """某個主題底下的字彙。``basic_only`` 預設只回基本 1,200 字。"""
    key = str(topic or "").strip().lower()
    for entry in load().get("vocab_by_topic") or []:
        if entry["topic"].lower() == key:
            return tuple(
                w["en"] for w in entry["words"] if (w["basic"] or not basic_only)
            )
    return ()


def elementary_targets() -> dict:
    """領綱明訂的國小畢業字彙量：口語 300、書寫 180。"""
    return dict(load().get("elementary_targets") or {})


# --- 與本專案難度模型的銜接（推論，非課綱規定）--------------------------------

# curriculum.py 的 band 1–5 是本專案自己的 CEFR 微階梯。課綱沒有把字彙分級到
# band，這裡的對應是**我們的教學推論**：低 band 只用基本字，高 band 才放行
# 加深加廣的 800 字。寫在這裡而不是 curriculum.py，是為了讓「課綱說的」與
# 「我們推論的」在程式碼裡也分得開。
_BAND_ALLOWS_EXTRA = 4


def vocab_for_band(band: int, topic: str | None = None) -> tuple[str, ...]:
    """某個難度 band 可用的字彙。

    band < 4 只給基本 1,200 字；band ≥ 4 才加上其他常用 800 字。
    **這個分界是本專案的推論，課綱本身沒有這條規則。**
    """
    try:
        band = int(band)
    except (TypeError, ValueError):
        band = 1
    if topic:
        return vocab_for_topic(topic, basic_only=band < _BAND_ALLOWS_EXTRA)
    if band < _BAND_ALLOWS_EXTRA:
        return basic_vocab()
    return basic_vocab() + extra_vocab()


def coverage(words) -> dict:
    """一組字對課綱基本字彙的覆蓋情形。

    用來回答「你們的題庫站得住腳嗎」：回傳落在基本 1,200 字內的數量、
    比例，以及不在表內的字（那些要嘛是專有名詞，要嘛該換掉）。
    """
    # 先濾掉 None 再轉字串：str(None) 是 "None"，會被當成一個字算進去
    items = [str(w).strip() for w in (words or []) if w is not None and str(w).strip()]
    inside = [w for w in items if is_basic(w)]
    outside = [w for w in items if not is_basic(w)]
    return {
        "total": len(items),
        "in_basic_1200": len(inside),
        "ratio": round(len(inside) / len(items), 4) if items else 0.0,
        "outside": outside,
    }
