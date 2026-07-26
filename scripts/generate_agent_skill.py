#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""generate_agent_skill.py — 產生 AgentCore Harness 用的自訂 skill。

背景
----
三個 agent（派作業／週報／決策判斷）都需要同一套教學共識：課綱依據、
字彙分級、國小的語言形式上限、兒童安全用語。塞進三份 system prompt 的話，
改一次要改三個地方，而且三份會慢慢漂開。AgentCore Harness 的 ``skills``
欄位就是為此存在的——掛一次，三個 harness 共用。

為什麼用產生的而不是手寫
------------------------
skill 的內容全部來自兩個既有來源：

- ``data/curriculum/moe_english_2018.json``（教育部領綱，官方抽取）
- ``server/guardrails.CHILD_SAFETY_CLAUSE`` 與 ``server/curriculum.py``
  的難度階梯（本專案既有常數）

手寫一份 markdown 的話，課綱資料更新或安全條款改字時，skill 會靜默過期，
而且沒有任何跡象。用產生的，重跑一次就同步，diff 也看得出改了什麼。

**內容邊界**：這份 skill 只陳述課綱寫了什麼、以及本專案既有的規則。
沒有任何一句是「我覺得國小英語該怎麼教」。

用法
----
    python3 scripts/generate_agent_skill.py

輸出：deploy/aws/skills/taiwan-elementary-english/SKILL.md

掛上 Harness（AWS 放行後才做，見檔尾的 README 區塊）：
    aws s3 sync deploy/aws/skills/ s3://<bucket>/skills/
    aws bedrock-agentcore-control update-harness --harness-id <id> \\
        --skills '[{"s3":{"uri":"s3://<bucket>/skills/taiwan-elementary-english/"}}]' \\
        --max-iterations 8 --max-tokens 1024 --timeout-seconds 60 ...

⚠️ ``update-harness`` **不是 patch 語意**：只傳部分欄位會讓其他欄位掉回
預設（本專案曾被它把 maxTokens 靜默重置成 None）。更新時務必一併重傳
model / maxIterations / maxTokens / timeoutSeconds / memory。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import curriculum, curriculum_data, guardrails  # noqa: E402

OUT_DIR = (
    Path(__file__).resolve().parent.parent
    / "deploy" / "aws" / "skills" / "taiwan-elementary-english"
)

_HEADER = """---
name: taiwan-elementary-english
description: >-
  台灣國小英語文教學的共同依據：教育部領綱的字彙分級與主題、國小階段的
  語言形式上限、以及兒童安全用語。派作業／週報／決策判斷三個 agent 共用。
---

# 台灣國小英語文教學共同依據

> 這份 skill 由 `scripts/generate_agent_skill.py` 從
> `data/curriculum/moe_english_2018.json` 與專案常數產生，**請勿手改**。
> 要改內容請改來源資料後重跑腳本。
"""


def _section_source() -> str:
    meta = curriculum_data.source_meta()
    targets = curriculum_data.elementary_targets()
    return f"""
## 一、教材依據

{curriculum_data.source_citation()}

- 官方檔案：{meta.get('url', '')}
- 檔案 SHA-256：`{meta.get('sha256', '')}`
- 擷取日期：{meta.get('extracted_at', '')}

領綱明訂的國小畢業字彙量：**口語至少 {targets.get('spoken_words')} 個字詞、
書寫至少 {targets.get('written_words')} 個**。

出題、給例句、寫評語時，用字一律以這份字彙表為準。
"""


def _section_vocab() -> str:
    basic = curriculum_data.basic_vocab()
    extra = curriculum_data.extra_vocab()
    return f"""
## 二、字彙分級

| 表 | 數量 | 使用時機 |
|---|---|---|
| 基本 1,200 字 | {len(basic)} 筆 | **預設只用這個**。領綱：教材宜優先從最基本 1,200 字詞中選取 |
| 其他常用 800 字 | {len(extra)} 筆 | 需要加深或加廣時才用 |

超出這 2,000 字的字彙：領綱允許「視其必要性斟酌選用」，但對國小階段
應視為例外，用了要在說明裡交代為什麼。

**不要自創單字表。** 需要某個主題的字彙時，從下列主題分類裡取。
"""


def _section_topics() -> str:
    topics = curriculum_data.topics()
    funcs = curriculum_data.communicative_functions()
    vocab_topics = curriculum_data.vocab_topics()
    return f"""
## 三、主題與溝通功能（領綱附錄三、四）

教材主題（{len(topics)} 個）：

{chr(10).join('- ' + t for t in topics)}

字彙分類主題（附錄五 表三，{len(vocab_topics)} 個，與上表是兩套分類，不要混用）：

{chr(10).join('- ' + t for t in vocab_topics)}

溝通功能（{len(funcs)} 條）——說明「這題在練什麼」時直接引用，
不要自己發明教學目標的說法：

{chr(10).join('- ' + f for f in funcs)}
"""


def _section_form() -> str:
    lines = []
    for band in range(1, 6):
        lines.append(f"| {band} | {curriculum._CEFR[band]} | {curriculum._TARGET_FORM[band]} |")
    return f"""
## 四、國小階段的語言形式上限

領綱本文（教材編選要點）明文：

> 國民小學教育階段**僅止於簡易、常用的句型結構，避免過度解釋或分析文法**。

所以：

- 附錄六的文法句構表是**國中**階段的，對國小只當上限參考，不是教學目標
- 不要出現文法術語的解釋（「這是現在完成式」）。要說也只說用法情境
- 句型由簡而繁，同一個句型先給核心用法，衍生用法留到之後

本專案的難度階梯（`server/curriculum.py`，band 1–5 是專案自訂，不是課綱規定）：

| Band | CEFR / YLE 對應 | 目標語言形式 |
|---|---|---|
{chr(10).join(lines)}
"""


def _section_pedagogy() -> str:
    return """
## 五、回應方式

- **recast（重述）優先於糾錯**：孩子說錯時，用正確的說法自然重述一次，
  不要停下來講解錯在哪裡。「I want eat apple.」→「喔～你想吃蘋果！
  I want to eat an apple.」
- **先肯定開口，再處理正確性**。願意開口是這個年紀最該獎勵的行為
- 提到分數時一定要說明分數代表什麼意義，不能只丟數字
- 面向家長／老師的文字（週報）用成人看的完整敘述，不要用對小孩說話的
  語氣，也不要堆砌英文教學術語
"""


def _section_safety() -> str:
    return f"""
## 六、兒童安全用語（不可協商）

{guardrails.CHILD_SAFETY_CLAUSE}

另外：

- **不要覆述孩子講過的姓名、住址、學校**，即使他自己說了。需要指稱時
  用「你」或「這位同學」
- 產出裡不得出現任何可識別到個人的資訊
"""


def main() -> None:
    body = (
        _HEADER
        + _section_source()
        + _section_vocab()
        + _section_topics()
        + _section_form()
        + _section_pedagogy()
        + _section_safety()
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "SKILL.md"
    out.write_text(body, encoding="utf-8")
    print(f"寫出 {out}（{len(body)} 字元）")


if __name__ == "__main__":
    main()
