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
WINDOW_S = 5.0   # interim refreshes look at the last 5s
UTTER_MAX_S = 15.0  # full utterance kept for the final commit — no lost sentence starts
HOP_S = 1.0      # refresh every second
SILENCE = 0.003  # quiet-speech-safe silence threshold
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
    # no-proxy opener: security suites proxy localhost and swallow the request
    # 60s covers Ollama's cold model load; steady-state calls take ~1-2s
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=60) as r:
        return json.load(r)["response"].strip()


LOG = pathlib.Path(__file__).with_name("livesub.log")

# glossary.txt: names/terms (one per line or comma-separated) that Whisper
# should recognise — e.g. character names for the show you're watching
GLOSSARY_FILE = pathlib.Path(__file__).with_name("glossary.txt")
GLOSSARY = ""
if GLOSSARY_FILE.exists():
    terms = [t.strip() for t in GLOSSARY_FILE.read_text(encoding="utf-8").replace("\n", ",").split(",") if t.strip()]
    GLOSSARY = ", ".join(terms)


def model_cached(name: str) -> bool:
    hub = pathlib.Path.home() / ".cache" / "huggingface" / "hub"
    return any(hub.glob(f"models--*faster-whisper-{name}*"))


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")


def transcriber(out_q: queue.Queue):
    if not model_cached(MODEL):
        out_q.put(f"⬇ downloading '{MODEL}' model — first run only, takes a few minutes…")
    try:
        model = WhisperModel(MODEL, device="cuda", compute_type="int8_float16")
        log(f"model {MODEL} on GPU")
    except Exception as e:
        log(f"GPU failed: {e}; using CPU")
        # ponytail: no GPU -> medium is too slow on CPU, auto-drop to small
        cpu_model = "small" if MODEL == "medium" else MODEL
        out_q.put(f"⚠ CPU mode — using '{cpu_model}' model")
        model = WhisperModel(cpu_model, device="cpu", compute_type="int8")
    last_out = ""
    pending = ""  # interim text held until a silence boundary confirms the sentence
    while True:  # outer loop: reopens capture when the default output device changes
        try:
            spk = sc.default_speaker()
            mic = sc.get_microphone(spk.name, include_loopback=True)
        except Exception as e:  # no output device right now (bluetooth reconnecting…)
            log(f"no audio device: {e!r}")
            out_q.put("⚠ no audio output device — waiting…")
            time.sleep(3)
            continue
        log(f"capturing loopback of: {spk.name}")
        out_q.put(f"● listening on {spk.name}")
        buf = np.zeros(0, dtype=np.float32)
        # capture on its own thread so transcription never causes dropped audio
        audio_q = queue.Queue()
        stop_capture = threading.Event()

        def capture():
            try:
                with mic.recorder(samplerate=SR, channels=1) as rec:
                    while not stop_capture.is_set():
                        audio_q.put(rec.record(numframes=int(SR * 0.25)).flatten())
            except Exception as e:  # device unplugged / bluetooth dropped
                log(f"capture ended: {e!r}")

        def default_name():
            try:
                return sc.default_speaker().name
            except Exception:
                return ""  # device vanished; exit inner loop and reopen

        cap_t = threading.Thread(target=capture, daemon=True)
        cap_t.start()
        try:
            while default_name() == spk.name:
                try:
                    # timeout so a dead device (AirPods off, headphones
                    # unplugged) never blocks forever — reopen on new default
                    parts = [audio_q.get(timeout=3)]
                    while len(parts) * 0.25 < HOP_S:
                        parts.append(audio_q.get(timeout=3))
                except queue.Empty:
                    break  # capture stalled; outer loop reopens the device
                chunk = np.concatenate(parts)
                if PAUSED.is_set():
                    buf = np.zeros(0, dtype=np.float32)
                    pending = ""
                    continue

                def transcribe(audio):
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
                        initial_prompt=GLOSSARY or None,
                        beam_size=2, vad_filter=True, condition_on_previous_text=False,
                        vad_parameters={"min_silence_duration_ms": 250},
                        no_speech_threshold=0.5, log_prob_threshold=-0.8,
                    )
                    text = " ".join(s.text.strip() for s in segments).strip()
                    low = text.lower().strip(" .!")
                    if any(h.strip(" .") in low for h in HALLUCINATIONS) and len(low) < 40:
                        return ""
                    for h in HALLUCINATIONS:  # also strip them off the end of real lines
                        h = h.strip(" .")
                        if low.endswith(h):
                            text = text[:len(text) - len(low) + low.rfind(h)].rstrip(" .,;和")
                            low = text.lower().strip(" .!")
                    return text

                if np.abs(chunk).max() < SILENCE:  # silence boundary: commit the utterance
                    if len(buf) > SR * 0.5:
                        # re-transcribe the WHOLE utterance so no words are lost,
                        # even for sentences longer than the interim window
                        final = transcribe(buf)
                        if TARGET != "en" and final:
                            try:
                                final = ollama_translate(final)
                            except Exception as e:
                                log(f"ollama failed: {e!r}")
                                final = f"[ollama offline] {final}"
                        if final and final != last_out:
                            last_out = final
                            log(f"[commit] {final}")
                            out_q.put(final)
                    pending = ""
                    buf = np.zeros(0, dtype=np.float32)
                    continue
                buf = np.concatenate([buf, chunk])[-int(SR * UTTER_MAX_S):]
                if audio_q.qsize() > 8:  # backlogged: skip interims, catch up; commit still covers everything
                    continue
                text = transcribe(buf[-int(SR * WINDOW_S):])  # interim: recent window only
                if not text or text == last_out or (len(text) > 12 and text in last_out):
                    continue
                if TARGET != "en":
                    pending = text  # non-EN targets show finals only (translation cadence)
                    continue
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
    """Reapplied periodically: Tk can recreate the native window and silently
    drop the style — without it the overlay swallows clicks meant for the
    video player underneath (pause/captions buttons live right there)."""
    import ctypes
    u = ctypes.windll.user32
    GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
    GA_ROOT = 2
    hwnd = u.GetAncestor(root.winfo_id(), GA_ROOT) or root.winfo_id()
    style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    want = style | WS_EX_LAYERED | WS_EX_TRANSPARENT
    if style != want:
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, want)
        log(f"clickthrough (re)applied to hwnd {hwnd}")
    root.after(2000, make_clickthrough, root)


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
    w, h = int(sw * 0.8), 260  # tall enough for the history panel; empty area is click-through
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{sh - h - CFG.get('bottom_margin', 60)}")
    root.configure(bg=TRANSPARENT)

    canvas = tk.Canvas(root, bg=TRANSPARENT, highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    import tkinter.font as tkfont
    family = CFG.get("font", "Cascadia Code")
    if family not in tkfont.families():
        family = "Segoe UI"
    font = (family, CFG.get("font_size", 14), "bold")
    text_color = CFG.get("text_color", "#ffffff")
    outline = CFG.get("outline_color", "#1a1a1a")
    dim_color = "#9a9a9a"
    small_font = (family, max(CFG.get("font_size", 14) - 3, 8))

    import collections
    history = collections.deque(maxlen=10)
    state = {"last": 0.0, "shown": False, "combo_down": False,
             "hist_combo": False, "hist_on": False,
             "current": "", "previous": ""}

    def outlined(cx, cy, text, f, fill):
        if outline:
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                canvas.create_text(cx + dx, cy + dy, text=text, font=f,
                                   fill=outline, width=w - 40, justify="center")
        canvas.create_text(cx, cy, text=text, font=f,
                           fill=fill, width=w - 40, justify="center")

    def render():
        canvas.delete("all")
        cx = w // 2
        if state["hist_on"]:  # history panel: recent lines stacked, newest at bottom
            lines = list(history) + ([state["current"]] if state["current"] else [])
            lines = lines[-8:]
            for i, line in enumerate(lines):
                is_last = i == len(lines) - 1
                outlined(cx, h - 24 - 26 * (len(lines) - 1 - i), line,
                         font if is_last else small_font,
                         text_color if is_last else dim_color)
            return
        if state["previous"]:
            outlined(cx, h - 62, state["previous"], small_font, dim_color)
        if state["current"]:
            outlined(cx, h - 30, state["current"], font, text_color)

    import difflib
    import re as _re

    def same_sentence(a, b):
        """True when b is a refinement/extension of a (case, punctuation,
        partial overlap) — replace in place instead of repeating."""
        na = _re.sub(r"[^\w\s]", "", a).casefold().split()
        nb = _re.sub(r"[^\w\s]", "", b).casefold().split()
        if not na or not nb:
            return True
        sa, sb = " ".join(na), " ".join(nb)
        return (sa in sb or sb in sa
                or difflib.SequenceMatcher(None, sa, sb).ratio() > 0.6)

    def draw(text):
        if not text:
            state["current"] = state["previous"] = ""
        elif text.startswith(("●", "⚠", "⬇", "⏸", "error")):
            state["current"] = text
        else:
            # only a genuinely NEW sentence pushes the old one up; a refined
            # version of the same sentence replaces it in place — no repeats
            if state["current"] and not same_sentence(state["current"], text):
                state["previous"] = state["current"]
                history.append(state["current"])
            state["current"] = text
        render()

    def combo(vk, flag):
        import ctypes
        down = all(ctypes.windll.user32.GetAsyncKeyState(k) & 0x8000
                   for k in (0x11, 0x12, vk))
        fired = down and not state[flag]
        state[flag] = down
        return fired

    def check_hotkey():
        # polled, no global-hotkey lib needed
        if combo(0x4C, "combo_down"):  # Ctrl+Alt+L: pause
            if PAUSED.is_set():
                PAUSED.clear()
                draw("● resumed")
            else:
                PAUSED.set()
                draw("⏸ paused — Ctrl+Alt+L to resume")
            state["last"] = time.time()
            state["shown"] = True
        if combo(0x48, "hist_combo"):  # Ctrl+Alt+H: history panel
            state["hist_on"] = not state["hist_on"]
            state["last"] = time.time()
            state["shown"] = True
            render()

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

    def start_tray():
        try:
            import pystray
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (64, 64), "#1a1a1a")
            d = ImageDraw.Draw(img)
            d.rectangle([6, 20, 58, 46], outline="#ffffff", width=3)
            d.text((14, 24), "CC", fill="#ffffff")

            def toggle_pause(icon, item):
                PAUSED.clear() if PAUSED.is_set() else PAUSED.set()

            def open_settings(icon, item):
                import subprocess
                subprocess.Popen([str(pathlib.Path(__file__).with_name(".venv") / "Scripts" / "pythonw.exe"),
                                  str(pathlib.Path(__file__).with_name("settings.py"))])

            def quit_app(icon, item):
                icon.stop()
                root.after(0, root.destroy)

            pystray.Icon("LiveSub", img, "LiveSub", pystray.Menu(
                pystray.MenuItem(lambda i: "Resume" if PAUSED.is_set() else "Pause", toggle_pause),
                pystray.MenuItem("Settings", open_settings),
                pystray.MenuItem("Quit", quit_app),
            )).run()
        except Exception as e:
            log(f"tray failed: {e!r}")  # overlay still works without it

    threading.Thread(target=start_tray, daemon=True).start()

    draw("LiveSub loading model…")
    state["last"] = time.time()
    state["shown"] = True
    root.after(200, make_clickthrough, root)
    root.after(200, tick)
    root.mainloop()


if __name__ == "__main__":
    main()
