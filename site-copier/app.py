#!/usr/bin/env python3
"""Site Copier desktop app.

Starts a small private server on this computer and opens the app in your
browser. Close the browser tab and the app quits by itself.
"""
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from copy_site import SiteCopier

TOKEN = secrets.token_urlsafe(16)
OUTPUT_ROOT = Path.home() / "Site Copier"
IDLE_EXIT_SECONDS = 45


class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.copier = None
        self.lines = []
        self.running = False
        self.out = None
        self.last_ping = time.time()

    def log(self, msg):
        with self.lock:
            self.lines.append(msg.strip("\n"))

    def start(self, url, name, max_pages):
        with self.lock:
            if self.running:
                return False
            self.lines, self.running = [], True
        url = url if "://" in url else "https://" + url
        safe = re.sub(r"[^A-Za-z0-9 _.-]+", "_", name).strip() or "my-site"
        self.out = str(OUTPUT_ROOT / safe)
        self.copier = SiteCopier(url, self.out, max_pages, log=self.log)
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self):
        try:
            self.copier.run()
            if self.copier.cancelled:
                self.log("Stopped. Everything copied so far was kept.")
        except Exception as e:  # show any crash in the app instead of dying silently
            self.log(f"  ! Something went wrong: {e}")
        finally:
            with self.lock:
                self.running = False

    def status(self, since):
        with self.lock:
            c = self.copier
            return {
                "running": self.running,
                "lines": self.lines[since:],
                "total": len(self.lines),
                "pages": len(c.pages) if c else 0,
                "files": sum(1 for v in c.assets.values() if v) if c else 0,
                "out": self.out,
                "has_site": bool(self.out and os.path.exists(os.path.join(self.out, "index.html"))),
            }


JOB = Job()


def open_path(path):
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606 - opening the user's own output
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            body = PAGE.replace("__TOKEN__", TOKEN).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            data = {}
        if data.get("token") != TOKEN:  # only our own page may control the app
            return self.send_json({"error": "forbidden"}, 403)
        JOB.last_ping = time.time()

        if self.path == "/status":
            return self.send_json(JOB.status(int(data.get("since", 0))))
        if self.path == "/start":
            url = (data.get("url") or "").strip()
            if not url:
                return self.send_json({"error": "Please enter a website address."}, 400)
            pages = max(1, min(500, int(data.get("max_pages") or 20)))
            ok = JOB.start(url, data.get("name") or "my-site", pages)
            return self.send_json({"ok": ok})
        if self.path == "/stop":
            if JOB.copier:
                JOB.copier.cancelled = True
            return self.send_json({"ok": True})
        if self.path == "/open":
            if JOB.out and os.path.exists(JOB.out):
                target = JOB.out
                if data.get("what") == "site":
                    target = os.path.join(JOB.out, "index.html")
                open_path(target)
            return self.send_json({"ok": True})
        self.send_error(404)


def watchdog(server):
    while True:
        time.sleep(5)
        if not JOB.running and time.time() - JOB.last_ping > IDLE_EXIT_SECONDS:
            server.shutdown()
            return


