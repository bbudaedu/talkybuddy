import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mdToPlainText,
  buildMetadata,
  buildSkillMarkdown,
  buildProbeSentence,
  needsFallback,
  FALLBACK_RESULT,
  buildFallbackReply,
} from "./material-skill.js";

/* /api/material 的真實回傳形狀（見 server/agents/material.py 的公開契約）。 */
function sampleResult() {
  return {
    topic: "天氣與心情",
    source: "cloud",
    accepted_count: 2,
    rejected_count: 1,
    entries: [
      { en: "sunny", zh: "晴天", cat: "weather", np: "a sunny day",
        sent: "It's sunny today." },
      { en: "rainy", zh: "雨天", cat: "weather", np: "a rainy day",
        sent: "It's rainy today." },
    ],
  };
}

/* ---------------- mdToPlainText ---------------- */

test("mdToPlainText 去掉標題符號，保留標題文字", () => {
  assert.equal(mdToPlainText("# Unit 3 Weather"), "Unit 3 Weather");
});

test("mdToPlainText 去掉粗體與行內程式碼標記", () => {
  assert.equal(mdToPlainText("**sunny** 是 `晴天`"), "sunny 是 晴天");
});

test("mdToPlainText 保留清單項目的文字，去掉項目符號", () => {
  assert.equal(mdToPlainText("- sunny 晴天\n- rainy 雨天"),
               "sunny 晴天\nrainy 雨天");
});

test("mdToPlainText 把連結換成連結文字", () => {
  assert.equal(mdToPlainText("看 [課本](http://x.test/a) 第三課"),
               "看 課本 第三課");
});

test("mdToPlainText 丟掉程式碼圍欄那幾行，留下內容", () => {
  assert.equal(mdToPlainText("```md\nsunny\n```"), "sunny");
});

/* ---------------- buildMetadata ---------------- */

test("buildMetadata 的 vocab 以中文為鍵，對齊 scaffold.VOCAB 的真實結構", () => {
  /* VOCAB[zh] = {en, cat, np, sent}——metadata 若不是這個形狀，
     頁面上展示的就不是聊天 agent 真正拿到的東西。 */
  const meta = buildMetadata(sampleResult(), "Unit 3");
  assert.deepEqual(meta.vocab["晴天"], {
    en: "sunny", cat: "weather", np: "a sunny day", sent: "It's sunny today.",
  });
});

test("buildMetadata 帶上 title/topic/source 與採用退回計數", () => {
  const meta = buildMetadata(sampleResult(), "Unit 3");
  assert.equal(meta.title, "Unit 3");
  assert.equal(meta.topic, "天氣與心情");
  assert.equal(meta.source, "cloud");
  assert.equal(meta.accepted, 2);
  assert.equal(meta.rejected, 1);
});

test("buildMetadata 對空詞條回傳空 vocab 而不是爆掉", () => {
  const meta = buildMetadata(
    { topic: "x", source: "rule", accepted_count: 0, rejected_count: 3, entries: [] },
    "空的");
  assert.deepEqual(meta.vocab, {});
  assert.equal(meta.accepted, 0);
});

/* ---------------- buildSkillMarkdown ---------------- */

test("buildSkillMarkdown 每個詞條都要出現在輸出裡", () => {
  const md = buildSkillMarkdown(sampleResult(), "Unit 3");
  for (const w of ["sunny", "晴天", "rainy", "雨天", "It's sunny today."]) {
    assert.ok(md.includes(w), `SKILL.md 少了 ${w}`);
  }
});

test("buildSkillMarkdown 標明教材標題與主題", () => {
  const md = buildSkillMarkdown(sampleResult(), "Unit 3");
  assert.ok(md.includes("Unit 3"));
  assert.ok(md.includes("天氣與心情"));
});

test("buildSkillMarkdown 必須寫明這份檔案尚未掛上 Harness", () => {
  /* 這不是排版要求，是不能讓評審誤會的界線：詞條併進 VOCAB 是上傳當下
     就生效的，但 SKILL.md 要 s3 sync + update-harness 才會生效。
     少了這句話，這頁就在宣稱一件沒發生的事。 */
  const md = buildSkillMarkdown(sampleResult(), "Unit 3");
  assert.ok(md.includes("尚未"), "SKILL.md 沒有標明尚未掛上 Harness");
  assert.ok(md.includes("update-harness"), "SKILL.md 沒有寫出掛載方式");
});

