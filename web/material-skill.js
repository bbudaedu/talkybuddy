/* material-skill.js — 教材萃取結果 → 聊天 agent 可調用的 metadata / SKILL.md。
 *
 * 為什麼抽成獨立模組而不是寫在 material.html 裡：這幾個函式是這頁唯一有
 * 邏輯的部分（其餘都是 DOM 接線），寫在 <script> 裡就永遠測不到。
 * 對應測試：web/material-skill.test.mjs（node --test）。
 *
 * 上游資料是 `POST /api/material` 的回傳，形狀見 server/agents/material.py：
 *     {topic, entries:[{en,zh,cat,np,sent}], accepted_count, rejected_count, source}
 */

/** 把 markdown 語法剝成純文字，再送進萃取 agent。
 *
 * 教材 agent 吃的是「教材文字」，不是 markdown。`#`、`**`、圍欄這些符號
 * 對它只是雜訊，還會被當成詞的一部分（`**sunny**` 不等於 `sunny`）。
 */
export function mdToPlainText(md) {
  if (!md) return "";
  return String(md)
    .split("\n")
    /* 圍欄行整行丟掉（連同 ```md 的語言標記），圍欄內的內容保留。 */
    .filter((line) => !/^\s*```/.test(line))
    .map((line) =>
      line
        .replace(/^\s*#{1,6}\s+/, "")            /* 標題符號 */
        .replace(/^\s*[-*+]\s+/, "")             /* 清單項目符號 */
        .replace(/^\s*>\s?/, "")                 /* 引言符號 */
        .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") /* 連結 → 連結文字 */
        .replace(/(\*\*|__)(.*?)\1/g, "$2")      /* 粗體 */
        .replace(/`([^`]*)`/g, "$1")             /* 行內程式碼 */
        .trim(),
    )
    .join("\n")
    .trim();
}

/** 組出聊天 agent 實際拿到的 metadata。
 *
 * `vocab` 刻意以**中文為鍵**、值為 `{en, cat, np, sent}`——那就是
 * `scaffold.VOCAB` 的真實結構（`register_material_vocab` 以
 * `VOCAB[zh] = {...}` 原地合併）。頁面展示的若不是這個形狀，看起來像
 * metadata、實際上跟玩偶拿到的東西對不起來。
 */
export function buildMetadata(result, title) {
  const r = result || {};
  const vocab = {};
  for (const e of r.entries || []) {
    if (!e || !e.zh) continue;
    vocab[e.zh] = { en: e.en, cat: e.cat, np: e.np, sent: e.sent };
  }
  return {
    title: title || "",
    topic: r.topic || "",
    source: r.source || "",
    accepted: r.accepted_count || 0,
    rejected: r.rejected_count || 0,
    vocab,
  };
}

/** 組出可掛上 AgentCore Harness 的 SKILL.md。
 *
 * 開頭那段但書是**必要的**，不是排版：詞條併進 VOCAB 是上傳當下就生效的，
 * 但這份 markdown 要 s3 sync + update-harness 才會生效。兩者混為一談就是
 * 在宣稱一件還沒發生的事。
 */
export function buildSkillMarkdown(result, title) {
  const r = result || {};
  const entries = r.entries || [];
  const rows = entries.map(
    (e) => `| ${e.zh} | ${e.en} | ${e.cat} | ${e.np} | ${e.sent} |`,
  );
  return [
    `# ${title || "未命名教材"} — 教材萃取產出的 skill`,
    "",
    `> 由「說說學伴」教材萃取 agent 產生（來源：${r.source || "unknown"}）。`,
    "> ",
    "> ⚠️ **這份檔案尚未掛上 AgentCore Harness。** 要生效需先 `aws s3 sync`",
    "> 到 skills bucket，再以 `update-harness` 重新掛載（注意它不是 patch",
    "> 語意，其餘欄位要一併重傳）。",
    "> ",
    "> 下表的詞條**則已在上傳當下併進 `scaffold.VOCAB`**，聊天玩偶下一輪",
    "> 就會使用——這兩件事的生效程度不同，不要混為一談。",
    "",
    `## 主題`,
    "",
    r.topic || "（未標註）",
    "",
    `## 目標詞彙（採用 ${r.accepted_count || 0} 條／退回 ${r.rejected_count || 0} 條）`,
    "",
    "| 中文 | 英文 | 分類 | 名詞片語 | 例句 |",
    "| --- | --- | --- | --- | --- |",
    ...rows,
    "",
    "## 帶讀規則",
    "",
    "- 孩子用中文講出上表的詞時，把它換成英文，並帶讀對應的完整例句。",
    "- 例句就是目標句，不要自行造新句——那會偏離本週單元。",
    "",
  ].join("\n");
}

/** 造一句「試講一句」用的學生話。
 *
 * 中英夾雜是真實孩子的說法，也正好是玩偶該接住的情境：認出英文詞、
 * 帶讀完整句。沒有詞條時回 null，呼叫端據此不送出（送空句只會浪費一輪）。
 */
export function buildProbeSentence(entries) {
  const list = entries || [];
  if (!list.length) return null;
  const first = list[0];
  if (!first || !first.en) return null;
  return `我今天學到 ${first.en}`;
}

/* ==========================================================================
 * 現場保命：真呼叫失敗或全數退回時的回退
 *
 * 這頁在台上只有一次機會，而它依賴的東西全都可能當場出事：現場網路是手機
 * 熱點、萃取要往 AgentCore／Bedrock 跑一趟、`/ws/talk` 還要再開一條
 * WebSocket。另外「全數退回」根本不是錯誤（詞已在字庫就會被擋，見
 * `scaffold._is_valid_material_entry`），但畫面會空一片，台上看起來就是壞了。
 *
 * **回退資料是 2026-08-02 用 `hackathon/課程Unit 8.md` 對 164 詞基準線實跑
 * 出來的真結果**（採用 8／退回 0），不是編的。之所以要強調：`source` 標成
 * 獨立的 `"demo"` 而不是冒用 `cloud`／`rule`，UI 會據此把徽章寫成「預備展示
 * 資料」——畫面上任何一刻都不會宣稱「這一輪真的跑了」。
 * ========================================================================== */

/** 這次的回傳需不需要換成回退資料。 */
export function needsFallback(result) {
  if (!result) return true;
  if (!(result.accepted_count > 0)) return true;
  return false;
}

export const FALLBACK_RESULT = {
  topic: "時間詞彙與週末活動安排",
  source: "demo",
  accepted_count: 8,
  rejected_count: 0,
  entries: [
    { zh: "早上", en: "morning", cat: "time", np: "the morning",
      sent: "I read a book in the morning." },
    { zh: "下午", en: "afternoon", cat: "time", np: "the afternoon",
      sent: "We play games in the afternoon." },
    { zh: "晚上", en: "evening", cat: "time", np: "the evening",
      sent: "Dad comes home in the evening." },
    { zh: "夜晚", en: "night", cat: "time", np: "night",
      sent: "I sleep at night." },
    { zh: "今天", en: "today", cat: "time", np: "today",
      sent: "Today is a sunny day." },
    { zh: "明天", en: "tomorrow", cat: "time", np: "tomorrow",
      sent: "Let's go swimming tomorrow." },
    { zh: "週末", en: "weekend", cat: "time", np: "the weekend",
      sent: "I visit Grandma on the weekend." },
    { zh: "每天", en: "every day", cat: "time", np: "every day",
      sent: "I drink milk every day." },
  ],
};

/** 試講連不上時的玩偶回覆，**從畫面上實際那批詞造**。
 *
 * 不能寫死：萃取可能是真的、只有試講掛掉，這時畫面上孩子說的是
 * 「我今天學到 beach」，回覆若固定講 morning，一問一答就對不起來。
 * 沒有詞條時才退回預備那批。句型比照 buildProbeSentence，兩者成對。
 */
export function buildFallbackReply(entries) {
  const list = (entries && entries.length) ? entries : FALLBACK_RESULT.entries;
  const e = list[0];
  return `哇，${e.en} 是${e.zh}，很棒的字呢！我們一起來說說看：`
    + `${e.sent} 你也試試看跟著說一次。`;
}
