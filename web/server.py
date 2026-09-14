#!/usr/bin/env python3
"""Status dashboard for an AltServer-Linux deployment.

    python3 web/server.py [--port 8099] [--host 127.0.0.1]

Stdlib only. Serves a single auto-refreshing page plus /api/status returning the same data as
JSON, so it doubles as the watchdog endpoint something else can poll.

WHY THIS EXISTS. AltServer cannot report its own health, and neither can the phone:

  * avahi can report a successful registration while publishing nothing, so the only trustworthy
    advertisement test is an external browse.
  * AltStore suppresses the one error it would otherwise raise during an unattended refresh
    (BackgroundRefreshAppsOperation sets ignoresServerNotFoundError = true).
  * Almost everything AltServer logs goes to stdout at info level, so `journalctl -p err` stays
    empty no matter what breaks.

Net effect without something like this: a deployment stops refreshing and the first symptom is an
app that will not open, seven days later, with no signal anywhere in between.

SCOPE. Read-only diagnostics. It deliberately does NOT sign in or handle 2FA yet -- that needs a
supervisor that owns the AltServer child's stdin, and it should not be built against an
authentication flow that is not yet working.

Bind to 127.0.0.1 unless you understand the consequences: this reports device identifiers and
should not be exposed to the LAN, and never to the internet.
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import status_checks  # noqa: E402
import pairing  # noqa: E402

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AltServer status</title>
<style>
  :root {
    --bg:#f6f7f9; --card:#fff; --fg:#14161a; --muted:#5b6370; --line:#e3e6ea;
    --ok:#177245; --okbg:#e8f5ee; --warn:#8a6100; --warnbg:#fdf3e0;
    --fail:#a01b2b; --failbg:#fdeaec; --unknown:#4a5160; --unknownbg:#eef0f3;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#14161a; --card:#1c1f25; --fg:#e8eaed; --muted:#9aa3b0; --line:#2a2f37;
      --ok:#5cd6a0; --okbg:#122a20; --warn:#e8b866; --warnbg:#2b2213;
      --fail:#ff8a94; --failbg:#2d1519; --unknown:#9aa3b0; --unknownbg:#22262c;
    }
  }
  * { box-sizing:border-box; }
  body { margin:0; padding:2rem 1rem; background:var(--bg); color:var(--fg);
         font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif; }
  .wrap { max-width:820px; margin:0 auto; }
  header { display:flex; align-items:baseline; justify-content:space-between;
           gap:1rem; flex-wrap:wrap; margin-bottom:1.25rem; }
  h1 { font-size:1.3rem; margin:0; letter-spacing:-0.01em; }
  .meta { color:var(--muted); font-size:.85rem; }
  .overall { display:inline-block; padding:.2rem .6rem; border-radius:999px;
             font-weight:600; font-size:.8rem; letter-spacing:.02em; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:.9rem 1rem; margin-bottom:.6rem; }
  .row { display:flex; align-items:center; gap:.7rem; }
  .pill { flex:none; padding:.12rem .5rem; border-radius:6px; font-size:.72rem;
          font-weight:700; text-transform:uppercase; letter-spacing:.04em; }
  .name { font-weight:600; flex:none; min-width:11rem; }
  .summary { color:var(--fg); }
  .detail { color:var(--muted); font-size:.85rem; margin-top:.4rem;
            word-break:break-word; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  .fix { margin-top:.5rem; padding:.5rem .65rem; border-radius:7px;
         background:var(--unknownbg); font-size:.85rem; }
  .fix b { font-weight:600; }
  .ok .pill{background:var(--okbg);color:var(--ok)} .warn .pill{background:var(--warnbg);color:var(--warn)}
  .fail .pill{background:var(--failbg);color:var(--fail)} .unknown .pill{background:var(--unknownbg);color:var(--unknown)}
  .overall.ok{background:var(--okbg);color:var(--ok)} .overall.warn{background:var(--warnbg);color:var(--warn)}
  .overall.fail{background:var(--failbg);color:var(--fail)}
  footer { color:var(--muted); font-size:.8rem; margin-top:1.5rem; }
  @media (max-width:640px){ .row{flex-wrap:wrap} .name{min-width:0} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>AltServer status</h1>
    <div class="meta">
      <span id="overall" class="overall">checking…</span>
      &nbsp;<span id="host"></span> &middot; <span id="when"></span>
    </div>
  </header>
  <div id="checks"></div>
  <footer>
    Refreshes every 30s. Read-only &mdash; this page does not sign in or change anything.
    Raw JSON at <code>/api/status</code>. &middot; <a href="/pairing">Pairing setup &rarr;</a>
  </footer>
</div>
<script>
async function load() {
  try {
    const r = await fetch('/api/status', {cache:'no-store'});
    const d = await r.json();
    const o = document.getElementById('overall');
    o.textContent = d.overall; o.className = 'overall ' + d.overall;
    document.getElementById('host').textContent = d.host || '';
    document.getElementById('when').textContent = new Date().toLocaleTimeString();
    document.getElementById('checks').innerHTML = d.checks.map(c => `
      <div class="card ${c.state}">
        <div class="row">
          <span class="pill">${c.state}</span>
          <span class="name">${esc(c.name)}</span>
          <span class="summary">${esc(c.summary)}</span>
        </div>
        ${c.detail ? `<div class="detail">${esc(c.detail)}</div>` : ''}
        ${c.fix ? `<div class="fix"><b>Try:</b> ${esc(c.fix)}</div>` : ''}
      </div>`).join('');
  } catch (e) {
    document.getElementById('checks').innerHTML =
      '<div class="card fail"><div class="row"><span class="pill">fail</span>' +
      '<span class="summary">Status service unreachable</span></div></div>';
  }
}
function esc(s){ return String(s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
load(); setInterval(load, 30000);
</script>
</body>
</html>
"""


