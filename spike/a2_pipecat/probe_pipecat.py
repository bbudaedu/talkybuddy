"""探查已裝 Pipecat 版本的實際 API，供 spike 其餘部分對接。不預設答案。"""
import importlib
import inspect


def show(label, obj):
    print(f"\n=== {label} ===")
    print(obj)


def try_import(path):
    try:
        return importlib.import_module(path)
    except Exception as e:  # noqa: BLE001
        print(f"[import FAIL] {path}: {e}")
        return None


def main():
    import pipecat
    show("pipecat version", getattr(pipecat, "__version__", "?"))

    # (a) FunASRSTTService 匯入路徑
    for path in ("pipecat.services.funasr.stt", "pipecat.services.funasr"):
        mod = try_import(path)
        if mod and hasattr(mod, "FunASRSTTService"):
            cls = mod.FunASRSTTService
            show(f"FunASRSTTService @ {path}", cls)
            show("  __init__ signature", inspect.signature(cls.__init__))
            show("  MRO", [c.__name__ for c in cls.__mro__])
            break

    # (b) 可中止 TTS 基類候選
    for path, name in (
        ("pipecat.services.tts_service", "InterruptibleTTSService"),
        ("pipecat.services.tts_service", "TTSService"),
        ("pipecat.services.ai_services", "TTSService"),
        ("pipecat.services.ai_services", "InterruptibleTTSService"),
    ):
        mod = try_import(path)
        if mod and hasattr(mod, name):
            cls = getattr(mod, name)
            show(f"TTS base {name} @ {path}", cls)
            show("  MRO", [c.__name__ for c in cls.__mro__])
            methods = [m for m in dir(cls) if "tts" in m.lower() or m in ("run_tts", "flush", "process_frame")]
            show("  tts-ish methods", methods)
            if hasattr(cls, "run_tts"):
                try:
                    show("  run_tts signature", inspect.signature(cls.run_tts))
                except (TypeError, ValueError):
                    pass

    # (c) interruption frame 候選
    frames = try_import("pipecat.frames.frames")
    if frames:
        cands = [n for n in dir(frames) if "Interrupt" in n or "StartedSpeaking" in n or "StoppedSpeaking" in n]
        show("interruption-related frames", cands)
        audio_cands = [n for n in dir(frames) if "Audio" in n or "TTS" in n or n in ("TextFrame", "TranscriptionFrame", "EndFrame")]
        show("audio/text/tts frames", audio_cands)

    # (d) FrameProcessor 基類與 FrameDirection
    fp = try_import("pipecat.processors.frame_processor")
    if fp:
        show("frame_processor exports", [n for n in dir(fp) if "Frame" in n or "Direction" in n])

    # (e) pipeline 執行 API
    for path, names in (
        ("pipecat.pipeline.pipeline", ["Pipeline"]),
        ("pipecat.pipeline.task", ["PipelineTask"]),
        ("pipecat.pipeline.runner", ["PipelineRunner"]),
    ):
        mod = try_import(path)
        if mod:
            for n in names:
                if hasattr(mod, n):
                    show(f"{n} @ {path}", getattr(mod, n))

    print("\n>>> 把上面結果謄進 SPIKE-RESULT.md 的『未知 #2/#3 實測』。")


if __name__ == "__main__":
    main()
