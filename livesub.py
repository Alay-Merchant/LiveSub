"""LiveSub: system audio -> translucent English subtitles on screen."""
import os
import pathlib
import queue
import threading
import time
import tkinter as tk

import truststore
truststore.inject_into_ssl()  # ponytail: Norton intercepts TLS on this machine

import nvidia.cublas, nvidia.cudnn
for pkg in (nvidia.cublas, nvidia.cudnn):
    d = str(pathlib.Path(pkg.__path__[0]) / "bin")
    os.add_dll_directory(d)
    os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]

import numpy as np
import soundcard as sc
from faster_whisper import WhisperModel

import tomllib

CFG_PATH = pathlib.Path(__file__).with_name("config.toml")
CFG = tomllib.loads(CFG_PATH.read_text(encoding="utf-8")) if CFG_PATH.exists() else {}

SR = 16000
WINDOW_S = 5.0   # longer context = far better fast-dialogue accuracy
HOP_S = 1.0      # refresh every second
MODEL = CFG.get("model", "medium")

# Whisper hallucinates these on silence/music — never show them
HALLUCINATIONS = (
    "thank you for watching", "thanks for watching", "please subscribe",
    "see you next time", "subtitles by", "i'll see you in the next video",
    "bye-bye", "thank you.",
)
SOURCE = CFG.get("source", "auto")
TARGET = CFG.get("target", "en")
OLLAMA_MODEL = CFG.get("ollama_model", "llama3.2")
FADE_AFTER_S = 4.0     # hide subtitle after this much silence
TRANSPARENT = "#010101"  # colorkey — anything this color becomes see-through
PAUSED = threading.Event()
VOICE_FOCUS = CFG.get("voice_focus", True)
VOCAL_ISOLATION = CFG.get("vocal_isolation", False)
_SEP = None


def isolate_vocals(audio: np.ndarray) -> np.ndarray:
    """Demucs vocal stem: real separation of dialogue from music. GPU-hungry."""
    global _SEP
    import torch, torchaudio
    from demucs.api import Separator
    if _SEP is None:
        _SEP = Separator(model="htdemucs", device="cuda" if torch.cuda.is_available() else "cpu")
    wav = torch.from_numpy(audio).unsqueeze(0)
    wav44 = torchaudio.functional.resample(wav, SR, 44100).repeat(2, 1)
    _, stems = _SEP.separate_tensor(wav44, 44100)
    v = stems["vocals"].mean(0, keepdim=True)
    return torchaudio.functional.resample(v, 44100, SR).squeeze(0).cpu().numpy().astype(np.float32)


def bandpass(audio: np.ndarray) -> np.ndarray:
    # ponytail: FFT brick-wall 150-4000 Hz; cuts bass/music/effects, keeps speech.
    # A real source-separation model (demucs) is the upgrade if this isn't enough.
    spec = np.fft.rfft(audio)
    freqs = np.fft.rfftfreq(len(audio), 1 / SR)
    spec[(freqs < 150) | (freqs > 4000)] = 0
    return np.fft.irfft(spec, len(audio)).astype(np.float32)


