# LiveSub

**Live translated subtitles for anything playing on your PC.**

![LiveSub demo](assets/demo.gif)

Watching anime with no subs? A French lecture? A Korean stream? LiveSub listens to
whatever your computer is playing, transcribes it with Whisper running locally on
your machine, and overlays translucent, click-through subtitles in English (or any
language, via Ollama). No cloud, no accounts, no uploads — your audio never leaves
your PC.

- 🌍 **~99 languages in, auto-detected** — Japanese, French, Korean, Chinese, German, Spanish, Russian, and Indian languages including Hindi, Gujarati, Tamil, Telugu, Bengali, Marathi, Punjabi, Urdu…
- 🖥️ **Works with everything** — video players, browsers, streams, games, calls
- 👻 **Non-distracting** — translucent overlay, click-through (your mouse ignores it), fades when nobody's talking
- 🎵 **Voice focus** — filters out music and effects so dialogue wins
- 🔒 **100% local** — Whisper runs on your GPU (or CPU)

## Install (Windows)

1. Install [Python 3.11+](https://www.python.org/downloads/) (tick "Add to PATH")
2. [Download LiveSub](https://github.com/Alay-Merchant/LiveSub/archive/refs/heads/main.zip) and unzip
3. Double-click `install.bat`

An NVIDIA GPU makes it fast; without one it falls back to CPU (use `model = "small"`).

## Use

| Action | How |
|---|---|
| Browser panel | `panel.bat` — opens http://localhost:7765 with Start/Stop buttons and live status |
| Start | `run.bat` — wait for "● listening on …" |
| Stop | tray icon → Quit (or `stop.bat`) |
| Pause/resume | `Ctrl+Alt+L`, or the tray icon |
| Recent lines | `Ctrl+Alt+H` — pops the last 8 subtitles if you missed one |
| Settings | `settings.bat` or tray icon — language, model, size, colors, position |
| Transcript | `livesub.log` — everything from the last session |

**Pro tip — glossary:** rename `glossary.txt.example` to `glossary.txt` and list the
character names of the show you're watching. Name recognition improves dramatically.

First run downloads the Whisper model (~1.5 GB for `medium`), one time.

## Tips

- **Streaming sites (Netflix, Disney+):** watch in Chrome or Firefox. The Windows
  apps and Edge use protected audio that can't be captured.
- **Subtitles in a language other than English:** set `target` in settings and run
  [Ollama](https://ollama.com) locally — English output needs nothing extra.
- **Accuracy vs speed:** `medium` model is the sweet spot on a 4 GB GPU; `small` if
  you're on CPU.

## Compatibility

Crowdsourced — [report yours](../../issues/new?template=compatibility_report.md)!

| Source | Works? | Notes |
|---|---|---|
| YouTube (any browser) | ✅ | |
| Local video players (VLC, mpv…) | ✅ | |
| Netflix / Disney+ in Chrome or Firefox | ✅ | software DRM, captures fine |
| Netflix / Disney+ in Edge or Windows apps | ❌ | protected audio path — captures silence |
| Discord / Zoom calls | ✅ | captures the *other* side (your mic is never captured) |
| Games | ✅ | dialogue-heavy games work well |

## How it works

System audio (WASAPI loopback) → rolling 5s windows → faster-whisper
(transcribe + translate on-device) → silence-boundary commit for stable text →
transparent always-on-top Tkinter overlay. ~2–3s behind live speech, which is the
practical floor for chunked live transcription.

## License

MIT
