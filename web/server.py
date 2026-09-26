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

SCOPE. Status and pairing are read-only. /install is not: it signs in and takes the 2FA code in
the browser, via installer.py, which supervises an AltServer child and owns its stdin. That is
how a 2FA code reaches a `std::cin` read in a container with no terminal.

Bind to 127.0.0.1 unless you understand the consequences: this reports device identifiers and
should not be exposed to the LAN, and never to the internet.
"""

import argparse
import json
import os
import time
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import status_checks  # noqa: E402
import pairing  # noqa: E402
import installer  # noqa: E402

# The three pages share a <head> but each has its own <body>, so the tab bar is inserted into each
# rather than living in one template. aria-current is what actually marks the active tab -- the
# styling hangs off it, so a screen reader and the stylesheet cannot disagree about which is which.
_TABS = (("/", "Status"), ("/pairing", "Pairing"), ("/install", "Install AltStore"))


def _nav(active_href):
    links = []
    for href, label in _TABS:
        current = ' aria-current="page"' if href == active_href else ""
        links.append('    <a href="%s"%s>%s</a>' % (href, current, label))
    return '  <nav class="tabs">\n%s\n  </nav>\n' % "\n".join(links)


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
  .hrow { display:flex; align-items:center; gap:.6rem; margin-top:.5rem; }
  #histbox { position:relative; }
  .hbar { padding:5px 0; }
  .hname { flex:none; min-width:11rem; font-size:.85rem; color:var(--muted); }
  .hbar { display:flex; gap:1px; flex:1; min-width:0; }
  .hcell { flex:1 1 0; min-width:2px; height:14px; border-radius:2px; background:var(--unknownbg);
           transition:transform .13s cubic-bezier(.2,.8,.3,1), opacity .13s ease;
           transform-origin:50% 50%; will-change:transform; }
  /* Dock-style magnify: the hovered run grows most, its neighbours less, so the one under the
     cursor is unambiguous even when each bar is two pixels wide. */
  .hbar:hover .hcell { opacity:.5; }
  .hcell:hover { transform:scale(2.6,2.1); opacity:1; border-radius:3px; }
  .hcell:hover + .hcell,
  .hcell:has(+ .hcell:hover) { transform:scale(1.85,1.6); opacity:.95; }
  .hcell:hover + .hcell + .hcell,
  .hcell:has(+ .hcell + .hcell:hover) { transform:scale(1.35,1.3); opacity:.8; }
  @media (prefers-reduced-motion: reduce) { .hcell { transition:none; } }
  .histtip { position:absolute; z-index:5; pointer-events:none; padding:.3rem .5rem;
             border-radius:6px; background:var(--fg); color:var(--card); font-size:.75rem;
             white-space:nowrap; box-shadow:0 2px 8px rgba(0,0,0,.25); transform:translate(-50%,-115%); }
  .histtip b { font-weight:700; text-transform:uppercase; letter-spacing:.03em; }
  .hcell.ok{background:var(--ok)} .hcell.warn{background:var(--warn)}
  .hcell.fail{background:var(--fail)} .hcell.unknown{background:var(--unknown);opacity:.45}
  .hlast { flex:none; font-size:.78rem; color:var(--muted); min-width:9rem; text-align:right; }
  .ok .pill{background:var(--okbg);color:var(--ok)} .warn .pill{background:var(--warnbg);color:var(--warn)}
  .fail .pill{background:var(--failbg);color:var(--fail)} .unknown .pill{background:var(--unknownbg);color:var(--unknown)}
  .overall.ok{background:var(--okbg);color:var(--ok)} .overall.warn{background:var(--warnbg);color:var(--warn)}
  .overall.fail{background:var(--failbg);color:var(--fail)}
  footer { color:var(--muted); font-size:.8rem; margin-top:1.5rem; }
  button.act { font:inherit; font-size:.85rem; font-weight:600; padding:.35rem .8rem;
               border:1px solid var(--line); border-radius:7px; background:var(--card);
               color:var(--fg); cursor:pointer; }
  button.act:hover { border-color:var(--muted); }
  button.act[aria-pressed="true"] { background:var(--okbg); color:var(--ok); border-color:var(--ok); }
  pre.logout { margin:.7rem 0 0; padding:.6rem .7rem; max-height:24rem; overflow:auto;
               background:var(--unknownbg); border-radius:7px; font-size:.8rem; line-height:1.45;
               font-family:ui-monospace,SFMono-Regular,Menlo,monospace; white-space:pre-wrap;
               word-break:break-word; }
  nav.tabs { display:flex; flex-wrap:wrap; gap:.15rem; margin-bottom:1.4rem;
             border-bottom:1px solid var(--line); }
  nav.tabs a { padding:.5rem .8rem; margin-bottom:-1px; font-size:.9rem; font-weight:600;
               color:var(--muted); text-decoration:none; border-bottom:2px solid transparent; }
  nav.tabs a:hover { color:var(--fg); }
  nav.tabs a[aria-current="page"] { color:var(--fg); border-bottom-color:var(--fg); }
  @media (max-width:640px){ .row{flex-wrap:wrap} .name{min-width:0}
                            nav.tabs a{padding:.5rem .6rem} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>AltServer status</h1>
    <div class="meta">
      <span id="overall" class="overall">checking...</span>
       <span id="host"></span> - <span id="when"></span>
    </div>
  </header>
  <div id="checks"></div>

  <div class="card" id="histbox">
    <div class="row">
      <span class="name">History</span>
      <span class="summary" id="histstate">loading...</span>
    </div>
    <div id="hist"></div>
    <div class="histtip" id="histtip" hidden></div>
  </div>

  <div class="card" style="margin-top:1rem">
    <div class="row">
      <button class="act" id="logtoggle" aria-pressed="false">Watch refresh log</button>
      <span class="summary" id="logstate">Not watching. Start this, then trigger a refresh from AltStore.</span>
    </div>
    <pre class="logout" id="logout" hidden></pre>
  </div>

  <footer>
    Refreshes every 30s. Read-only - this page does not sign in or change anything.
    Raw JSON at <code>/api/status</code>.
  </footer>
</div>
<script>
function ago(s) {
  if (s < 90) { return s + 's ago'; }
  if (s < 5400) { return Math.round(s/60) + 'm ago'; }
  if (s < 172800) { return Math.round(s/3600) + 'h ago'; }
  return Math.round(s/86400) + 'd ago';
}

function histTime(sec) {
  const d = new Date(sec * 1000);
  const today = new Date();
  const sameDay = d.toDateString() === today.toDateString();
  const t = d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'});
  return sameDay ? t : (d.toLocaleDateString([], {month:'short', day:'numeric'}) + ' ' + t);
}

// Delegated, because the rows are re-rendered on every refresh.
function wireHistTip() {
  const box = document.getElementById('hist');
  const tip = document.getElementById('histtip');
  if (!box || !tip || box.dataset.wired) { return; }
  box.dataset.wired = '1';
  box.addEventListener('mouseover', function (ev) {
    const c = ev.target.closest('.hcell');
    if (!c) { return; }
    tip.innerHTML = '<b>' + esc(c.dataset.s) + '</b> ' + esc(histTime(Number(c.dataset.t)));
    tip.hidden = false;
    const cr = c.getBoundingClientRect();
    const br = document.getElementById('histbox').getBoundingClientRect();
    tip.style.left = (cr.left - br.left + cr.width / 2) + 'px';
    tip.style.top = (cr.top - br.top) + 'px';
  });
  box.addEventListener('mouseleave', function () { tip.hidden = true; });
}

async function loadHistory() {
  const box = document.getElementById('hist');
  const state = document.getElementById('histstate');
  try {
    const r = await fetch('/api/history', {cache:'no-store'});
    const d = await r.json();
    if (!d.available || !d.entries.length) {
      state.textContent = d.why || 'No history recorded yet.';
      box.innerHTML = '';
      return;
    }
    const entries = d.entries.slice(-120);
    const names = [];
    entries.forEach(e => Object.keys(e.checks || {}).forEach(n => {
      if (names.indexOf(n) < 0) { names.push(n); }
    }));
    const span = entries[entries.length-1].t - entries[0].t;
    // A recorder that stopped must not read as "all quiet" - that is the whole failure this
    // view exists to make visible.
    state.textContent = d.stale
      ? ('STALE - nothing recorded for ' + ago(d.age_seconds) + '. The altserver-web healthcheck '
         + 'is the writer; check that it is passing --record.')
      : (entries.length + ' runs over ' + ago(span).replace(' ago','') + ', newest '
         + ago(d.age_seconds));
    box.innerHTML = names.map(n => {
      let lastBad = null;
      entries.forEach(e => { const s = (e.checks||{})[n]; if (s && s !== 'ok') { lastBad = e.t; } });
      const cells = entries.map(e => {
        const s = (e.checks||{})[n] || 'unknown';
        // data-, not title=: the native tooltip takes a second to appear, cannot be styled, and
        // on 2px-wide bars gives no clue which one it belongs to.
        return '<i class="hcell ' + s + '" data-t="' + e.t + '" data-s="' + s + '"></i>';
      }).join('');
      const newest = entries[entries.length-1].t;
      const last = lastBad === null ? 'clean' : ('last not-ok ' + ago(newest - lastBad));
      return '<div class="hrow"><span class="hname">' + esc(n) + '</span>'
           + '<span class="hbar">' + cells + '</span>'
           + '<span class="hlast">' + last + '</span></div>';
    }).join('');
    wireHistTip();
  } catch (e) {
    state.textContent = 'Could not read the history.';
  }
}

let firstLoad = true;
async function load() {
  try {
    // First load asks for the full check (a human just opened this). The 30s refresh does
    // not: /api/status without full=1 probes anisette's STATIC route, because the bare root
    // provisions against Apple when the machine identity is missing, and an open tab would
    // otherwise trigger that 2880 times a day.
    const r = await fetch('/api/status' + (firstLoad ? '?full=1' : ''), {cache:'no-store'});
    firstLoad = false;
    const d = await r.json();
    const o = document.getElementById('overall');
    o.textContent = d.overall; o.className = 'overall ' + d.overall;
    document.getElementById('host').textContent = d.host || '';
    document.getElementById('when').textContent = new Date().toLocaleTimeString();
    loadHistory();
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
let logTimer = null;
const logBtn = document.getElementById('logtoggle');
const logOut = document.getElementById('logout');
const logState = document.getElementById('logstate');

async function pollLog(){
  try {
    const d = await (await fetch('/api/logs', {cache:'no-store'})).json();
    if (!d.available) {
      logState.textContent = d.why || 'No log available.';
      logOut.hidden = true;
      return;
    }
    const atBottom = logOut.scrollTop + logOut.clientHeight >= logOut.scrollHeight - 30;
    logOut.hidden = false;
    logOut.textContent = d.lines.length ? d.lines.join(String.fromCharCode(10)) : '(log is empty)';
    logState.textContent = d.lines.length + ' line(s) - watching';
    if (atBottom) { logOut.scrollTop = logOut.scrollHeight; }
  } catch (e) {
    logState.textContent = 'Could not read the log.';
  }
}

// Watching stops itself after this long. A refresh takes seconds, so anything beyond a few
// minutes means the tab was left open -- and an abandoned tab polling every 2s forever is a
// self-inflicted load on a box whose whole job is to sit quietly and refresh apps.
const LOG_WATCH_MS = 5 * 60 * 1000;
let logStopTimer = null;

function setWatching(on, reason){
  logBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
  logBtn.textContent = on ? 'Stop watching' : 'Watch refresh log';
  clearTimeout(logStopTimer); logStopTimer = null;
  if (on) {
    pollLog();
    logTimer = setInterval(pollLog, 2000);
    logStopTimer = setTimeout(function(){
      setWatching(false, 'Stopped automatically after 5 minutes. Click to watch again.');
    }, LOG_WATCH_MS);
  } else {
    clearInterval(logTimer); logTimer = null;
    logState.textContent = reason ||
      'Stopped. The log keeps being written; nothing is being polled.';
  }
}

logBtn.addEventListener('click', () => setWatching(logTimer === null));

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
    <div class="meta"><span id="when"></span></div>
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
      d.paired ? 'Paired OK' : ('Next: ' + (d.next||''));
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


INSTALL_PAGE = PAGE[:PAGE.index("<body>")].replace(
    "<title>AltServer status</title>", "<title>Install AltStore</title>") + """<body>