def ollama_translate(text: str) -> str:
    # ponytail: blocking urllib call, fine at subtitle cadence
    import json, urllib.request
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=json.dumps({
            "model": OLLAMA_MODEL, "stream": False,
            "prompt": f"Translate to {TARGET}. Output ONLY the translation, nothing else.\n\n{text}",
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["response"].strip()


LOG = pathlib.Path(__file__).with_name("livesub.log")


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def transcriber(out_q: queue.Queue):
    try:
        model = WhisperModel(MODEL, device="cuda", compute_type="int8_float16")
        log(f"model {MODEL} on GPU")
    except Exception as e:
        log(f"GPU failed: {e}; using CPU")
        out_q.put("⚠ CPU mode — GPU unavailable, subtitles will lag")
        model = WhisperModel(MODEL, device="cpu", compute_type="int8")
    last_out = ""
    pending = ""  # interim text held until a silence boundary confirms the sentence
    while True:  # outer loop: reopens capture when the default output device changes
        spk = sc.default_speaker()
        mic = sc.get_microphone(spk.name, include_loopback=True)
        log(f"capturing loopback of: {spk.name}")
        out_q.put(f"● listening on {spk.name}")
        buf = np.zeros(0, dtype=np.float32)
        # capture on its own thread so transcription never causes dropped audio
        audio_q = queue.Queue()
        stop_capture = threading.Event()

        def capture():
            with mic.recorder(samplerate=SR, channels=1) as rec:
                while not stop_capture.is_set():
                    audio_q.put(rec.record(numframes=int(SR * 0.25)).flatten())

        cap_t = threading.Thread(target=capture, daemon=True)
        cap_t.start()
        try:
            while sc.default_speaker().name == spk.name:
                parts = [audio_q.get()]
                while len(parts) * 0.25 < HOP_S:
                    parts.append(audio_q.get())
                chunk = np.concatenate(parts)
                if PAUSED.is_set():
                    buf = np.zeros(0, dtype=np.float32)
                    pending = ""
                    continue
                if np.abs(chunk).max() < 0.005:  # silence boundary: commit pending line
                    if pending and pending != last_out:
                        last_out = pending
                        log(f"[commit] {pending}")
                        out_q.put(pending)
                    pending = ""
                    buf = np.zeros(0, dtype=np.float32)
                    continue
                buf = np.concatenate([buf, chunk])[-int(SR * WINDOW_S):]
                audio = buf
                if VOCAL_ISOLATION:
                    try:
                        audio = isolate_vocals(audio)
                    except Exception as e:
                        log(f"vocal isolation failed: {e!r}")
                elif VOICE_FOCUS:
                    audio = bandpass(audio)
                segments, _ = model.transcribe(
                    audio,
                    task="translate" if TARGET == "en" else "transcribe",
                    language=None if SOURCE == "auto" else SOURCE,
                    beam_size=2, vad_filter=True, condition_on_previous_text=False,
                    vad_parameters={"min_silence_duration_ms": 250},
                    no_speech_threshold=0.5, log_prob_threshold=-0.8,
                )
                text = " ".join(s.text.strip() for s in segments).strip()
                low = text.lower().strip(" .!")
                if any(h.strip(" .") in low for h in HALLUCINATIONS) and len(low) < 40:
                    continue
                # ponytail: substring check kills overlap-window repeats; fuzzy match if it misses
                if not text or text == last_out or (len(text) > 12 and text in last_out):
                    continue
                if TARGET != "en":
                    try:
                        text = ollama_translate(text)
                    except Exception as e:
                        log(f"ollama failed: {e!r}")
                        text = f"[ollama offline] {text}"
                # show interim only when it extends what's on screen; rewrites wait
                # for the silence commit — no more flickering text
                if (not pending or pending in text) and text != last_out:
                    log(text)
                    out_q.put(text)
                    last_out = text
                pending = text
        finally:
            stop_capture.set()
            cap_t.join(timeout=2)


def make_clickthrough(root):
    import ctypes
    GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ctypes.windll.user32.SetWindowLongW(
        hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT)


def main():
    LOG.write_text("", encoding="utf-8")
    LOG.with_name("livesub.pid").write_text(str(os.getpid()), encoding="utf-8")
    q = queue.Queue()

    def guarded():
        try:
            transcriber(q)
        except Exception as e:
            log(f"transcriber died: {e!r}")
            q.put(f"error: {e}")

    threading.Thread(target=guarded, daemon=True).start()

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", TRANSPARENT)
    root.attributes("-alpha", CFG.get("opacity", 0.75))
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    w, h = int(sw * 0.8), 110
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{sh - h - CFG.get('bottom_margin', 60)}")
    root.configure(bg=TRANSPARENT)

    canvas = tk.Canvas(root, bg=TRANSPARENT, highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    import tkinter.font as tkfont
    family = CFG.get("font", "Cascadia Code")
    if family not in tkfont.families():
        family = "Segoe UI"
    font = (family, CFG.get("font_size", 14), "bold")
    text_color = CFG.get("text_color", "#3b3b3b")
    outline = CFG.get("outline_color", "#e8e8e8")

    def draw(text):
        canvas.delete("all")
        if not text:
            return
        cx, cy = w // 2, h // 2
        if outline:
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                canvas.create_text(cx + dx, cy + dy, text=text, font=font,
                                   fill=outline, width=w - 40, justify="center")
        canvas.create_text(cx, cy, text=text, font=font,
                           fill=text_color, width=w - 40, justify="center")

    state = {"last": 0.0, "shown": False, "combo_down": False}

    def check_hotkey():
        # Ctrl+Alt+L toggles pause; polled, no global-hotkey lib needed
        import ctypes
        down = all(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000
                   for vk in (0x11, 0x12, 0x4C))
        if down and not state["combo_down"]:
            if PAUSED.is_set():
                PAUSED.clear()
                draw("● resumed")
            else:
                PAUSED.set()
                draw("⏸ paused — Ctrl+Alt+L to resume")
            state["last"] = time.time()
            state["shown"] = True
        state["combo_down"] = down

    def tick():
        check_hotkey()
        try:
            text = None
            while True:  # drain to latest
                text = q.get_nowait()
        except queue.Empty:
            pass
        if text:
            draw(text)
            state["last"] = time.time()
            state["shown"] = True
        elif state["shown"] and time.time() - state["last"] > FADE_AFTER_S:
            draw("")
            state["shown"] = False
        root.after(200, tick)

    draw("LiveSub loading model…")
    state["last"] = time.time()
    state["shown"] = True
    root.after(200, make_clickthrough, root)
    root.after(200, tick)
    root.mainloop()


if __name__ == "__main__":
    main()
