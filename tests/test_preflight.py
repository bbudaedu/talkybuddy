# -*- coding: utf-8 -*-
"""開機自檢的判讀門檻必須測起來。

真機狀態沒辦法在 CI 造出來，但**判錯門檻會讓演練前的檢查失去意義**——
比「沒有檢查」更糟，因為它會給出假的信心。2026-07-30 那輪除錯的教訓就是
「合成音自檢通過、真人卻失敗」這類假通過最浪費時間。
"""

from edge.runtime import preflight
from edge.runtime.preflight import FAIL, OK, WARN


# ---------------------------------------------------------------------------
# 觸發鍵 / logind
# ---------------------------------------------------------------------------

def test_power_key_without_logind_ignore_is_a_hard_fail():
    """logind 沒放手時按下玩偶是關機，這必須是 FAIL 而不是警告。"""
    state, detail = preflight.evaluate_power_key_guard("poweroff", 116)
    assert state == FAIL
    assert "關機" in detail


def test_power_key_with_logind_ignore_passes():
    state, _ = preflight.evaluate_power_key_guard("ignore", 116)
    assert state == OK


def test_the_key_that_does_not_work_is_flagged():
    """KEY_HOME(102) 在本板實測不送任何事件，設成它要被標出來。"""
    state, detail = preflight.evaluate_power_key_guard("ignore", 102)
    assert state == WARN
    assert "102" in detail


def test_logind_setting_is_irrelevant_for_other_keys():
    """用非 power 鍵時，logind 設定不該讓檢查失敗（例如外接 USB 鍵盤）。"""
    state, _ = preflight.evaluate_power_key_guard("poweroff", 30)
    assert state == WARN  # 非 116 一律提醒，但不是 FAIL


# ---------------------------------------------------------------------------
# ALSA 裝置（必須看 service unit 的設定，不是本行程的環境變數）
# ---------------------------------------------------------------------------

def test_both_alsa_devices_set_in_the_service_passes():
    state, detail = preflight.evaluate_alsa_devices({
        "TALKYBUDDY_EDGE_ALSA_DEVICE": "plughw:1,0",
        "TALKYBUDDY_EDGE_ALSA_PLAYBACK": "plughw:0,0",
    })
    assert state == OK
    assert "plughw:1,0" in detail and "plughw:0,0" in detail


def test_missing_playback_device_is_a_hard_fail():
    """播放沒明示的症狀是「回答了但聽不到」，且 aplay 不報錯——必須擋下來。"""
    state, detail = preflight.evaluate_alsa_devices({
        "TALKYBUDDY_EDGE_ALSA_DEVICE": "plughw:1,0",
    })
    assert state == FAIL
    assert "播放" in detail


def test_missing_capture_device_is_a_hard_fail():
    state, detail = preflight.evaluate_alsa_devices({
        "TALKYBUDDY_EDGE_ALSA_PLAYBACK": "plughw:0,0",
    })
    assert state == FAIL
    assert "錄音" in detail


def test_empty_service_environment_fails_with_the_fix_command():
    """service 沒有任何設定時要直接給出修法，不要讓人自己找。"""
    state, detail = preflight.evaluate_alsa_devices({})
    assert state == FAIL
    assert "install_services.sh" in detail


def test_mic_check_records_from_the_service_device_not_the_process_env(monkeypatch):
    """收音測試必須用 **service 設定的**錄音裝置，不是本行程的環境變數。

    2026-07-30 真機誤報：preflight 手動執行時不會帶 TALKYBUDDY_EDGE_ALSA_DEVICE，
    於是用模組預設的 `default` 錄音，錄到別張音效卡、得到 peak=0.012，
    回報「靜音鍵沒按」——但靜音鍵其實是按了的。假警告比沒有檢查更糟，
    因為它會讓人開始不信任整張表。
    """
    from edge.runtime import audio_io

    seen = {}

    def _fake_capture(_seconds):
        seen["device"] = audio_io._ARECORD_DEVICE
        raise RuntimeError("停在這裡就夠了，只驗裝置")

    monkeypatch.setattr(audio_io, "_ARECORD_DEVICE", "default")
    monkeypatch.setattr(audio_io, "capture_16k_mono_wav", _fake_capture)

    preflight._check_mic("plughw:1,0")

    assert seen["device"] == "plughw:1,0", (
        f"錄音用了 {seen['device']!r} 而不是 service 設定的 plughw:1,0"
    )


def test_mic_check_restores_the_original_device_afterwards(monkeypatch):
    """覆寫是暫時的——不能污染同一行程後續的其他檢查。"""
    from edge.runtime import audio_io

    def _boom(_seconds):
        raise RuntimeError("錄音失敗")

    monkeypatch.setattr(audio_io, "_ARECORD_DEVICE", "default")
    monkeypatch.setattr(audio_io, "capture_16k_mono_wav", _boom)

    preflight._check_mic("plughw:1,0")

    assert audio_io._ARECORD_DEVICE == "default", "錄音裝置沒有還原"


