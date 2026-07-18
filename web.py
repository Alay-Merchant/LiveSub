"""LiveSub control panel: http://localhost:7765 — start/stop + all settings.

Security: binds 127.0.0.1 only; Host-header check (blocks DNS rebinding);
custom-header requirement on POSTs (blocks cross-site form/fetch CSRF);
every config value validated against a whitelist before touching disk.
"""
import http.server
import json
import pathlib
import re
import subprocess

HERE = pathlib.Path(__file__).parent
PID = HERE / "livesub.pid"
LOG = HERE / "livesub.log"
CFG = HERE / "config.toml"
PORT = 7765

LANGS = ["auto", "ja", "fr", "en", "ko", "zh", "de", "es", "it", "pt", "ru",
         "hi", "gu", "ta", "te", "bn", "mr", "pa", "ur",
         "ar", "nl", "pl", "tr", "vi", "th"]
TARGETS = ["en", "fr", "de", "es", "ja", "hi", "it", "pt"]

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

# key -> (label, kind, constraint)
FIELDS = {
    "source": ("Spoken language", "select", LANGS),
    "target": ("Subtitle language", "select", TARGETS),
    "model": ("Whisper model", "select", WHISPER_MODELS),
    "translation_provider": ("Translation via (whisper = free built-in)", "select", ["whisper", "ollama", "openai", "anthropic"]),
    "ollama_model": ("Ollama model", "text", r"^[\w.:\-]{0,60}$"),
    "openai_model": ("OpenAI model", "text", r"^[\w.:\-]{0,60}$"),
    "anthropic_model": ("Claude model", "text", r"^[\w.:\-]{0,60}$"),
    "openai_api_key": ("OpenAI API key", "secret", r"^[\w.\-]{0,300}$"),
    "anthropic_api_key": ("Anthropic API key", "secret", r"^[\w.\-]{0,300}$"),
    "font_size": ("Text size", "int", (8, 48)),
    "opacity": ("Opacity", "float", (0.2, 1.0)),
    "bottom_margin": ("Distance from bottom (px)", "int", (0, 600)),
    "voice_focus": ("Voice focus (cut music)", "select", ["true", "false"]),
    "text_color": ("Text color", "color", None),
    "outline_color": ("Outline color", "color", None),
    "font": ("Font", "text", r"^[\w \-]{0,40}$"),
}

SECRET_KEYS = [k for k, (_, kind, _c) in FIELDS.items() if kind == "secret"]

# whisper model pre-download state — one at a time is plenty
DOWNLOAD = {"model": None, "status": None}


def model_cached(name: str) -> bool:
    hub = pathlib.Path.home() / ".cache" / "huggingface" / "hub"
    return any(hub.glob(f"models--*faster-whisper-{name}*"))


def download_model(name: str):
    DOWNLOAD.update(model=name, status="downloading")
    try:
        import truststore
        truststore.inject_into_ssl()
        from faster_whisper import WhisperModel
        WhisperModel(name, device="cpu", compute_type="int8")  # triggers HF download
        DOWNLOAD["status"] = "done"
    except Exception as e:
        DOWNLOAD["status"] = f"error: {e}"


def read_cfg():
    import tomllib
    return tomllib.loads(CFG.read_text(encoding="utf-8"))


def validate(key, raw):
    """Return the cleaned value or raise ValueError. Nothing unvalidated
    ever reaches config.toml (regex-patched file: injection would be code)."""
    label, kind, c = FIELDS[key]
    raw = str(raw).strip()
    if kind == "secret":
        if not re.fullmatch(c, raw):
            raise ValueError(key)
        return raw
    if kind == "select":
        if raw not in c:
            raise ValueError(key)
        return raw
    if kind == "int":
        v = int(raw)
        if not c[0] <= v <= c[1]:
            raise ValueError(key)
        return v
    if kind == "float":
        v = round(float(raw), 2)
        if not c[0] <= v <= c[1]:
            raise ValueError(key)
        return v
    if kind == "color":
        if raw != "" and not re.fullmatch(r"#[0-9a-fA-F]{6}", raw):
            raise ValueError(key)
        return raw
    if not re.fullmatch(c, raw):  # text
        raise ValueError(key)
    return raw


def write_cfg(values):
    text = CFG.read_text(encoding="utf-8")
    for key, val in values.items():
        if isinstance(val, str) and val not in ("true", "false"):
            rep = f'{key} = "{val}"'
        else:
            rep = f"{key} = {val}"
        text = re.sub(rf"^{key}\s*=\s*[^#\n]*", rep + " ", text, flags=re.M)
    CFG.write_text(text, encoding="utf-8")


def _pid():
    if not PID.exists():
        return None
    raw = PID.read_text().strip()
    return raw if raw.isdigit() else None  # never pass unvalidated text to taskkill