def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Site Copier is running at {url}  (close the browser tab to quit)", flush=True)
    threading.Thread(target=watchdog, args=(server,), daemon=True).start()
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    server.serve_forever()


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Site Copier</title>
<style>
  :root {
    --bg: #f4f5fb; --card: #ffffff; --text: #1b1d29; --muted: #6b7084; --line: #e3e5ef;
    --accent: #5b5bf0; --accent2: #9a4ff0; --ok: #1f9d55; --bad: #d64545;
    --log: #12131c; --logtext: #c9cde0; --field: #f7f8fc;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #0f1017; --card: #181a24; --text: #eceef6; --muted: #9196ab; --line: #2a2d3b;
            --field: #10121a; --log: #0a0b10; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; background: var(--bg); color: var(--text);
         font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         display: flex; justify-content: center; padding: 40px 16px; }
  .wrap { width: 100%; max-width: 720px; }
  .brand { display: flex; align-items: center; gap: 14px; margin-bottom: 24px; }
  .logo { width: 48px; height: 48px; border-radius: 14px; display: grid; place-items: center;
          background: linear-gradient(135deg, var(--accent), var(--accent2)); color: #fff;
          box-shadow: 0 8px 24px -8px var(--accent); }
  h1 { font-size: 24px; margin: 0; letter-spacing: -.02em; }
  .sub { color: var(--muted); margin: 2px 0 0; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 16px;
          padding: 24px; box-shadow: 0 10px 30px -18px rgba(20,20,60,.35); margin-bottom: 16px; }
  label { display: block; font-weight: 600; font-size: 13px; margin: 0 0 6px; }
  .hint { color: var(--muted); font-weight: 400; }
  input { width: 100%; padding: 12px 14px; font: inherit; color: var(--text); background: var(--field);
          border: 1.5px solid var(--line); border-radius: 10px; outline: none; transition: border .15s, box-shadow .15s; }
  input:focus { border-color: var(--accent); box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 18%, transparent); }
  .grid { display: grid; grid-template-columns: 1fr 160px; gap: 14px; margin-top: 16px; }
  @media (max-width: 520px) { .grid { grid-template-columns: 1fr; } }
  .row { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 20px; }
  button { font: inherit; font-weight: 600; border: 0; border-radius: 10px; padding: 12px 18px; cursor: pointer;
           transition: transform .08s, opacity .15s; }
  button:active { transform: scale(.98); }
  button:disabled { opacity: .45; cursor: not-allowed; }
  .primary { flex: 1; color: #fff; background: linear-gradient(135deg, var(--accent), var(--accent2)); }
  .ghost { background: var(--field); color: var(--text); border: 1.5px solid var(--line); }
  .danger { background: transparent; color: var(--bad); border: 1.5px solid var(--bad); }
  .stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
  .stat { background: var(--field); border: 1px solid var(--line); border-radius: 12px; padding: 14px; }
  .stat b { display: block; font-size: 22px; }
  .stat span { color: var(--muted); font-size: 13px; }
  .status { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; font-weight: 600; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--muted); }
  .dot.run { background: var(--accent); animation: pulse 1s infinite; }
  .dot.ok { background: var(--ok); }
  @keyframes pulse { 50% { opacity: .3; } }
  .bar { height: 6px; border-radius: 6px; background: var(--line); overflow: hidden; margin: 14px 0; }
  .bar i { display: block; height: 100%; width: 0; background: linear-gradient(90deg, var(--accent), var(--accent2));
           transition: width .3s; }
  .bar.indef i { width: 35%; animation: slide 1.2s infinite ease-in-out; }
  @keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(300%); } }
  pre { background: var(--log); color: var(--logtext); border-radius: 12px; padding: 14px; margin: 0;
        height: 220px; overflow: auto; font: 12px/1.6 ui-monospace, Menlo, Consolas, monospace; white-space: pre-wrap; word-break: break-all; }
  .err { color: var(--bad); font-size: 14px; margin-top: 10px; min-height: 1em; }
  .foot { color: var(--muted); font-size: 12.5px; text-align: center; margin-top: 8px; }
  .hidden { display: none; }
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">
    <div class="logo"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/></svg></div>
    <div><h1>Site Copier</h1><p class="sub">Save a website you own as files you can edit.</p></div>
  </div>

  <div class="card">
    <label for="url">Website address</label>
    <input id="url" placeholder="https://your-site.com" autofocus>
    <div class="grid">
      <div><label for="name">Folder name <span class="hint">— saved in “Site Copier” in your home folder</span></label>
        <input id="name" placeholder="my-site"></div>
      <div><label for="pages">Max pages</label><input id="pages" type="number" min="1" max="500" value="20"></div>
    </div>
    <div class="row">
      <button id="go" class="primary">Copy website</button>
      <button id="stop" class="danger hidden">Stop</button>
    </div>
    <div class="err" id="err"></div>
  </div>

  <div class="card hidden" id="progress">
    <div class="status"><span class="dot" id="dot"></span><span id="stext">Starting…</span></div>
    <div class="stats">
      <div class="stat"><b id="np">0</b><span>Pages</span></div>
      <div class="stat"><b id="nf">0</b><span>Files</span></div>
      <div class="stat"><b id="nt">0s</b><span>Time</span></div>
    </div>
    <div class="bar indef" id="bar"><i></i></div>
    <pre id="log"></pre>
    <div class="row">
      <button id="openSite" class="primary" disabled>Open copied site</button>
      <button id="openFolder" class="ghost" disabled>Show folder</button>
    </div>
  </div>

  <p class="foot">Open the copied site, click <b>Edit</b> in the corner, change anything, then <b>Save</b>.<br>
  Only copy websites you own or have permission to copy.</p>
</div>
<script>
const TOKEN = "__TOKEN__";
const $ = id => document.getElementById(id);
let since = 0, started = 0, timer = null, wasRunning = false;

async function call(path, body = {}) {
  const r = await fetch(path, { method: "POST", body: JSON.stringify({ ...body, token: TOKEN }) });
  return r.json();
}

$("url").addEventListener("input", () => {
  try {
    const host = new URL($("url").value.includes("://") ? $("url").value : "https://" + $("url").value).hostname;
    $("name").placeholder = host.replace(/^www\./, "") || "my-site";
  } catch { $("name").placeholder = "my-site"; }
});
$("url").addEventListener("keydown", e => { if (e.key === "Enter") $("go").click(); });

$("go").onclick = async () => {
  $("err").textContent = "";
  const res = await call("/start", {
    url: $("url").value, name: $("name").value || $("name").placeholder, max_pages: +$("pages").value
  });
  if (res.error) { $("err").textContent = res.error; return; }
  since = 0; started = Date.now(); $("log").textContent = "";
  $("progress").classList.remove("hidden");
  setRunning(true);
};
$("stop").onclick = () => call("/stop");
$("openSite").onclick = () => call("/open", { what: "site" });
$("openFolder").onclick = () => call("/open", { what: "folder" });

function setRunning(on) {
  $("go").disabled = on;
  $("stop").classList.toggle("hidden", !on);
  $("dot").className = "dot " + (on ? "run" : "ok");
  $("bar").classList.toggle("indef", on);
  $("bar").querySelector("i").style.width = on ? "" : "100%";
  wasRunning = on;
}

async function poll() {
  try {
    const s = await call("/status", { since });
    if (s.lines.length) {
      const log = $("log"), atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
      log.textContent += s.lines.join("\n") + "\n";
      if (atBottom) log.scrollTop = log.scrollHeight;
    }
    since = s.total;
    $("np").textContent = s.pages; $("nf").textContent = s.files;
    if (s.running) {
      $("nt").textContent = Math.round((Date.now() - started) / 1000) + "s";
      $("stext").textContent = "Copying…";
    } else if (wasRunning) {
      setRunning(false);
      $("stext").textContent = s.has_site ? "Done! Saved to " + s.out : "Finished — nothing could be saved.";
    }
    $("openSite").disabled = s.running || !s.has_site;
    $("openFolder").disabled = s.running || !s.out;
  } catch {
    $("stext").textContent = "The app has closed. Start Site Copier again to continue.";
    clearInterval(timer);
  }
}
timer = setInterval(poll, 700);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