<div class="wrap">
  <header>
    <h1>Install AltStore</h1>
    <div class="meta"><span id="state">...</span></div>
  </header>

  <div class="card" id="warnbox">
    <div class="row"><span class="pill" style="background:var(--warnbg);color:var(--warn)">note</span>
    <span class="summary">Your Apple ID password is sent to this page over plain HTTP.</span></div>
    <div class="fix">Run this on loopback and reach it over an SSH tunnel unless you trust every
    device on your network. The password is passed to AltServer through the environment, never on
    a command line, and is not logged or echoed back.</div>
  </div>

  <form id="f" class="card" onsubmit="return start(event)">
    <label>Device UDID<br><input name="udid" id="udid" style="width:100%;padding:.45rem;margin:.3rem 0 .25rem"
      placeholder="detecting..." required></label>
    <div id="udidnote" class="meta" style="display:none;margin-bottom:.7rem"></div>
    <label>Apple ID<br><input name="apple_id" type="email" style="width:100%;padding:.45rem;margin:.3rem 0 .7rem" required></label>
    <label>Password<br><input name="password" type="password" style="width:100%;padding:.45rem;margin:.3rem 0 .7rem" required></label>
    <button type="submit" style="padding:.5rem 1rem;font-weight:600">Install AltStore</button>
    <div id="msg" style="display:none;margin-top:.7rem;padding:.55rem .7rem;border-radius:7px;
         background:var(--failbg);color:var(--fail);font-weight:600"></div>
  </form>

  <form id="tfa" class="card" style="display:none" onsubmit="return sendCode(event)">
    <div class="row"><span class="pill" style="background:var(--warnbg);color:var(--warn)">2FA</span>
    <span class="summary">Apple sent a six-digit code to your devices.</span></div>
    <input id="code" inputmode="numeric" pattern="[0-9]{6}" maxlength="6"
      style="width:9rem;padding:.45rem;margin:.6rem .5rem 0 0;font-size:1.1rem;letter-spacing:.2em" required>
    <button type="submit" style="padding:.5rem 1rem;font-weight:600">Submit code</button>
    <span id="tfamsg" class="meta"></span>
  </form>

  <div class="card" id="logbox" style="display:none">
    <div class="row"><span class="name">Progress</span></div>
    <pre id="log" class="detail" style="max-height:22rem;overflow:auto;white-space:pre-wrap"></pre>
  </div>

  <footer>Credentials and account data are filtered out of the log above before it is shown.</footer>