def alive():
    pid = _pid()
    if not pid:
        return False
    r = subprocess.run(["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"],
                       capture_output=True, text=True)
    return "python" in r.stdout


def start():
    if not alive():
        subprocess.Popen([str(HERE / ".venv" / "Scripts" / "pythonw.exe"), str(HERE / "livesub.py")])


def stop():
    pid = _pid()
    if pid:
        subprocess.run(["taskkill", "/f", "/pid", pid], capture_output=True)
        PID.unlink(missing_ok=True)


PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>LiveSub</title>
<style>
 :root{--bg:#141414;--card:#1e1e1e;--fg:#eee;--dim:#9a9a9a;--green:#4caf50;--red:#b33}
 *{box-sizing:border-box}body{font-family:'Segoe UI',sans-serif;background:var(--bg);color:var(--fg);
  margin:0;display:flex;flex-direction:column;align-items:center;padding:24px 12px}
 .card{background:var(--card);border-radius:12px;padding:20px 24px;margin:10px;width:min(560px,95vw)}
 h1{margin:.2em 0;font-size:1.6rem}h2{font-size:1rem;color:var(--dim);margin:0 0 12px;font-weight:600}
 #dot{display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:8px;background:#666}
 #dot.on{background:var(--green)}
 button{font:inherit;padding:.55em 1.6em;border:0;border-radius:8px;cursor:pointer;color:#fff}
 #start{background:var(--green)}#stop{background:var(--red)}#save{background:#3a6ea5;width:100%}
 button:disabled{opacity:.35;cursor:default}
 .row{display:flex;justify-content:space-between;align-items:center;margin:8px 0;gap:12px}
 .row label{color:var(--dim);font-size:.92rem}
 select,input{font:inherit;background:#2a2a2a;color:var(--fg);border:1px solid #3a3a3a;
  border-radius:6px;padding:.35em .5em;width:180px}
 input[type=color]{padding:2px;height:2.1em}
 #log{color:var(--dim);font-size:.85rem;white-space:pre-wrap;font-family:'Cascadia Code',monospace;min-height:3em}
 #msg{font-size:.85rem;color:var(--green);height:1.2em;margin-top:6px;text-align:center}
 .hint{font-size:.78rem;color:#777;margin-top:10px}
</style></head><body>
<h1>LiveSub</h1>

<div class="card">
 <div class="row"><span><span id="dot"></span><b id="status">…</b></span>
 <span><button id="start" onclick="act('start')">Start</button>
 <button id="stop" onclick="act('stop')">Stop</button></span></div>
 <div class="hint">Hotkeys while running: Ctrl+Alt+L pause · Ctrl+Alt+H recent lines</div>
</div>

<div class="card"><h2>Whisper models</h2>
 <div id="modelStatus" class="hint"></div>
 <div class="row"><label>Pre-download a model</label>
 <span><select id="dlModel"></select>
 <button id="dlBtn" onclick="dl()" style="background:#3a6ea5">Download</button></span></div>
 <div id="dlStatus" class="hint"></div>
</div>

<div class="card"><h2>Settings</h2><div id="form"></div>
 <button id="save" onclick="save()">Save settings</button><div id="msg"></div>
 <div class="hint">Saving restarts LiveSub if it's running. No API key or account is
 needed — the built-in whisper provider is free and fully local; keys only matter
 if you switch to OpenAI or Anthropic.</div>
</div>

<div class="card"><h2>Recent subtitles</h2><div id="log">—</div></div>

<script>
const FIELDS = __FIELDS__;
const H = {'X-LiveSub':'1'};  // custom header: CSRF guard
function el(id){return document.getElementById(id)}
function buildForm(cfg){
  el('form').innerHTML = Object.entries(FIELDS).map(([k, f]) => {
    const v = cfg[k] ?? '';
    if (f.kind === 'select')
      return `<div class="row"><label>${f.label}</label><select id="f_${k}">` +
        f.options.map(o => `<option ${String(v)===o?'selected':''}>${o}</option>`).join('') + `</select></div>`;
    if (f.kind === 'secret')
      return `<div class="row"><label>${f.label}</label><input type="password" id="f_${k}" value=""
        placeholder="${v === '•saved•' ? '•••••• (saved — leave blank to keep)' : 'not set'}"></div>`;
    if (f.kind === 'color')
      return `<div class="row"><label>${f.label}</label><input type="color" id="f_${k}" value="${v||'#ffffff'}"></div>`;
    if (f.kind === 'int' || f.kind === 'float')
      return `<div class="row"><label>${f.label}</label><input type="number" id="f_${k}" value="${v}"
        min="${f.min}" max="${f.max}" step="${f.kind==='float'?0.05:1}"></div>`;
    return `<div class="row"><label>${f.label}</label><input id="f_${k}" value="${v}"></div>`;
  }).join('');
}
async function refresh(){
  const s = await (await fetch('/status')).json();
  el('dot').className = s.running ? 'on' : '';
  el('status').textContent = s.running ? 'Running — subtitles on screen' : 'Stopped';
  el('start').disabled = s.running; el('stop').disabled = !s.running;
  el('log').textContent = s.log || '—';
  el('modelStatus').innerHTML = Object.entries(s.cached)
    .map(([m, c]) => `${m} ${c ? '✓' : '·'}`).join(' &nbsp; ');
  if (!el('dlModel').options.length)
    el('dlModel').innerHTML = Object.keys(s.cached).map(m => `<option>${m}</option>`).join('');
  const d = s.download;
  el('dlStatus').textContent = d.model ? `${d.model}: ${d.status}` : '';
  el('dlBtn').disabled = d.status === 'downloading';
}
async function dl(){
  await fetch('/download', {method:'POST', headers:{...H,'Content-Type':'application/json'},
                            body: JSON.stringify({model: el('dlModel').value})});
  setTimeout(refresh, 500);
}
async function act(a){ await fetch('/'+a, {method:'POST', headers:H}); setTimeout(refresh, 600); }
async function save(){
  const body = {};
  for (const k of Object.keys(FIELDS)) body[k] = el('f_'+k).value;
  const r = await fetch('/config', {method:'POST', headers:{...H,'Content-Type':'application/json'},
                                    body: JSON.stringify(body)});
  el('msg').textContent = r.ok ? 'Saved ✓' : 'Invalid value: ' + await r.text();
  el('msg').style.color = r.ok ? 'var(--green)' : 'var(--red)';
  setTimeout(()=>{el('msg').textContent=''}, 4000); setTimeout(refresh, 800);
}
(async () => { buildForm(await (await fetch('/config')).json()); refresh(); setInterval(refresh, 2000); })();
</script></body></html>"""

FIELDS_JS = json.dumps({
    k: ({"label": l, "kind": kind, "options": c} if kind == "select" else
        {"label": l, "kind": kind, "min": c[0], "max": c[1]} if kind in ("int", "float") else
        {"label": l, "kind": kind})
    for k, (l, kind, c) in FIELDS.items()})


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _guard(self, post):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("localhost", "127.0.0.1"):
            self.send_error(403)  # DNS-rebinding guard
            return False
        if post and self.headers.get("X-LiveSub") != "1":
            self.send_error(403)  # cross-site POSTs can't set custom headers
            return False
        return True

    def _send(self, body, code=200, ctype="text/html"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._guard(post=False):
            return
        if self.path == "/status":
            tail = ""
            if LOG.exists():
                lines = [l for l in LOG.read_text(encoding="utf-8", errors="replace").splitlines()
                         if "clickthrough" not in l]
                tail = "\n".join(lines[-8:])
            self._send(json.dumps({
                "running": alive(), "log": tail,
                "download": DOWNLOAD,
                "cached": {m: model_cached(m) for m in WHISPER_MODELS},
            }), ctype="application/json")
        elif self.path == "/config":
            cfg = {k: v for k, v in read_cfg().items() if k in FIELDS}
            for k in SECRET_KEYS:  # never send stored keys to the browser
                cfg[k] = "•saved•" if cfg.get(k) else ""
            self._send(json.dumps(cfg), ctype="application/json")
        elif self.path == "/":
            self._send(PAGE.replace("__FIELDS__", FIELDS_JS))
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._guard(post=True):
            return
        if self.path == "/start":
            start()
        elif self.path == "/stop":
            stop()
        elif self.path == "/download":
            n = int(self.headers.get("Content-Length", 0))
            if n > 1000:
                return self.send_error(413)
            try:
                name = json.loads(self.rfile.read(n)).get("model")
            except json.JSONDecodeError:
                return self._send("bad json", code=400, ctype="text/plain")
            if name not in WHISPER_MODELS:
                return self._send("unknown model", code=400, ctype="text/plain")
            if DOWNLOAD["status"] != "downloading":
                import threading
                threading.Thread(target=download_model, args=(name,), daemon=True).start()
        elif self.path == "/config":
            n = int(self.headers.get("Content-Length", 0))
            if n > 10_000:
                return self.send_error(413)
            try:
                incoming = json.loads(self.rfile.read(n))
                incoming = {k: v for k, v in incoming.items()
                            if not (k in SECRET_KEYS and str(v).strip() in ("", "•saved•"))}
                cleaned = {k: validate(k, v) for k, v in incoming.items() if k in FIELDS}
            except (ValueError, json.JSONDecodeError) as e:
                return self._send(str(e), code=400, ctype="text/plain")
            write_cfg(cleaned)
            if alive():
                stop()
                start()
        else:
            return self.send_error(404)
        self._send("ok", ctype="text/plain")


if __name__ == "__main__":
    print(f"LiveSub panel: http://localhost:{PORT}")
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
