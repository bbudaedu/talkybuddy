export const meta = {
  name: 'talkybuddy-mvp-prototype',
  description: '建置「說說學伴」決賽 MVP 可互動 Web 原型（學生端＋教師儀表板）',
  phases: [
    { title: 'Build', detail: '學生端與教師端並行建置' },
    { title: 'QA', detail: '逐檔語法/契約/離線檢查與修正' },
    { title: 'Integrate', detail: '跨檔整合驗證與 README' },
  ],
}

const DIR = '/home/budaedu/hackathon/demo-mvp'
const SPECS = `專案背景文件（先讀這三份再動工）：
- /home/budaedu/hackathon/說說學伴_28天決賽MVP規劃書.md
- /home/budaedu/hackathon/說說學伴_技術SPEC_v2.md
- /home/budaedu/hackathon/說說學伴_決賽評分對照與demo腳本.md`

const CONTRACT = `## 共用資料契約（兩端必須逐字一致，不得自行改名）
localStorage keys（皆為 JSON 字串）：
1. "ssxb_interactions" — 互動紀錄陣列，元素 schema：
{
  "seq": 1,                          // 單調遞增整數（冪等鍵）
  "device_id": "GENIO-520-X992",
  "student_id": "STUDENT-AMING-004",
  "ts": "2026-07-03T10:24:13+08:00", // ISO 字串
  "network_mode": "edge" | "cloud",
  "student_text": "I want to eat 蘋果",
  "asr_confidence": 0.92,
  "ai_response_text": "跟我說一遍：I want to eat an apple.",
  "scores": { "fluency": 55, "vocabulary": 60, "grammar": 50 },
  "latency_ms": { "first_sound": 280, "round_total": 1450 },
  "synced": false
}
2. "ssxb_diagnoses" — AI 診斷陣列（Hermes Agent + Bedrock Claude 的 mock 產物），元素 schema：
{
  "date": "2026-07-03",
  "scores": { "pronunciation": 62, "fluency": 58, "vocabulary": 65, "grammar": 54 },
  "strengths": ["願意開口", "詞彙記憶佳"],
  "weaknesses": ["冠詞 a/an 遺漏", "中英夾雜比例高"],
  "emotional_status": "自信心提升中，遇長句會退縮",
  "instructions": {
    "classroom": "課堂建議…",
    "device": "裝置端主題…",
    "peer": "同儕共學建議…"
  }
}
3. "ssxb_seeded" — "1" 表示已灌入示範種子資料（由教師端負責種子）。
學生端只 append "ssxb_interactions" 與（sync 時）append "ssxb_diagnoses"；教師端唯讀＋首次種子。

## 硬性限制（兩端皆適用）
- 單一自包含 HTML 檔（inline CSS/JS），**零外部資源**：不得引用任何 CDN、Google Fonts、外部圖片、fetch 外部網址——決賽要現場斷網 demo，載入任何外網資源都算失敗。字型用 system-ui / "Noto Sans TC" local fallback。
- 介面文字全部繁體中文（台灣用語）；程式識別字英文。
- RWD：手機（375px）與 PC 皆可用，viewport meta 必備。
- 純 vanilla JS，不用任何框架/函式庫。`

const OUT_SCHEMA = {
  type: 'object',
  required: ['status', 'path', 'summary'],
  properties: {
    status: { type: 'string', description: 'done 或 blocked' },
    path: { type: 'string' },
    summary: { type: 'string', description: '完成內容與已知限制，繁體中文' },
  },
}

