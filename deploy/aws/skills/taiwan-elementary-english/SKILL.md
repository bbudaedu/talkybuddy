---
name: taiwan-elementary-english
description: >-
  台灣國小英語文教學的共同依據：教育部領綱的字彙分級與主題、國小階段的
  語言形式上限、以及兒童安全用語。派作業／週報／決策判斷三個 agent 共用。
---

# 台灣國小英語文教學共同依據

> 這份 skill 由 `scripts/generate_agent_skill.py` 從
> `data/curriculum/moe_english_2018.json` 與專案常數產生，**請勿手改**。
> 要改內容請改來源資料後重跑腳本。

## 一、教材依據

十二年國民基本教育課程綱要 國民中小學暨普通型高級中等學校 語文領域－英語文（教育部（國家教育研究院發布頁），2018-04-16 發布）

- 官方檔案：https://www.naer.edu.tw/upload/1/16/doc/812/%E5%8D%81%E4%BA%8C%E5%B9%B4%E5%9C%8B%E6%B0%91%E5%9F%BA%E6%9C%AC%E6%95%99%E8%82%B2%E8%AA%B2%E7%A8%8B%E7%B6%B1%E8%A6%81%E5%9C%8B%E6%B0%91%E4%B8%AD%E5%B0%8F%E5%AD%B8%E6%9A%A8%E6%99%AE%E9%80%9A%E5%9E%8B%E9%AB%98%E7%B4%9A%E4%B8%AD%E7%AD%89%E5%AD%B8%E6%A0%A1%E8%AA%9E%E6%96%87%E9%A0%98%E5%9F%9F%E2%94%80%E8%8B%B1%E8%AA%9E%E6%96%87.odt
- 檔案 SHA-256：`f1f052187d85a16ba38e4c72330746275009c5025ec9e678f1456918f4aed4af`
- 擷取日期：2026-07-27

領綱明訂的國小畢業字彙量：**口語至少 300 個字詞、
書寫至少 180 個**。

出題、給例句、寫評語時，用字一律以這份字彙表為準。

## 二、字彙分級

| 表 | 數量 | 使用時機 |
|---|---|---|
| 基本 1,200 字 | 1211 筆 | **預設只用這個**。領綱：教材宜優先從最基本 1,200 字詞中選取 |
| 其他常用 800 字 | 794 筆 | 需要加深或加廣時才用 |

超出這 2,000 字的字彙：領綱允許「視其必要性斟酌選用」，但對國小階段
應視為例外，用了要在說明裡交代為什麼。

**不要自創單字表。** 需要某個主題的字彙時，從下列主題分類裡取。

## 三、主題與溝通功能（領綱附錄三、四）

教材主題（40 個）：

- Animals
- Appearance
- Home appliances
- Clothing/Accessories
- Colors
- Computers
- Customs & lifestyles
- Daily routines
- Eating out
- Environment & pollution
- Families, family relationships & kinship terms
- Famous or interesting people
- Famous or interesting places
- Food & drinks
- Friends & personal relationship
- Gender equality
- Health
- Holidays & festivals
- Houses & apartments
- Human rights
- Interests and hobbies
- Manners
- Money & prices
- Nation & languages
- Nature
- Neighborhood
- Numbers
- Occupations
- Parts of the body
- School life
- Shapes, sizes & measurements
- Shopping
- Special events
- Sports & exercise
- Study habits or plans
- Time, dates, months, seasons & years
- Transportation
- Traveling
- Weather & climate
- Science & technology

字彙分類主題（附錄五 表三，37 個，與上表是兩套分類，不要混用）：

- People
- Personal characteristics
- Parts of body
- Health
- Forms of address
- Family
- Numbers
- Time
- Money
- Food & drinks
- Tableware
- Clothing & accessories
- Colors
- Sports, interests & hobbies
- Houses & apartments
- School
- Places & locations
- Transportation
- Sizes & measurements
- Countries and areas
- Languages
- Holidays & festivals
- Occupations
- Weather & nature
- Geographical terms
- Animals & insects
- Articles & determiners
- Pronouns & reflexives
- Wh-words
- Be & auxiliaries
- Prepositions
- Conjunctions
- Interjections
- Other nouns
- Other verbs
- Other adjectives
- Other adverbs

溝通功能（45 條）——說明「這題在練什麼」時直接引用，
不要自己發明教學目標的說法：

- Asking about abilities
- Asking about ownership
- Asking about prices
- Asking about the time, the day, & the date
- Asking about transportation
- Asking for and giving advice
- Asking for and giving directions
- Asking for and giving information
- Asking for and giving instructions
- Asking for and giving permission
- Asking how things are said in English
- Asking how words are spelled
- Asking people to repeat or clarify something
- Checking & indicating understanding
- Comparing things, people, etc.
- Describing actions
- Describing people’s appearances
- Describing emotions and experiences
- Describing a sequence
- Expressing agreement & disagreement
- Expressing congratulations
- Expressing gratitude
- Expressing concern
- Expressing likes & dislikes
- Expressing prohibition
- Expressing wants and needs
- Extending, accepting, and declining invitations
- Getting attention
- Giving reasons
- Greeting people
- Introducing friends, family and oneself
- Making appointments
- Making apologies
- Making compliments
- Making plans
- Making requests
- Making suggestions
- Making telephone calls
- Naming common toys and household objects
- Offering and requesting help
- Ordering food & drinks
- Talking about location
- Talking about daily schedules and activities
- Talking about frequency
- Talking about past, present, and future events

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
| 1 | pre-A1 (Starters 前) | 單字 / 名詞片語（an apple） |
| 2 | pre-A1 (Starters) | 固定框架短句 3–4 詞（I see a dog.） |
| 3 | A1 (Movers) | 完整句＋冠詞/複數/形容詞（I see a big dog.） |
| 4 | A2 (Flyers 入門) | 擴充句：介系詞/連接詞/疑問句（I see a dog in the park.） |
| 5 | A2 (Flyers) | 複合句＋原因/時態（I like dogs because they are cute.） |

## 五、回應方式

- **recast（重述）優先於糾錯**：孩子說錯時，用正確的說法自然重述一次，
  不要停下來講解錯在哪裡。「I want eat apple.」→「喔～你想吃蘋果！
  I want to eat an apple.」
- **先肯定開口，再處理正確性**。願意開口是這個年紀最該獎勵的行為
- 提到分數時一定要說明分數代表什麼意義，不能只丟數字
- 面向家長／老師的文字（週報）用成人看的完整敘述，不要用對小孩說話的
  語氣，也不要堆砌英文教學術語

## 六、兒童安全用語（不可協商）

五、你的對象是國小兒童：不得談論暴力、血腥、成人、藥物、恐怖或色情內容；不得索取或覆述姓名、住址、電話、學校等個人資料。六、若學生表達難過、害怕、想傷害自己或被欺負，先用繁體中文溫柔安撫，鼓勵他告訴老師或家人，不要追問細節、不要給處置建議。

另外：

- **不要覆述孩子講過的姓名、住址、學校**，即使他自己說了。需要指稱時
  用「你」或「這位同學」
- 產出裡不得出現任何可識別到個人的資訊
