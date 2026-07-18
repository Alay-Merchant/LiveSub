"""Settings window: edits config.toml, restarts LiveSub on save."""
import pathlib
import re
import subprocess
import tkinter as tk
from tkinter import ttk

HERE = pathlib.Path(__file__).parent
CFG = HERE / "config.toml"

LANGS = ["auto", "ja", "fr", "en", "ko", "zh", "de", "es", "it", "pt", "ru", "hi", "ar", "nl", "pl", "tr", "vi", "th"]
FIELDS = [
    # key, label, widget, options
    ("source", "Spoken language", "combo", LANGS),
    ("target", "Subtitle language", "combo", ["en", "fr", "de", "es", "ja"]),
    ("model", "Model (speed vs accuracy)", "combo", ["small", "medium"]),  # large-v3: 18s/chunk on 4GB GPU, unusable
    ("font_size", "Text size", "spin", (8, 48)),
    ("opacity", "Opacity", "spin", (0.2, 1.0)),
    ("bottom_margin", "Distance from bottom (px)", "spin", (0, 600)),
    ("voice_focus", "Voice focus (cut music)", "combo", ["true", "false"]),
    ("vocal_isolation", "AI vocal isolation (lags on this GPU)", "combo", ["false", "true"]),
    ("text_color", "Text color", "entry", None),
    ("outline_color", "Outline color", "entry", None),
]


class RawBool(str):
    """Written to toml unquoted (true/false)."""


def read_cfg():
    import tomllib
    return tomllib.loads(CFG.read_text(encoding="utf-8"))


def write_cfg(values):
    # ponytail: regex-patch existing lines, keeps comments; full toml writer not needed
    text = CFG.read_text(encoding="utf-8")
    for key, val in values.items():
        quoted = isinstance(val, str) and not isinstance(val, RawBool)
        rep = f'{key} = "{val}"' if quoted else f"{key} = {val}"
        text = re.sub(rf"^{key}\s*=\s*[^#\n]*", rep + " ", text, flags=re.M)
    CFG.write_text(text, encoding="utf-8")


def main():
    cfg = read_cfg()
    root = tk.Tk()
    root.title("LiveSub Settings")
    root.resizable(False, False)
    frm = ttk.Frame(root, padding=16)
    frm.pack()
    vars = {}
    for row, (key, label, kind, opt) in enumerate(FIELDS):
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 12))
        v = tk.StringVar(value=str(cfg.get(key, "")))
        vars[key] = v
        if kind == "combo":
            ttk.Combobox(frm, textvariable=v, values=opt, width=16, state="readonly").grid(row=row, column=1)
        elif kind == "spin":
            ttk.Spinbox(frm, textvariable=v, from_=opt[0], to=opt[1],
                        increment=0.05 if isinstance(opt[0], float) else 1, width=17).grid(row=row, column=1)
        else:
            ttk.Entry(frm, textvariable=v, width=19).grid(row=row, column=1)

    status = ttk.Label(frm, text="")
    status.grid(row=len(FIELDS) + 1, column=0, columnspan=2)

    def save():
        out = {}
        for key, v in vars.items():
            s = v.get().strip()
            if key in ("font_size", "bottom_margin"):
                out[key] = int(float(s))
            elif key == "opacity":
                out[key] = float(s)
            elif key in ("voice_focus", "vocal_isolation"):
                out[key] = RawBool(s.lower())
            else:
                out[key] = s
        write_cfg(out)
        pid_file = HERE / "livesub.pid"
        if pid_file.exists():
            subprocess.run(["taskkill", "/f", "/pid", pid_file.read_text().strip()],
                           capture_output=True)
        subprocess.Popen([str(HERE / ".venv" / "Scripts" / "pythonw.exe"), str(HERE / "livesub.py")])
        status.config(text="Saved — LiveSub restarted")

    ttk.Button(frm, text="Save & restart LiveSub", command=save).grid(
        row=len(FIELDS), column=0, columnspan=2, pady=(12, 2))
    root.mainloop()


if __name__ == "__main__":
    main()


def test():
    cfg = read_cfg()
    assert "model" in cfg
    write_cfg({"font_size": cfg.get("font_size", 14)})  # round-trip, no-op change
    assert read_cfg()["font_size"] == cfg.get("font_size", 14)
    print("settings round-trip ok")