const buildSpecs = [
  {
    key: 'student',
    path: `${DIR}/index.html`,
    prompt: `你要為黑客松決賽 MVP 建置「說說學伴 TalkyBuddy」的**學生端企鵝玩偶互動原型**（在 PC/手機瀏覽器運行，模擬未來跑在 MediaTek Genio 520 上的邊緣管線）。

${SPECS}

${CONTRACT}

## 輸出檔案
寫到 ${DIR}/index.html（用 Write 工具，目錄不存在會自動建立）。

## 必做功能（對應決賽 demo 分鏡）
1. **企鵝玩偶 UI**：手繪 inline SVG 企鵝（可愛、圓潤、針織玩偶感），腹部有 LED 呼吸燈（CSS animation），依狀態變色律動：待機=柔和青色慢呼吸、聆聽=橘色快閃、思考=紫色轉圈感、說話=綠色律動。企鵝要有簡單動畫（聆聽時歪頭/眨眼、說話時嘴巴開合）。
2. **互動狀態機**（嚴格照 SPEC v2 §5.1）：[待機] --按住肚子(push-to-talk)--> [聆聽] --放開--> [ASR] --信心足--> [LLM生成] --> [TTS播放(暫停聆聽)] --> [待機]；信心低/逾時 --> [兜底話術]（「你可以再說一次嗎？」等 3 種輪替）--> [待機]。畫面上要有目前狀態的文字指示。
3. **Push-to-talk**：按住企鵝肚子（mousedown/touchstart～mouseup/touchend）錄音。支援時用 Web Speech API（webkitSpeechRecognition，lang 依輸入語言用 "zh-TW"，continuous=false, interimResults=true 顯示即時字幕）。
4. **預設示範語句備援（demo 穩定性關鍵）**：一排「快速語句」按鈕（至少 6 句，中英夾雜，如「I want to eat 蘋果」「我想要 go to school」「This is my 書包」「我喜歡 dog」「Can I have 水?」「老師 good morning」），點了直接當 ASR 結果進管線。瀏覽器不支援 SpeechRecognition 或「飛航模式」開啟時，自動只用此路徑並顯示提示。
5. **規則式雙語鷹架引擎（核心）**：內建中→英詞庫（至少 30 個國小常用詞：食物/學校/動物/家庭/動作）＋句型模板。偵測 student_text 中的中文詞→替換成英文→產生完整正確英文句→回應格式：「你說得很棒！蘋果的英文是 apple。跟我說一遍：I want to eat an apple.」。要處理 a/an 冠詞、純中文輸入（鼓勵＋給整句英文）、純英文輸入（糾正文法小錯或稱讚＋延伸問句）。內建「7 步對話腳本」模式：一個引導式小單元（食物主題），AI 依步驟帶讀，有進度指示。
6. **本地評分啟發式**：依英文詞比例、句長、詞庫命中計算 fluency/vocabulary/grammar（0-100），寫入互動紀錄。
7. **兒少內容安全過濾**：小型禁詞表，命中→固定安撫話術（「我們來聊聊學校的事吧！」），不進 LLM 回應路徑。
8. **TTS**：speechSynthesis，中英混句拆段（中文段 zh-TW voice、英文段 en voice），語速稍慢(rate 0.9)。無 voice 時降級為文字氣泡＋提示。回應一律同時顯示文字對話氣泡（對話串 UI，學生右、企鵝左）。
9. **雙模與斷網橋段（決賽記憶點）**：頂部狀態列顯示「edge 邊緣模式 / cloud 連線中」badge＋模擬延遲數字（首音 280ms、單輪 1.4s 左右隨機抖動）。一個大的「✈️ 飛航模式」開關：開啟時 network_mode='edge'、互動照常（證明離線不中斷）、紀錄 synced=false 累積在佇列（顯示「待同步 N 筆」）；關閉（復連）時播放同步動畫，把佇列標 synced=true，並依近期互動生成一筆新的 mock 診斷 append 到 ssxb_diagnoses（內容要依實際互動的弱點動態生成，例如缺 a/an 就寫進 weaknesses），顯示「Hermes Agent 已產出最新診斷」toast。
10. **每輪互動寫入 ssxb_interactions**（照契約 schema，seq 從現有最大值+1）。
11. 頁首小導覽：產品名「說說學伴 TalkyBuddy」＋「👩‍🏫 教師儀表板」連結到 ./teacher.html＋「跑在 MediaTek Genio 520（模擬）」字樣。
12. 視覺：溫暖童趣（奶油底色系、圓角、大按鈕），與教師端的專業深色系區隔。手機直式優先。

## 驗證要求（完成前自己做）
- 把所有 <script> 內容抽出跑 node --check 確認語法。
- grep 確認檔內沒有 http:// 或 https:// 的資源引用（註解內文字除外）。
- 用 node 模擬跑一次鷹架引擎函式（抽出純函式測 3 個輸入：中英夾雜、純中文、純英文），確認輸出合理。

回傳 status/path/summary。`,
  },
  {
    key: 'teacher',
    path: `${DIR}/teacher.html`,
    prompt: `你要為黑客松決賽 MVP 建置「說說學伴 TalkyBuddy」的**教師儀表板**（在 PC/手機瀏覽器運行）。

${SPECS}

${CONTRACT}

## 輸出檔案
寫到 ${DIR}/teacher.html（用 Write 工具）。

## 必做功能（對應決賽 demo 鏡頭 4「教師閉環」）
1. **種子資料**：載入時若 localStorage 無 "ssxb_seeded"，灌入示範資料：14 天的 ssxb_diagnoses（分數有起伏但整體上升的學習曲線，strengths/weaknesses/instructions 內容要真實可信、每天不同、對齊「阿明：中英夾雜、冠詞遺漏、自信心成長」的故事線）＋約 20 筆歷史 ssxb_interactions（多數 synced=true）。日期用「今天往回推 14 天」動態計算。之後每次載入都要合併顯示 live 資料（學生端寫入的新紀錄）。
2. **學生卡頭部**：學生「阿明」（STUDENT-AMING-004）、裝置 GENIO-520-X992、班級標籤、總互動次數、待同步筆數、最近互動時間。
3. **四維評分雷達圖**（pronunciation 發音/fluency 流暢度/vocabulary 詞彙/grammar 句型）：手刻 inline SVG，取最新一筆診斷，疊加「14 天前 vs 最新」兩層（半透明面積 fill ~10% opacity＋2px 線＋頂點 8px 圓點含 2px 底色 ring），有圖例。
4. **14 天學習趨勢折線圖**：手刻 SVG，四條線（四維分數，各一色），2px 圓角線、hairline 實線格線、y 軸乾淨刻度（0/25/50/75/100）、hover/touch 顯示 crosshair＋tooltip（日期＋四維數值）、線尾直接標維度名。**不用任何圖表函式庫**。
5. **AI 診斷卡（最新）**：標示「由 Hermes Agent（持久記憶）＋ AWS Bedrock（Claude）產出」，呈現 strengths（綠 tag）、weaknesses（橘 tag）、emotional_status、三欄 instructions：課堂指引 classroom / 裝置端主題 device / 同儕共學 peer。
6. **互動紀錄表**：最近 20 筆，欄位＝時間、學生說的話、AI 回應、三維分數（小型 meter bar）、模式 badge（edge=琥珀/cloud=藍）、同步狀態（✓/待同步）。手機時卡片化。tabular-nums 對齊數字。
7. **「重新整理資料」按鈕**＋每 5 秒自動 poll localStorage（學生端在另一分頁互動時，本頁能看到新資料進來）。「重置示範資料」按鈕（confirm 後清掉三個 key 重新種子）。
8. 頁首導覽：「🐧 回學生端」連結 ./index.html；標語「邊緣互動 × 雲端診斷閉環」。

## 視覺規格（dataviz 準則，已驗證的深色系）
- 深色主題：chart surface #1a1a19、頁面底 #0d0d0d 帶一點藍紫漸層呼應品牌、primary ink #ffffff、secondary #c3c2b7、muted #898781、hairline grid #2c2c2a、axis #383835。
- 四維 series 色（深色面已驗證）：pronunciation=#3987e5(藍)、fluency=#199e70(綠)、vocabulary=#c98500(黃)、grammar=#9085e9(紫)。文字一律用 text token 色、不穿 series 色，識別靠文字旁的色點。
- 圖例必備（≥2 series）；折線 2px、marker r≥4 含 2px surface ring；面積 fill 10% opacity；grid 為 1px 實線 recessive。
- 卡片式版面、圓角、hairline 邊框 rgba(255,255,255,0.10)。

## 驗證要求（完成前自己做）
- <script> 抽出跑 node --check。
- grep 確認零外部資源引用。
- 用 node 驗證種子資料生成函式輸出符合契約 schema（欄位名逐字比對）。

回傳 status/path/summary。`,
  },
]