/* ---------------- buildProbeSentence ---------------- */

test("buildProbeSentence 用第一個詞條造出一句中英夾雜的學生話", () => {
  const s = buildProbeSentence(sampleResult().entries);
  assert.ok(s.includes("sunny"), `試講句沒帶到目標詞：${s}`);
});

test("buildProbeSentence 沒有詞條時回 null，呼叫端據此不送出", () => {
  assert.equal(buildProbeSentence([]), null);
  assert.equal(buildProbeSentence(undefined), null);
});

/* ---------------- 回退（現場保命） ---------------- */

test("needsFallback：呼叫失敗回 null 時要回退", () => {
  assert.equal(needsFallback(null), true);
  assert.equal(needsFallback(undefined), true);
});

test("needsFallback：一條都沒採用時要回退", () => {
  /* 全數退回不是錯誤，是正常保護機制（詞已在字庫）。但畫面會空一片，
     台上看起來就是壞了。這種情況也要接手。 */
  assert.equal(needsFallback({ accepted_count: 0, entries: [] }), true);
});

test("needsFallback：有採用到就用真結果，不准偷換", () => {
  const real = { accepted_count: 3, entries: [{ en: "beach", zh: "海邊" }] };
  assert.equal(needsFallback(real), false);
});

test("FALLBACK_RESULT 的 source 不是 cloud 也不是 rule", () => {
  /* 這兩個值都代表「這一輪真的跑了」。回退資料若冒用，畫面上的徽章就在
     說謊。必須是獨立的第三種狀態，讓 UI 標成「預備展示資料」。 */
  assert.notEqual(FALLBACK_RESULT.source, "cloud");
  assert.notEqual(FALLBACK_RESULT.source, "rule");
  assert.equal(FALLBACK_RESULT.source, "demo");
});

test("FALLBACK_RESULT 是完整可渲染的結果，不會讓畫面空掉", () => {
  assert.ok(FALLBACK_RESULT.entries.length >= 4, "回退資料詞條太少，畫面撐不起來");
  assert.ok(FALLBACK_RESULT.topic, "回退資料缺 topic");
  for (const e of FALLBACK_RESULT.entries) {
    for (const k of ["zh", "en", "cat", "np", "sent"]) {
      assert.ok(e[k], `回退詞條缺欄位 ${k}：${JSON.stringify(e)}`);
    }
  }
});

test("回退資料可以直接餵進 buildMetadata / buildSkillMarkdown", () => {
  const meta = buildMetadata(FALLBACK_RESULT, "Unit 8");
  assert.ok(Object.keys(meta.vocab).length >= 4);
  const md = buildSkillMarkdown(FALLBACK_RESULT, "Unit 8");
  assert.ok(md.includes(FALLBACK_RESULT.entries[0].en));
});

test("buildFallbackReply 用畫面上實際那批詞造回覆，不是寫死的", () => {
  /* 試講也可能連不上（/ws/talk 走 WebSocket，現場是手機熱點）。
     但萃取可能是真的、只有試講掛掉——這時回退回覆若寫死講 morning，
     孩子畫面上說的是「我今天學到 beach」，兩段就對不起來了。 */
  const real = [{ zh: "海邊", en: "beach", cat: "place", np: "the beach",
                  sent: "We swim at the beach in summer." }];
  const reply = buildFallbackReply(real);
  assert.ok(reply.includes("beach"), `沒帶到目標詞：${reply}`);
  assert.ok(reply.includes("We swim at the beach in summer."),
            `沒帶到該詞的例句：${reply}`);
});

test("buildFallbackReply 與 buildProbeSentence 講的是同一個詞", () => {
  /* 這兩個一起構成畫面上的一問一答，用的必須是同一個詞條。 */
  const es = FALLBACK_RESULT.entries;
  const asked = buildProbeSentence(es);
  const reply = buildFallbackReply(es);
  assert.ok(reply.includes(es[0].en));
  assert.ok(asked.includes(es[0].en));
});

test("buildFallbackReply 沒有詞條時退回預備那批，不回空字串", () => {
  const reply = buildFallbackReply([]);
  assert.ok(reply.includes(FALLBACK_RESULT.entries[0].en), reply);
});