PAIRING_PAGE = PAGE.replace("<title>AltServer status</title>", "<title>Pair your iPhone</title>")

PAIRING_PAGE = PAIRING_PAGE[:PAIRING_PAGE.index("<body>")] + """<body>
<div class="wrap">
  <header>
    <h1>Pair your iPhone</h1>
    <div class="meta"><span id="when"></span> &middot; <a href="/">&larr; Status</a></div>
  </header>
  <p class="meta" style="margin-top:-.5rem">
    A USB cable is needed for this once, and only once. Wireless pairing is not supported, but
    after this step refreshing happens over Wi-Fi and the cable is never needed again.
  </p>
  <div id="steps"></div>
  <footer>Re-checks every 5s while you work. Run the commands shown on the server itself.</footer>
</div>
<script>
function esc(s){ return String(s).replace(/[&<>\"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c])); }
const PILL = {ok:'ok', todo:'warn', blocked:'fail'};
async function load(){
  try{
    const d = await (await fetch('/api/pairing',{cache:'no-store'})).json();
    document.getElementById('when').textContent =
      d.paired ? 'Paired \u2713' : ('Next: ' + (d.next||''));
    document.getElementById('steps').innerHTML = d.steps.map((s,i) => `
      <div class="card ${PILL[s.state]||'unknown'}">
        <div class="row">
          <span class="pill">${s.state==='ok'?'done':s.state}</span>
          <span class="name">${i+1}. ${esc(s.title)}</span>
        </div>
        ${s.detail ? `<div class="detail">${esc(s.detail)}</div>` : ''}
        ${s.action ? `<div class="fix"><b>Run on the server:</b><br>
           <code style="user-select:all">${esc(s.action)}</code></div>` : ''}
        ${s.note ? `<div class="fix" style="white-space:pre-line">${esc(s.note)}</div>` : ''}
      </div>`).join('');
  }catch(e){
    document.getElementById('steps').innerHTML =
      '<div class="card fail"><div class="row"><span class="pill">fail</span>' +
      '<span class="summary">Status service unreachable</span></div></div>';
  }
}
load(); setInterval(load, 5000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "AltServerStatus/0.1"

    def _send(self, code, body, content_type):
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif path == "/pairing":
            self._send(200, PAIRING_PAGE, "text/html; charset=utf-8")
        elif path == "/api/pairing":
            try:
                data = pairing.diagnose()
            except Exception as exc:
                data = {"steps": [{"title": "Pairing check failed", "state": "blocked",
                                   "detail": str(exc), "action": "", "note": ""}],
                        "udids": [], "paired": False, "next": "Pairing check failed"}
            self._send(200, json.dumps(data), "application/json")
        elif path == "/api/status":
            try:
                data = status_checks.run_all()
            except Exception as exc:  # never let a check crash the dashboard
                data = {"overall": "fail", "host": "", "checks": [{
                    "name": "Status service", "state": "fail",
                    "summary": "A check raised an exception", "detail": str(exc), "fix": ""}]}
            self._send(200, json.dumps(data), "application/json")
        else:
            self._send(404, "not found\n", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        pass  # the dashboard polls every 30s; logging that is pure noise


def main():
    ap = argparse.ArgumentParser(description="AltServer-Linux status dashboard")
    ap.add_argument("--host", default="127.0.0.1",
                    help="bind address (default 127.0.0.1; this reports device identifiers, so "
                         "do not expose it)")
    ap.add_argument("--port", type=int, default=8099)
    args = ap.parse_args()

    print("AltServer status dashboard on http://%s:%d" % (args.host, args.port), flush=True)
    HTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