</div>
<script>
function esc(s){ return String(s).replace(/[&<>\"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c])); }
function showMsg(t){
  const el = document.getElementById('msg');
  el.textContent = t || '';
  el.style.display = t ? '' : 'none';
}
async function post(url, body){
  try {
    const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify(body)});
    return await r.json();
  } catch (e) {
    return {ok:false, error:'Could not reach the server: ' + e};
  }
}
async function start(e){
  e.preventDefault();
  showMsg('');
  const f = e.target;
  const d = await post('/api/install/start', {
    udid: f.udid.value, apple_id: f.apple_id.value, password: f.password.value});
  showMsg(d.ok ? '' : (d.error || 'Could not start the install.'));
  f.password.value = '';
  return false;
}
async function sendCode(e){
  e.preventDefault();
  const d = await post('/api/install/code', {code: document.getElementById('code').value});
  document.getElementById('tfamsg').textContent = d.ok ? '' : d.error;
  if (d.ok) document.getElementById('code').value = '';
  return false;
}
async function poll(){
  try{
    const d = await (await fetch('/api/install/status',{cache:'no-store'})).json();
    document.getElementById('state').textContent =
      d.state + (d.elapsed ? ' - ' + d.elapsed + 's' : '');
    document.getElementById('tfa').style.display = d.state === 'awaiting_2fa' ? '' : 'none';
    document.getElementById('logbox').style.display = d.lines.length ? '' : 'none';
    const log = document.getElementById('log');
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
    log.textContent = d.lines.join(String.fromCharCode(10));
    if (atBottom) log.scrollTop = log.scrollHeight;
    if (d.error) showMsg(d.error);
  }catch(e){}
}
// The pairing page already knows the UDID. Making someone copy it across by hand is a step that
// can only go wrong -- and an empty field produced the least helpful failure available: a
// validation error that used to render as barely-visible grey text.
async function fillUdid(){
  const el = document.getElementById('udid');
  const note = document.getElementById('udidnote');
  try{
    const d = await (await fetch('/api/pairing',{cache:'no-store'})).json();
    if (d.udids && d.udids.length){
      if (!el.value) el.value = d.udids[0];        // never clobber something typed by hand
      note.textContent = d.paired
        ? 'Detected and paired.'
        : 'Detected, but the pairing is not valid - the install will fail until it is.';
      note.style.display = '';
      if (!d.paired) note.innerHTML += ' <a href="/pairing">Fix pairing -></a>';
    } else {
      el.placeholder = 'no device detected';
      note.innerHTML = 'No device is connected. <a href="/pairing">Pair your iPhone first -></a>';
      note.style.display = '';
    }
  }catch(e){
    el.placeholder = 'enter the device UDID';
  }
}
fillUdid();
poll(); setInterval(poll, 1500);
</script>
</body>
</html>
"""


# Insert the tab bar now that all three pages exist. Doing it here rather than inside each literal
# keeps one definition of the tabs: PAIRING_PAGE and INSTALL_PAGE are built from PAGE's <head>, so
# a nav placed in PAGE's <body> would not reach them, and three hand-written copies would drift.
def _with_nav(page, active_href):
    marker = '<div class="wrap">'
    if page.count(marker) != 1:
        raise AssertionError(
            "expected exactly one %r in the page for %s, found %d -- the tab bar would be "
            "inserted in the wrong place or not at all" % (marker, active_href, page.count(marker)))
    return page.replace(marker, marker + "\n" + _nav(active_href), 1)


PAGE = _with_nav(PAGE, "/")
PAIRING_PAGE = _with_nav(PAIRING_PAGE, "/pairing")
INSTALL_PAGE = _with_nav(INSTALL_PAGE, "/install")


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
        elif path == "/install":
            self._send(200, INSTALL_PAGE, "text/html; charset=utf-8")
        elif path == "/api/install/status":
            self._send(200, json.dumps(installer.INSTALLER.snapshot()), "application/json")
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
        elif path == "/api/logs":
            # AltServer's own output, as redacted by docker/redact-log.py --tee. Read from the
            # volume both containers share, so this needs no Docker socket -- which would be
            # root-on-host for a service that already takes an Apple ID password over plain HTTP.
            #
            # Read-only and tail-bounded: a refresh is a few dozen lines, and the file itself is
            # capped by the filter.
            path_log = os.environ.get("ALTSERVER_LOG", "/data/altserver.log")
            try:
                size = os.path.getsize(path_log)
                with open(path_log, "r", encoding="utf-8", errors="replace") as f:
                    if size > 200_000:
                        f.seek(size - 200_000)
                        f.readline()
                    lines = f.read().splitlines()[-400:]
                # mtime and its age, because a FROZEN log is indistinguishable from a quiet
                # server otherwise. If the redaction filter dies or its tee is disabled, this
                # file simply stops growing while stdout keeps flowing, and the panel goes on
                # rendering the same last lines forever -- which looks exactly like an idle
                # server, during the incident when this is the first thing anyone opens.
                mtime = os.path.getmtime(path_log)
                age = max(0, int(time.time() - mtime))
                data = {"lines": lines, "available": True, "path": path_log,
                        "mtime": int(mtime), "age_seconds": age,
                        "stale": age > 3600}
            except FileNotFoundError:
                data = {"lines": [], "available": False, "path": path_log,
                        "why": "No log yet at %s. It appears once AltServer has written a line; "
                               "an image built before the log view was added will not create it."
                               % path_log}
            except Exception as exc:
                data = {"lines": [], "available": False, "path": path_log,
                        "why": "Could not read %s: %s" % (path_log, exc)}
            self._send(200, json.dumps(data), "application/json")
        elif path == "/api/history":
            # The question the 2026-09-22 outage could not answer. Every check result used to be
            # computed fresh and discarded, so the page could say "broken now" and never "broken
            # since Tuesday" -- the advertisement had been dead for somewhere between three and
            # nine days and nothing recorded which.
            try:
                entries = status_checks.read_history(limit=2000)
                data = {"entries": entries, "available": True,
                        "path": status_checks.HISTORY_PATH}
                if entries:
                    newest = max(e.get("t", 0) for e in entries)
                    data["age_seconds"] = max(0, int(time.time() - newest))
                    # The recorder is the healthcheck, every 5 minutes. Nothing for half an hour
                    # means the recorder itself stopped, which must not read as "all quiet".
                    data["stale"] = data["age_seconds"] > 1800
                else:
                    data["why"] = ("No history yet at %s. The altserver-web healthcheck writes "
                                   "it every 5 minutes; give it one interval, and check that "
                                   "the healthcheck passes --record."
                                   % status_checks.HISTORY_PATH)
            except Exception as exc:
                data = {"entries": [], "available": False, "why": str(exc),
                        "path": status_checks.HISTORY_PATH}
            self._send(200, json.dumps(data), "application/json")
        elif path == "/api/status":
            # ?full=1 means "a human is asking, once": do the real anisette fetch and judge on
            # everything. Without it this is treated as polling, which is what the 30s page
            # refresh and any uptime monitor actually are.
            full = self.path.find("full=1") >= 0
            try:
                data = status_checks.run_all(polling=not full)
            except Exception as exc:  # never let a check crash the dashboard
                data = {"overall": "fail", "host": "", "checks": [{
                    "name": "Status service", "state": "fail",
                    "summary": "A check raised an exception", "detail": str(exc), "fix": ""}]}
            # The BODY keeps the full verdict, because that is what a human reading the page
            # should see. The STATUS CODE reflects the server only, unless ?full=1.
            #
            # Both halves matter. 503 exists because an unconditional 200 is how a three-day
            # total outage went unnoticed. But judging the code on EVERY check meant the
            # endpoint the README tells you to monitor returned 503 every time the phone left
            # the house -- and a monitor that pages you for going out gets muted, which lands
            # back at no monitoring at all.
            try:
                data["server_overall"] = status_checks.verdict(data["checks"], server_only=True)
            except Exception:
                data["server_overall"] = data.get("overall")
            code_from = data["overall"] if full else data["server_overall"]
            self._send(status_checks.http_status(code_from),
                       json.dumps(data), "application/json")
        else:
            self._send(404, "not found\n", "text/plain; charset=utf-8")

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._send(400, json.dumps({"ok": False, "error": "bad request"}), "application/json")
            return

        if path == "/api/install/start":
            ok, err = installer.INSTALLER.start(
                body.get("udid", ""), body.get("apple_id", ""), body.get("password", ""),
                os.environ.get("ALTSERVER_ANISETTE_SERVER"))
            self._send(200, json.dumps({"ok": ok, "error": err}), "application/json")
        elif path == "/api/install/code":
            ok, err = installer.INSTALLER.submit_code(body.get("code", ""))
            self._send(200, json.dumps({"ok": ok, "error": err}), "application/json")
        else:
            self._send(404, json.dumps({"ok": False, "error": "not found"}), "application/json")

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
    # THREADING IS REQUIRED, not an optimisation. The status checks shell out to avahi-browse,
    # idevicepair and curl, which take seconds; a single-threaded server would block every other
    # request behind them. Three pages polling at 30s, 5s and 1.5s would then queue against each
    # other, which looks like the UI freezing when you switch pages.
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
