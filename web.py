"""LiveSub web control panel: http://localhost:7765 — start/stop the overlay."""
import http.server
import json
import pathlib
import subprocess

HERE = pathlib.Path(__file__).parent
PID = HERE / "livesub.pid"
LOG = HERE / "livesub.log"
PORT = 7765


def alive():
    if not PID.exists():
        return False
    pid = PID.read_text().strip()
    r = subprocess.run(["tasklist", "/fi", f"PID eq {pid}", "/fo", "csv", "/nh"],
                       capture_output=True, text=True)
    return "pythonw.exe" in r.stdout or "python.exe" in r.stdout


def start():
    if not alive():
        subprocess.Popen([str(HERE / ".venv" / "Scripts" / "pythonw.exe"), str(HERE / "livesub.py")])


def stop():
    if PID.exists():
        subprocess.run(["taskkill", "/f", "/pid", PID.read_text().strip()], capture_output=True)
        PID.unlink(missing_ok=True)


PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>LiveSub</title>
<style>
 body{font-family:'Cascadia Code','Segoe UI',monospace;background:#141414;color:#eee;
      display:flex;flex-direction:column;align-items:center;padding-top:8vh}
 h1{font-weight:600}#dot{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:8px}
 .on{background:#4caf50}.off{background:#666}
 button{font:inherit;font-size:1.2rem;padding:.7em 2.2em;margin:1em .5em;border:0;border-radius:8px;cursor:pointer}
 #start{background:#4caf50;color:#fff}#stop{background:#b33;color:#fff}
 button:disabled{opacity:.35;cursor:default}
 #log{margin-top:2em;width:min(640px,90vw);color:#9a9a9a;font-size:.85rem;white-space:pre-wrap}
</style></head><body>
<h1>LiveSub</h1>
<p><span id="dot" class="off"></span><span id="status">…</span></p>
<div><button id="start" onclick="act('start')">Start</button>
<button id="stop" onclick="act('stop')">Stop</button></div>
<div id="log"></div>
<script>
async function refresh(){
  const s = await (await fetch('/status')).json();
  document.getElementById('dot').className = s.running ? 'on' : 'off';
  document.getElementById('status').textContent = s.running ? 'running — subtitles on screen' : 'stopped';
  document.getElementById('start').disabled = s.running;
  document.getElementById('stop').disabled = !s.running;
  document.getElementById('log').textContent = s.log;
}
async function act(a){ await fetch('/' + a, {method:'POST'}); setTimeout(refresh, 500); }
refresh(); setInterval(refresh, 2000);
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence per-request console noise
        pass

    def _send(self, body, ctype="text/html"):
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/status":
            tail = ""
            if LOG.exists():
                lines = [l for l in LOG.read_text(encoding="utf-8", errors="replace").splitlines()
                         if "clickthrough" not in l]
                tail = "\n".join(lines[-8:])
            self._send(json.dumps({"running": alive(), "log": tail}), "application/json")
        else:
            self._send(PAGE)

    def do_POST(self):
        if self.path == "/start":
            start()
        elif self.path == "/stop":
            stop()
        self._send("ok", "text/plain")


if __name__ == "__main__":
    print(f"LiveSub panel: http://localhost:{PORT}")
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