def test_mic_capture_failure_is_reported_as_fail(monkeypatch):
    from edge.runtime import audio_io

    def _boom(_seconds):
        raise OSError("device busy")

    monkeypatch.setattr(audio_io, "capture_16k_mono_wav", _boom)
    state, detail = preflight._check_mic("plughw:1,0")
    assert state == FAIL
    assert "錄音失敗" in detail


# ---------------------------------------------------------------------------
# 麥克風
# ---------------------------------------------------------------------------

def test_silent_mic_is_a_hard_fail_and_names_the_mute_button():
    """peak 過低幾乎都是實體靜音鍵沒按——訊息必須直接講出來，不要讓人猜。"""
    state, detail = preflight.evaluate_mic(peak=0.001, voice_band_ratio=0.5)
    assert state == FAIL
    assert "靜音鍵" in detail


def test_loud_but_low_frequency_noise_does_not_pass_as_voice():
    """真機踩過的坑：peak=0.109 但 98% 能量在 500Hz 以下，是噪音不是人聲。

    只看音量會給出假通過，這正是要防的。
    """
    state, detail = preflight.evaluate_mic(peak=0.109, voice_band_ratio=0.02)
    assert state == WARN
    assert "噪音" in detail


def test_real_voice_passes():
    state, _ = preflight.evaluate_mic(peak=0.3, voice_band_ratio=0.6)
    assert state == OK


# --- 削波：訊號太大跟太小一樣會壞事 -----------------------------------------

def test_clipping_is_flagged_even_though_the_signal_is_plenty_loud():
    """真機現況：peak=1.000（滿刻度）而自檢回 OK。

    削波把語音波形的頂端切平，直接拉低 ASR 準確度——2026-07-30 S2S 實測的
    那些誤判（電視聲被判成插話、噪音被判成韓文字符）很可能與此有關。

    原本的 evaluate_mic 只檢查訊號**夠不夠大**，沒檢查**是不是太大**。
    這是同一天第三個「自檢說綠燈但實際壞掉」的案例。
    """
    state, detail = preflight.evaluate_mic(
        peak=1.0, voice_band_ratio=0.26, clip_ratio=0.05)
    assert state == WARN
    assert "削波" in detail


def test_the_clipping_warning_carries_a_copy_pastable_fix():
    """USB 麥克風的 capture gain 是真的可調的（與 3.5mm 輸出不同）。

    真機現況是開在滿檔 147/147。訊息要能直接複製執行，不要只說「太大聲」。
    """
    _, detail = preflight.evaluate_mic(
        peak=1.0, voice_band_ratio=0.4, clip_ratio=0.05)
    assert "amixer" in detail
    assert "alsactl store" in detail, "不持久化的話重開機就打回原形"


def test_an_occasional_sample_hitting_the_ceiling_is_not_clipping():
    """peak=1.0 本身不算削波——偶爾一個樣本打到頂是正常的，持續打頂才是。

    這條是刻意防止製造出新的假警告：判定依據是「接近滿刻度的樣本占比」，
    不是 peak 這個單一極值。理由與模組既有的那條教訓同源（只看 peak 曾
    誤判過低頻噪音為人聲）。
    """
    state, _ = preflight.evaluate_mic(
        peak=1.0, voice_band_ratio=0.6, clip_ratio=0.0001)
    assert state == OK


def test_clipping_outranks_the_voice_band_warning():
    """兩個問題同時成立時要先講削波，因為它是另一個的成因。

    削波會把能量灌進高頻諧波，人聲頻段占比因此被稀釋。先叫人去調頻段
    只會白忙——調完增益，頻段占比很可能自己就回來了。
    """
    _, detail = preflight.evaluate_mic(
        peak=1.0, voice_band_ratio=0.02, clip_ratio=0.05)
    assert "削波" in detail


def test_a_silent_mic_still_wins_over_everything():
    """靜音鍵沒按是 FAIL、現在不能 demo；削波只是 WARN、還能 demo。

    優先序不能顛倒，否則會把「完全錄不到」講成「音量稍大」。
    """
    state, detail = preflight.evaluate_mic(
        peak=0.001, voice_band_ratio=0.5, clip_ratio=0.0)
    assert state == FAIL
    assert "靜音鍵" in detail


def test_clip_ratio_is_measured_not_guessed():
    """_voice_band_ratio 要一併算出削波占比，不能讓呼叫端自己從 peak 猜。

    削波占比＝接近滿刻度（|sample| >= 32000）的樣本比例。用合成訊號驗：
    一半樣本壓在滿刻度、一半在中間值。
    """
    samples = [32767] * 500 + [8000] * 500
    peak, _ratio, clip = preflight._voice_band_ratio(samples, 16000)
    assert peak == 32767 / 32768.0
    assert abs(clip - 0.5) < 0.01


def test_clean_audio_reports_no_clipping():
    peak, _ratio, clip = preflight._voice_band_ratio([8000, -8000] * 500, 16000)
    assert clip == 0.0
    assert abs(peak - 8000 / 32768.0) < 1e-6