phase('Build')
log('並行建置學生端與教師儀表板…')

const results = await pipeline(
  buildSpecs,
  (spec) => agent(spec.prompt, { label: `build:${spec.key}`, phase: 'Build', schema: OUT_SCHEMA }),
  (built, spec) => agent(
    `你是 QA 工程師。檢查並直接修正檔案 ${spec.path}（「說說學伴」決賽 MVP 原型的${spec.key === 'student' ? '學生端' : '教師儀表板'}）。

建置代理回報：${JSON.stringify(built)}

${CONTRACT}

## 檢查清單（逐項執行，發現問題直接用 Edit 修正）
1. 檔案存在且非空；<script> 內容抽到 /tmp 跑 node --check，語法錯誤必修。
2. localStorage key 與欄位名逐字比對契約（ssxb_interactions / ssxb_diagnoses / ssxb_seeded 及所有巢狀欄位）。
3. grep -n 'https\\?://' 檢查：不得有任何外部資源載入（<link href>、<script src>、<img src>、fetch、@import、url()）；純註解或顯示文字中的網址可留。
4. viewport meta 存在；快速掃 CSS 確認 375px 寬不會橫向溢出（關鍵容器要有 max-width/flex-wrap）。
5. 事件處理健壯性：touch 與 mouse 都有綁；speechSynthesis/SpeechRecognition 不存在時有降級路徑不會 throw（用 typeof 檢查）。
6. 常見 bug 掃描：JSON.parse 沒 try-catch、seq 計算在空陣列時 NaN、日期字串格式不一致、事件重複綁定、TTS 播放中重入。
7. 修正後重跑 node --check 確認。

回傳 status（done/blocked）、path、summary（列出你修了什麼）。`,
    { label: `qa:${spec.key}`, phase: 'QA', schema: OUT_SCHEMA }
  )
)

