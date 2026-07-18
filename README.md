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
4. Double-click `panel.bat` and hit **Start**

An NVIDIA GPU makes it fast; without one it falls back to CPU (use `model = "small"`).

## Use

| Action | How |
|---|---|
**The easy way:** double-click `panel.bat` — your browser opens the LiveSub control
panel at http://localhost:7765 with Start/Stop, every setting (languages, model,
text size, colors, position), and a live feed of recent subtitles. Saving settings
restarts LiveSub automatically.

| Action | How |
|---|---|
| Control panel | `panel.bat` → http://localhost:7765 (start/stop + all settings) |
| Start / stop directly | `run.bat` / tray icon → Quit / `stop.bat` |
| Pause/resume | `Ctrl+Alt+L`, or the tray icon |
| Recent lines | `Ctrl+Alt+H` — pops the last 8 subtitles if you missed one |
| Settings (native app) | `settings.bat` |
| Transcript | `livesub.log` — everything from the last session |

The panel binds to 127.0.0.1 only and rejects cross-site requests — nothing is
reachable from outside your machine.

**Pro tip — glossary:** rename `glossary.txt.example` to `glossary.txt` and list the
character names of the show you're watching. Name recognition improves dramatically.

First run downloads the Whisper model (~1.5 GB for `medium`), one time.

## Tips

- **Streaming sites (Netflix, Disney+):** watch in Chrome or Firefox. The Windows
  apps and Edge use protected audio that can't be captured.
- **Whisper models:** pick `tiny`→`large-v3` in the panel (speed vs accuracy) and
  pre-download any of them with the Download button so first launch isn't slow.
- **Translation providers:** built-in Whisper (free, English out) is the default.
  For other output languages or higher quality, switch "Translation via" to
  [Ollama](https://ollama.com) (free, local), OpenAI, or Anthropic Claude — paid
  providers just need an API key pasted into the panel (stored locally, never sent
  anywhere except that provider).
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