def test_empty_capture_does_not_crash_the_clip_measurement():
    peak, ratio, clip = preflight._voice_band_ratio([], 16000)
    assert (peak, ratio, clip) == (0.0, -1.0, 0.0)


def test_mic_thresholds_match_the_rehearsal_checklist():
    """門檻須與 NETCUT_REHEARSAL_CHECKLIST 步驟 0 一致，否則兩份文件會互相矛盾。"""
    assert preflight.MIC_PEAK_MIN == 0.05
    assert preflight.MIC_VOICE_BAND_MIN == 0.25


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------

def test_missing_core_capability_is_a_hard_fail():
    state, detail = preflight.evaluate_status(
        {"asr": True, "llm": False, "tts": True})
    assert state == FAIL
    assert "llm" in detail


def test_cloud_tts_missing_is_only_a_warning():
    """cloud_tts 缺金鑰不影響純離線 demo，不該擋演練。"""
    state, detail = preflight.evaluate_status(
        {"asr": True, "llm": True, "tts": True, "cloud_tts": False,
         "cloud_tts_detail": "未啟用：缺 ELEVENLABS_API_KEY 或 ELEVENLABS_VOICE_ID",
         "network_mode": "cloud"})
    assert state == WARN
    assert "ELEVENLABS" in detail


def test_cloud_tts_warning_says_which_kind_of_broken_it_is():
    """「沒設金鑰」與「設了但每次逾時降級」的處置完全不同，不能顯示成同一句。

    自檢原本一律印「缺 ELEVENLABS_API_KEY」——2026-07-30 金鑰其實已經設好、
    真正的原因是 CLOUD_TTS_TIMEOUT_S 擋掉了每一次合成。照著那句話去補金鑰
    只會白忙一輪。
    """
    state, detail = preflight.evaluate_status(
        {"asr": True, "llm": True, "tts": True, "cloud_tts": False,
         "cloud_tts_detail": "設定齊全但上次合成失敗 → 已靜默降級回邊緣語音"
                             "（逾時 > 1.5s 上限）",
         "network_mode": "cloud"})
    assert state == WARN
    assert "逾時" in detail
    assert "ELEVENLABS_API_KEY" not in detail, "別再叫人去補一把已經設好的金鑰"


def test_cloud_tts_warning_survives_an_old_server_without_the_detail_field():
    """裝置端的 server 可能還沒更新到有 cloud_tts_detail 的版本。

    自檢不該因為少一個欄位就爆掉——它是拿來救火的工具，本身不能是新的故障點。
    """
    state, detail = preflight.evaluate_status(
        {"asr": True, "llm": True, "tts": True,
         "cloud_tts": False, "network_mode": "cloud"})
    assert state == WARN
    assert "cloud_tts" in detail


def test_all_ready_in_cloud_mode_passes_clean():
    state, _ = preflight.evaluate_status(
        {"asr": True, "llm": True, "tts": True,
         "cloud_tts": True, "network_mode": "cloud"})
    assert state == OK


def test_edge_mode_is_flagged_as_wrong_starting_point_for_the_netcut_drill():
    """斷網演練要從 cloud 起步，否則沒有東西可降級。"""
    state, detail = preflight.evaluate_status(
        {"asr": True, "llm": True, "tts": True,
         "cloud_tts": True, "network_mode": "edge"})
    assert state == WARN
    assert "cloud" in detail


# ---------------------------------------------------------------------------
# 記憶體 / 報表
# ---------------------------------------------------------------------------

def test_low_memory_is_a_hard_fail():
    assert preflight.evaluate_memory(200)[0] == FAIL


def test_tight_memory_warns():
    assert preflight.evaluate_memory(600)[0] == WARN


def test_healthy_memory_passes():
    # 真機實測值約 1777MB
    assert preflight.evaluate_memory(1777)[0] == OK


def test_any_fail_means_cannot_demo():
    rows = [("a", OK, ""), ("b", FAIL, ""), ("c", WARN, "")]
    state, summary = preflight.overall_verdict(rows)
    assert state == FAIL
    assert "b" in summary


def test_warnings_alone_still_allow_demo():
    state, summary = preflight.overall_verdict([("a", OK, ""), ("b", WARN, "")])
    assert state == WARN
    assert "可以 demo" in summary


def test_all_ok_gives_a_clean_verdict():
    state, _ = preflight.overall_verdict([("a", OK, ""), ("b", OK, "")])
    assert state == OK


def test_report_lines_up_and_includes_every_row():
    out = preflight.format_report([
        ("短", OK, "一"),
        ("很長的項目名稱", FAIL, "二"),
    ])
    assert "短" in out and "很長的項目名稱" in out
    assert "一" in out and "二" in out
    assert len(out.splitlines()) == 2


def test_empty_report_does_not_crash():
    assert preflight.format_report([])
    assert preflight.overall_verdict([])[0] == OK
