# -*- coding: utf-8 -*-
"""store.py — SQLite 儲存層（說說學伴 TalkyBuddy）。

依 CONTRACTS.md 實作九個函式：
    init_db / add_interaction / list_interactions / pending_count /
    mark_all_synced / add_diagnosis / list_diagnoses / seed_demo

設計要點：
- 兩張表：
    interactions(seq INTEGER PRIMARY KEY, payload TEXT, synced INTEGER)
    diagnoses(date TEXT PRIMARY KEY, payload TEXT)
- payload 以 JSON TEXT 存放；list_* 回傳時攤回契約 dict 結構（含 synced bool）。
- 所有寫入以 threading.Lock 保護；連線 check_same_thread=False 供多執行緒共用。
- config.DB_PATH 由 config.py 提供；若 config 尚未就緒，退回以本檔位置推算的預設路徑。
- seed_demo() 僅在兩表皆空時灌入 14 天診斷 + 20 筆歷史互動（阿明故事線）。
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# 設定來源：優先使用 config.py；尚未存在時用可容錯的預設值
# ---------------------------------------------------------------------------

# 預設（fallback）常數——與 CONTRACTS.md config 段一致
_FALLBACK_BASE_DIR = Path(__file__).resolve().parent.parent
_FALLBACK_DB_PATH = _FALLBACK_BASE_DIR / "data" / "talkybuddy.db"
_FALLBACK_DEVICE_ID = "GENIO-520-X992"
_FALLBACK_STUDENT_ID = "STUDENT-AMING-004"


def _load_config():
    """嘗試載入 config 模組；失敗時回傳 None（延後到呼叫期，import 期絕不炸）。"""
    try:
        from server import config  # 套件相對層級（正式啟動路徑）
        return config
    except Exception:
        pass
    try:
        import config  # 單檔直跑時的退路
        return config
    except Exception:
        return None


def _db_path() -> Path:
    """每次呼叫都重新讀取 config.DB_PATH，讓測試可用 tempfile 動態改路徑。"""
    cfg = _load_config()
    if cfg is not None and hasattr(cfg, "DB_PATH"):
        return Path(cfg.DB_PATH)
    return Path(_FALLBACK_DB_PATH)


def _device_id() -> str:
    cfg = _load_config()
    return getattr(cfg, "DEVICE_ID", _FALLBACK_DEVICE_ID) if cfg else _FALLBACK_DEVICE_ID


def _student_id() -> str:
    cfg = _load_config()
    return getattr(cfg, "STUDENT_ID", _FALLBACK_STUDENT_ID) if cfg else _FALLBACK_STUDENT_ID


# ---------------------------------------------------------------------------
# 連線管理（單一共用連線；DB 路徑改變時自動重連）
# ---------------------------------------------------------------------------

_lock = threading.Lock()          # 保護所有寫入與連線切換
_conn: sqlite3.Connection | None = None
_conn_path: Path | None = None


def _get_conn() -> sqlite3.Connection:
    """取得共用連線；若 DB_PATH 變更（測試情境）則重新連線。"""
    global _conn, _conn_path
    path = _db_path()
    if _conn is None or _conn_path != path:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(path), check_same_thread=False)
        _conn_path = path
    return _conn


# ---------------------------------------------------------------------------
# 九個契約函式
# ---------------------------------------------------------------------------

def init_db() -> None:
    """建立資料表（若不存在）。"""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "CREATE TABLE IF NOT EXISTS interactions ("
            " seq INTEGER PRIMARY KEY,"
            " payload TEXT NOT NULL,"
            " synced INTEGER NOT NULL DEFAULT 0)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS diagnoses ("
            " date TEXT PRIMARY KEY,"
            " payload TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS student_profile ("
            " student_id TEXT PRIMARY KEY,"
            " payload TEXT NOT NULL,"
            " updated_at TEXT NOT NULL)"
        )
        conn.commit()


def add_interaction(d: dict) -> int:
    """新增一筆互動紀錄，回傳自增 seq。

    payload 內不重複儲存 seq / synced（seq 由主鍵、synced 由欄位負責），
    讀取時再攤回契約結構。
    """
    body = dict(d)
    body.pop("seq", None)
    synced = 1 if body.pop("synced", False) else 0
    # 補齊契約必要欄位的合理預設（呼叫端通常已填好）
    body.setdefault("device_id", _device_id())
    body.setdefault("student_id", _student_id())
    body.setdefault(
        "ts",
        datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds"),
    )
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO interactions (payload, synced) VALUES (?, ?)",
            (json.dumps(body, ensure_ascii=False), synced),
        )
        conn.commit()
        return int(cur.lastrowid)


def _row_to_interaction(seq: int, payload: str, synced: int) -> dict:
    """把 DB 列攤回契約 interaction dict（含 seq 與 synced bool）。"""
    d = json.loads(payload)
    d["seq"] = int(seq)
    d["synced"] = bool(synced)
    return d


def list_interactions(limit: int = 50) -> list[dict]:
    """列出最近的互動紀錄（新→舊），最多 limit 筆。"""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT seq, payload, synced FROM interactions ORDER BY seq DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [_row_to_interaction(*row) for row in rows]


def pending_count() -> int:
    """回傳尚未同步（synced=0）的互動筆數。"""
    with _lock:
        conn = _get_conn()
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM interactions WHERE synced = 0"
        ).fetchone()
    return int(n)


def mark_all_synced() -> list[dict]:
    """把所有未同步紀錄標記為已同步，並回傳「剛同步」的那些紀錄。"""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT seq, payload, synced FROM interactions WHERE synced = 0 ORDER BY seq ASC"
        ).fetchall()
        conn.execute("UPDATE interactions SET synced = 1 WHERE synced = 0")
        conn.commit()
    just_synced = []
    for seq, payload, _ in rows:
        d = _row_to_interaction(seq, payload, 1)  # 回傳時已是同步狀態
        just_synced.append(d)
    return just_synced


def add_diagnosis(d: dict) -> None:
    """新增（或覆寫同日期的）一筆診斷。"""
    date = str(d["date"])
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO diagnoses (date, payload) VALUES (?, ?)",
            (date, json.dumps(d, ensure_ascii=False)),
        )
        conn.commit()


def list_diagnoses() -> list[dict]:
    """列出所有診斷，依 date 升冪。"""
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT payload FROM diagnoses ORDER BY date ASC"
        ).fetchall()
    return [json.loads(p) for (p,) in rows]


def get_profile(student_id: str | None = None) -> dict | None:
    """取回某學生的長期 profile；尚未存過回 None。

    student_id 省略時預設用 config.STUDENT_ID（demo 單一學生）。
    """
    sid = student_id if student_id is not None else _student_id()
    with _lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT payload FROM student_profile WHERE student_id = ?",
            (sid,),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def save_profile(d: dict) -> None:
    """存入（或覆寫同一學生的）長期 profile；單列 / 學生。

    student_id 取自 payload；updated_at 取 payload 內既有值，
    無則以現在台北時區補上（仿 add_diagnosis 的 INSERT OR REPLACE 範式）。
    """
    sid = str(d.get("student_id") or _student_id())
    updated_at = d.get("updated_at") or datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ).isoformat(timespec="seconds")
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO student_profile (student_id, payload, updated_at) VALUES (?, ?, ?)",
            (sid, json.dumps(d, ensure_ascii=False), updated_at),
        )
        conn.commit()


def seed_demo() -> None:
    """首次啟動灌示範資料：14 天診斷 + 20 筆歷史互動。

    僅當 interactions 與 diagnoses 兩表皆為空時執行；
    故事線：阿明——中英夾雜、冠詞 a/an 遺漏、兩週內自信心逐步成長。
    """
    init_db()
    with _lock:
        conn = _get_conn()
        (n_inter,) = conn.execute("SELECT COUNT(*) FROM interactions").fetchone()
        (n_diag,) = conn.execute("SELECT COUNT(*) FROM diagnoses").fetchone()
    if n_inter > 0 or n_diag > 0:
        return

    for diag in _demo_diagnoses():
        add_diagnosis(diag)
    for inter in _demo_interactions():
        add_interaction(inter)


# ---------------------------------------------------------------------------
# 示範資料（阿明故事線）
# ---------------------------------------------------------------------------

def _demo_diagnoses() -> list[dict]:
    """產生 14 天診斷：日期 = 今天往回推 13 天到今天；分數整體上升、帶起伏。"""
    today = datetime.date.today()

    # 四維分數（index 0 = 最舊；整體上升、途中有回落起伏）
    pron = [48, 50, 49, 53, 55, 54, 58, 57, 60, 62, 61, 64, 66, 68]
    flu = [45, 47, 50, 48, 52, 55, 53, 57, 59, 58, 62, 64, 63, 67]
    voc = [52, 54, 53, 57, 59, 62, 60, 63, 66, 65, 68, 70, 72, 74]
    gra = [42, 44, 47, 46, 50, 49, 53, 55, 54, 58, 60, 59, 63, 65]

    # 每日敘事（strengths / weaknesses / emotional_status / instructions 三欄）
    days = [
        (
            ["願意開口嘗試", "中文表達完整"],
            ["幾乎整句中文，只夾一兩個英文單字", "冠詞 a/an 全數遺漏", "音量偏小"],
            "初次使用顯得緊張，說話音量小、常停頓等待提示。",
            {
                "classroom": "課堂先安排低壓力的單字複誦活動，避免當眾長句發表。",
                "device": "以單字級跟讀為主，句長限制在 3 個詞以內。",
                "peer": "配對安靜穩定的同儕，先做一對一單字互考。",
            },
        ),
        (
            ["能跟讀簡短英文單字", "對水果類單字反應快"],
            ["說 I have dog 漏掉冠詞 a", "遇到不會的詞直接換回中文", "語速慢且常自我中斷"],
            "仍偏被動，但被稱讚後願意再試一次，情緒平穩。",
            {
                "classroom": "點名回答時給選擇題式提問，降低開放式壓力。",
                "device": "在 have/want 句型後加入 a/an 提示音檔示範。",
                "peer": "請同儕示範 I have a dog 句型，阿明跟讀。",
            },
        ),
        (
            ["跟讀發音清晰度提升", "願意主動按下說話鍵"],
            ["冠詞 a/an 仍全漏（an apple 說成 apple）", "動詞單複數混淆", "句子多為 2-3 詞片語"],
            "開始有一點好奇心，會主動重播 AI 的英文示範。",
            {
                "classroom": "在黑板句型卡上用色筆特別標出 a/an 位置。",
                "device": "跟讀句刻意選 an apple、an egg 等母音開頭詞交錯練習。",
                "peer": "兩人一組玩「a 還是 an」快問快答小遊戲。",
            },
        ),
        (
            ["中英夾雜句變流暢：我想吃 apple 說得自然", "聽懂 AI 的中文引導"],
            ["今天狀態起伏，長句尾音含糊", "an 的使用零次成功", "疲倦時直接沉默"],
            "下午時段明顯疲倦，互動次數下降，需要短回合設計。",
            {
                "classroom": "將英語活動移到上午精神較好的時段。",
                "device": "縮短每回合長度，兩輪後給休息動畫。",
                "peer": "改為輕鬆的英文歌曲跟唱，不做競賽。",
            },
        ),
        (
            ["第一次完整說出 I want to eat an apple", "被糾正後願意重說"],
            ["a/an 仍需提示才會加上", "中文詞彙替換率高（book、dog 以外多用中文）", "疑問句語調平板"],
            "說出完整句後很開心，主動要求再玩一輪，自信心萌芽。",
            {
                "classroom": "公開稱讚他的完整句，建立成功經驗。",
                "device": "維持 an + 母音詞的高頻練習，成功即給音效獎勵。",
                "peer": "讓阿明教同儕說 an apple，用教別人鞏固記憶。",
            },
        ),
        (
            ["能自己選快速語句開啟對話", "回合間停頓縮短"],
            ["新學的 an 又忘了（回落）", "顏色類單字薄弱", "he/she 混用"],
            "遇到挫折會皺眉，但不再直接放棄，會再試一次。",
            {
                "classroom": "複習顏色單字，搭配實物教具。",
                "device": "偵測到 a/an 遺漏時改用「聽我說一次」溫和示範。",
                "peer": "玩顏色尋寶：同儕說英文顏色、阿明找物品。",
            },
        ),
        (
            ["a/an 提示後正確率過半", "開始模仿 AI 的語調"],
            ["自發句仍以中文為骨架", "過去式完全未掌握", "字尾 -s 常吞音"],
            "情緒穩定，把說說學伴當朋友，會跟企鵝說早安。",
            {
                "classroom": "課堂句型練習納入 I ate / I went 的聽辨（先聽不強求說）。",
                "device": "在回應中自然示範複數字尾 -s 的清晰發音。",
                "peer": "早自習與同儕互道 Good morning 建立日常英語習慣。",
            },
        ),
        (
            ["主動問「蘋果的英文是不是 apple」確認學過的詞", "音量明顯變大"],
            ["長句中段會卡住重來", "this/that 不分", "拼讀仍依賴整字記憶"],
            "求知慾上升，卡住時會笑著說「再一次」，挫折耐受度提高。",
            {
                "classroom": "給阿明擔任小組「單字確認員」的角色任務。",
                "device": "句長可放寬到 5-6 個詞，卡住時只重複後半句。",
                "peer": "與程度稍好的同儕輪流造句，互相接龍。",
            },
        ),
        (
            ["自發說出 I have a cat（冠詞自己加上）", "中英轉換更順"],
            ["an 在非熟悉詞（an orange 以外）仍漏", "疑問句 do/does 混淆", "書寫落後口說"],
            "第一次不靠提示用對 a，得意地重複說了三次，自信明顯成長。",
            {
                "classroom": "在聯絡簿貼紙獎勵他的 a/an 進步，讓家長知道。",
                "device": "擴充母音開頭詞庫：umbrella、elephant、ice cream。",
                "peer": "請阿明示範 I have a cat 給全組聽。",
            },
        ),
        (
            ["能連續對話 4 回合不中斷", "動物類單字幾乎全對"],
            ["今天分數小回落：感冒影響發音清晰度", "th 音以 s 替代", "介係詞 in/on 混用"],
            "身體不適仍願意完成練習，展現學習責任感。",
            {
                "classroom": "減量練習，以聽力輸入取代口說輸出。",
                "device": "降低跟讀判定嚴格度，避免生病期挫折。",
                "peer": "改為同儕唸、阿明比手勢的低負擔活動。",
            },
        ),
        (
            ["康復後回彈：主動挑戰長句", "an apple / an egg 穩定正確"],
            ["自創句文法錯誤率仍高（語序中文化）", "數字 13/30 聽辨不清", "連音聽不出來"],
            "積極性回到高點，會跟企鵝比賽「今天要說十句英文」。",
            {
                "classroom": "利用他的好勝心設計每日十句挑戰表。",
                "device": "加入 13/30、14/40 的最小對比聽辨練習。",
                "peer": "與同儕組隊累積「本週英文句數」，合作不對抗。",
            },
        ),
        (
            ["中英夾雜比例下降：英文詞替換率首次過半", "語調有起伏、像在聊天"],
            ["a/an 在陌生名詞前正確率約六成", "第三人稱單數 -s 仍漏", "長母音短母音不分"],
            "把英文當遊戲而非考試，會主動分享今天學的新詞給家人。",
            {
                "classroom": "邀請他在課堂分享「我教企鵝的新單字」。",
                "device": "回應中重複正確版本即可，減少明示糾錯保持流暢。",
                "peer": "當小老師教低年級學弟妹三個單字。",
            },
        ),
        (
            ["自我修正出現：說 a apple 後自己改成 an apple", "問句 What is this 使用正確"],
            ["自我修正尚不穩定（約一半時候察覺）", "字彙廣度仍集中在食物/動物", "拼字練習排斥"],
            "出現關鍵的自我監控行為，學習自主性顯著提升。",
            {
                "classroom": "肯定他的自我修正行為，強化「說錯再改很棒」的信念。",
                "device": "引入文具、家庭成員兩類新主題詞彙。",
                "peer": "同儕互相抓 a/an，抓到與改對都給點數。",
            },
        ),
        (
            ["四維全面進步：完整句比例、冠詞正確率皆創新高", "面對新句型敢直接開口猜"],
            ["語法仍是四維中最弱項", "書寫與拼字需要補強", "快速對話下 an 仍會退回 a"],
            "兩週前的害羞男孩已能主動開啟英語對話，自信心成長顯著。",
            {
                "classroom": "可讓阿明參加下月英語日的短句發表，準備 3 句自我介紹。",
                "device": "進入句型擴充期：加入 because、and 的複合句鷹架。",
                "peer": "與夥伴排練英語日對話短劇，一天一次。",
            },
        ),
    ]

    out: list[dict] = []
    for i, (strengths, weaknesses, emotional, instructions) in enumerate(days):
        date = today - datetime.timedelta(days=13 - i)
        out.append(
            {
                "date": date.isoformat(),
                "scores": {
                    "pronunciation": pron[i],
                    "fluency": flu[i],
                    "vocabulary": voc[i],
                    "grammar": gra[i],
                },
                "strengths": strengths,
                "weaknesses": weaknesses,
                "emotional_status": emotional,
                "instructions": instructions,
            }
        )
    return out


def _demo_interactions() -> list[dict]:
    """產生 20 筆歷史互動：中英夾雜句 + AI 鷹架回應；多數 synced=1。"""
    today = datetime.date.today()
    tz = "+08:00"

    def ts(days_ago: int, hhmm: str) -> str:
        d = today - datetime.timedelta(days=days_ago)
        return f"{d.isoformat()}T{hhmm}:00{tz}"

    # (days_ago, 時間, 學生說的話, asr信心, AI回應, fluency, vocab, grammar,
    #  asr_ms, llm_ms, tts_ms, network_mode, synced)
    rows = [
        (6, "09:12", "我想吃 apple", 0.88,
         "你說得很棒！蘋果的英文是 apple。跟我說一次：I want to eat an apple.",
         48, 52, 40, 380, 1420, 340, "edge", True),
        (6, "09:15", "I have dog", 0.82,
         "好厲害，你會用英文說整句了！記得小狗前面要加 a 喔：I have a dog.",
         50, 54, 42, 350, 1310, 320, "edge", True),
        (6, "09:19", "這是 my book", 0.79,
         "沒錯，book 就是書！我們把整句說成英文：This is my book.",
         49, 55, 44, 410, 1500, 360, "edge", True),
        (5, "10:02", "我喜歡 cat 還有 rabbit", 0.86,
         "你認識好多動物的英文！跟我說：I like cats and rabbits.",
         52, 58, 45, 330, 1280, 310, "edge", True),
        (5, "10:06", "I want eat egg", 0.74,
         "很接近囉！egg 是母音開頭，前面要用 an：I want to eat an egg.",
         51, 57, 46, 420, 1450, 350, "edge", True),
        (5, "10:11", "今天我去 school", 0.83,
         "school 說得很標準！整句英文是：I go to school today. 你也說說看！",
         54, 58, 47, 360, 1190, 300, "edge", True),
        (4, "09:31", "I see a bird 在樹上", 0.87,
         "哇，你自己加了 a，太棒了！在樹上可以說 in the tree：I see a bird in the tree.",
         55, 60, 50, 340, 1230, 290, "edge", True),
        (4, "09:36", "我的 favorite color 是 blue", 0.85,
         "說得真好！試試看整句：My favorite color is blue.",
         57, 62, 51, 300, 1100, 280, "edge", True),
        (4, "09:40", "apple 是紅色 red", 0.81,
         "對，蘋果是紅色的！我們說：The apple is red. 再跟我說一次！",
         56, 61, 52, 390, 1350, 330, "edge", True),
        (3, "14:05", "I want to eat an apple", 0.93,
         "太厲害了！你把 an 用得剛剛好，整句都對！那你想吃幾顆蘋果呢？How many apples do you want?",
         60, 63, 56, 280, 980, 260, "edge", True),
        (3, "14:09", "Two apples！我要 two", 0.9,
         "答得好！Two apples，複數的 s 也說對了。你真的進步好多！",
         61, 64, 57, 310, 1050, 270, "edge", True),
        (3, "14:14", "he 喜歡 banana", 0.78,
         "很好！他喜歡香蕉可以說：He likes bananas. likes 後面有個小小的 s 喔。",
         59, 63, 55, 400, 1400, 340, "edge", True),
        (2, "09:22", "I have a cat, 她叫 Mimi", 0.89,
         "你自己加了 a，好棒！貓咪是女生可以說 she：I have a cat. Her name is Mimi.",
         63, 66, 58, 320, 1120, 290, "cloud", True),
        (2, "09:27", "Mimi like fish 跟 milk", 0.84,
         "Mimi 喜歡魚和牛奶呀！我們說：Mimi likes fish and milk. 你說說看！",
         62, 67, 59, 350, 1210, 300, "cloud", True),
        (2, "09:33", "What is this? 這是 pencil 嗎", 0.91,
         "你的問句說得好標準！這是鉛筆，我們說：This is a pencil.",
         64, 68, 60, 290, 1000, 270, "cloud", True),
        (1, "10:41", "I eat a orange... an orange!", 0.92,
         "哇！你自己發現要改成 an orange，這是最棒的學習！I eat an orange. 完全正確！",
         66, 70, 62, 300, 950, 260, "edge", True),
        (1, "10:46", "My family 有 four people", 0.87,
         "你會介紹家人了！整句說：There are four people in my family. 跟我一起說！",
         65, 71, 61, 340, 1180, 290, "edge", True),
        (0, "09:05", "Good morning! 今天 weather 很好", 0.9,
         "早安！你一開口就是英文，太棒了！今天天氣很好：The weather is nice today.",
         68, 72, 63, 310, 1020, 270, "edge", False),
        (0, "09:10", "I want to play ball 和 my friend", 0.88,
         "好句子！和朋友一起玩可以說 with：I want to play ball with my friend.",
         67, 73, 64, 330, 1150, 280, "edge", False),
        (0, "09:16", "I have an umbrella!", 0.94,
         "太厲害了！umbrella 前面用 an，你完全說對了，給你一個大大的讚！",
         69, 74, 65, 280, 900, 250, "edge", False),
    ]

    device_id = _device_id()
    student_id = _student_id()
    out: list[dict] = []
    for (days_ago, hhmm, text, conf, reply,
         flu, voc, gra, asr_ms, llm_ms, tts_ms, mode, synced) in rows:
        round_total = asr_ms + llm_ms + tts_ms + 120  # 加上轉檔與傳輸的固定開銷
        out.append(
            {
                "device_id": device_id,
                "student_id": student_id,
                "ts": ts(days_ago, hhmm),
                "network_mode": mode,
                "student_text": text,
                "asr_confidence": conf,
                "ai_response_text": reply,
                "scores": {"fluency": flu, "vocabulary": voc, "grammar": gra},
                "latency_ms": {
                    "asr": asr_ms,
                    "llm": llm_ms,
                    "tts_first": tts_ms,
                    "round_total": round_total,
                },
                "synced": synced,
            }
        )
    return out