phase('Integrate')
const ok = results.filter(Boolean)
log(`建置+QA 完成 ${ok.length}/2，進行跨檔整合驗證…`)

const integration = await agent(
  `你是整合驗證工程師。目標：確保「說說學伴」MVP 原型兩個檔案能正確互通，並產出 README。

檔案：
- ${DIR}/index.html（學生端）
- ${DIR}/teacher.html（教師儀表板）

QA 回報：${JSON.stringify(ok)}

${CONTRACT}

## 任務
1. 讀兩個檔案，逐字比對雙方讀寫 localStorage 的 key 名與所有欄位名（含巢狀 scores/instructions/latency_ms）。任何不一致：以契約為準，用 Edit 修正。
2. 確認互相的導覽連結（./teacher.html、./index.html）存在。
3. 用 node 寫一支整合測試腳本放 /tmp：模擬學生端的「寫入互動＋生成診斷」純函式與教師端的「讀取合併」邏輯（從兩檔抽出關鍵函式或依 schema 手寫 stub），驗證資料能 round-trip；跑通。
4. 兩檔各再跑一次 node --check（抽出 script）。
5. 寫 ${DIR}/README.md（繁體中文）：專案一句話、兩個頁面說明、本機執行方式（cd ${DIR} && python3 -m http.server 8000，瀏覽器開 http://localhost:8000 與 /teacher.html；手機同網段用電腦 IP）、決賽 4 分鐘 demo 對應操作步驟（對照分鏡 0-5：快速語句互動→開飛航模式再互動→復連看同步→切教師端看診斷）、已知限制（Web Speech API 需 Chrome/Edge 且線上；離線時用快速語句按鈕；本原型為 PC/手機模擬，尚未移植 Genio 520）。
6. 回報發現與修正。

回傳 status、path（README 路徑）、summary。`,
  { label: 'verify:integration', phase: 'Integrate', schema: OUT_SCHEMA }
)

return { builds: ok, integration }