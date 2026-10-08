"""Tiny web demo: paste C code, watch SegSense crash it, triage it, and patch it live."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .agent import Config, SegSense
from .llm import TokenFactory

MAX_SOURCE_BYTES = 64 * 1024
EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
# Each fix compiles and runs untrusted code; keep concurrency low on a shared demo box.
_slots = threading.BoundedSemaphore(2)


def _examples() -> dict[str, str]:
    if not EXAMPLES_DIR.is_dir():
        return {}
    return {p.name: p.read_text() for p in sorted(EXAMPLES_DIR.glob("*.c"))}


def make_handler(patch_model: str, triage_model: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SegSense/0.1"

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif self.path == "/api/examples":
                self._send(200, "application/json", json.dumps(_examples()).encode())
            elif self.path == "/healthz":
                self._send(200, "text/plain", b"ok")
            else:
                self._send(404, "text/plain", b"not found")

        def do_POST(self) -> None:
            if self.path != "/api/fix":
                self._send(404, "text/plain", b"not found")
                return
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_SOURCE_BYTES + 4096:
                self._send(413, "text/plain", b"source too large")
                return
            try:
                body = json.loads(self.rfile.read(length))
                source = str(body["source"])
            except (ValueError, KeyError):
                self._send(400, "text/plain", b"expected JSON with a 'source' field")
                return
            if not _slots.acquire(blocking=False):
                self._send(429, "text/plain", b"busy: try again in a moment")
                return

            # Stream events as newline-delimited JSON so the page updates while the agent works.
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(kind: str, data: dict) -> None:
                self.wfile.write((json.dumps({"event": kind, **data}) + "\n").encode())
                self.wfile.flush()

            try:
                config = Config(
                    patch_model=patch_model,
                    triage_model=triage_model,
                    max_attempts=min(int(body.get("max_attempts", 3)), 5),
                    stdin=body.get("stdin") or None,
                    args=str(body.get("args", "")).split(),
                    timeout=5.0,
                )
                result = SegSense(TokenFactory(), config, on_event=emit).fix(source, body.get("filename", "program.c"))
                emit("done", {"success": result.success, "final": result.final, "diff": result.diff})
            except Exception as exc:
                emit("error", {"message": str(exc)})
                emit("done", {"success": False})
            finally:
                _slots.release()

        def _send(self, code: int, ctype: str, payload: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt: str, *args) -> None:
            print(f"{self.address_string()} {fmt % args}")

    return Handler


def serve(host: str, port: int, patch_model: str, triage_model: str | None) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(patch_model, triage_model))
    print(f"SegSense demo on http://{host}:{port}  (patch: {patch_model}, triage: {triage_model})")
    server.serve_forever()


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SegSense</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --text:#e6edf3; --dim:#8b949e;
          --green:#76b900; --red:#f85149; --amber:#d29922; --blue:#58a6ff; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 system-ui, sans-serif; }
  header { padding:20px 16px 8px; max-width:1200px; margin:auto; }
  h1 { margin:0; font-size:26px; } h1 span { color:var(--green); }
  header p { margin:4px 0 0; color:var(--dim); }
  main { display:grid; grid-template-columns:1fr 1fr; gap:16px; padding:16px; max-width:1200px; margin:auto; }
  @media (max-width: 860px) { main { grid-template-columns:1fr; } }
  .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:12px; min-width:0; }
  textarea, input, select { width:100%; background:var(--bg); color:var(--text); border:1px solid var(--line);
             border-radius:6px; padding:8px; font:13px/1.45 ui-monospace, Menlo, monospace; }
  textarea { height:420px; resize:vertical; }
  .row { display:flex; gap:8px; margin-top:8px; flex-wrap:wrap; }
  .row > * { flex:1; min-width:140px; }
  button { background:var(--green); color:#000; border:0; border-radius:6px; padding:10px; font-weight:700; cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  #log { font:13px/1.5 ui-monospace, Menlo, monospace; white-space:pre-wrap; overflow-wrap:anywhere; max-height:360px; overflow:auto; }
  #diff { font:13px/1.45 ui-monospace, Menlo, monospace; white-space:pre; overflow:auto; max-height:420px; margin:0; }
  .add { color:var(--green); } .del { color:var(--red); } .hunk { color:var(--blue); }
  .bad { color:var(--red); font-weight:700; } .ok { color:var(--green); font-weight:700; }
  .ai { color:var(--amber); } .dim { color:var(--dim); }
  h2 { font-size:14px; margin:0 0 8px; color:var(--dim); text-transform:uppercase; letter-spacing:.05em; }
</style>
</head>
<body>
<header>
  <h1>Seg<span>Sense</span></h1>
  <p>Paste C code with a memory bug. SegSense compiles it with AddressSanitizer + UBSan, catches the crash,
     and has NVIDIA Nemotron on Nebius Token Factory patch it until it runs clean.</p>
</header>
<main>
  <section class="panel">
    <h2>Program</h2>
    <select id="examples"><option value="">Load an example...</option></select>
    <textarea id="src" spellcheck="false" style="margin-top:8px"></textarea>
    <div class="row">
      <input id="args" placeholder="program args (optional)">
      <input id="stdin" placeholder="stdin (optional)">
    </div>
    <div class="row"><button id="go">Debug it</button></div>
  </section>
  <section class="panel">
    <h2>Agent log</h2>
    <div id="log" class="dim">Waiting for a program.</div>
    <h2 style="margin-top:16px">Patch</h2>
    <pre id="diff" class="dim">No patch yet.</pre>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let examples = {};
fetch('/api/examples').then(r => r.json()).then(ex => {
  examples = ex;
  for (const name of Object.keys(ex)) $('examples').add(new Option(name, name));
  const first = Object.keys(ex)[0];
  if (first) { $('examples').value = first; $('src').value = ex[first]; }
});
$('examples').onchange = e => { if (examples[e.target.value]) $('src').value = examples[e.target.value]; };

function line(html) { $('log').insertAdjacentHTML('beforeend', html + '\n'); $('log').scrollTop = 1e9; }
function tag(n) { return n === undefined ? '' : `<span class="dim">[${n === 0 ? 'orig' : 'try ' + n}]</span> `; }
function showDiff(diff) {
  $('diff').className = '';
  $('diff').innerHTML = diff.split('\n').map(l => {
    const cls = l.startsWith('@@') ? 'hunk' : (l.startsWith('+') && !l.startsWith('+++')) ? 'add'
              : (l.startsWith('-') && !l.startsWith('---')) ? 'del' : '';
    return `<span class="${cls}">${esc(l)}</span>`;
  }).join('\n');
}

function render(ev) {
  const t = tag(ev.attempt);
  switch (ev.event) {
    case 'start': line(`<span class="dim">compiler ${esc(ev.compiler)} · patch model ${esc(ev.patch_model)}</span>`); break;
    case 'build': line(t + 'compiling with -fsanitize=address,undefined'); break;
    case 'build_failed': line(t + '<span class="bad">compile error</span>\n' + esc(ev.output)); break;
    case 'crash': line(t + `<span class="bad">${esc(ev.headline)}</span> ${esc(ev.summary)}` +
                       (ev.frames.length ? '\n    ' + ev.frames.map(esc).join('\n    ') : '')); break;
    case 'pass': line(t + `<span class="ok">clean run</span> (exit ${ev.exit_code})`); break;
    case 'triage_start': line(`<span class="ai">${esc(ev.model)} triaging...</span>`); break;
    case 'triage': line(`<span class="ai">triage (${ev.seconds}s):</span> ${esc(ev.text)}`); break;
    case 'patch_start': line(t + `<span class="ai">${esc(ev.model)} writing a patch...</span>`); break;
    case 'patch': line(t + `<span class="ai">patch in ${ev.seconds}s</span>` +
                       (ev.root_cause ? `\n    root cause: ${esc(ev.root_cause)}` : '') +
                       (ev.fix ? `\n    fix: ${esc(ev.fix)}` : '')); break;
    case 'clean': line(`<span class="ok">${esc(ev.message)}</span>`); break;
    case 'fixed': line(`<span class="ok">Fixed after ${ev.attempt} attempt(s).</span>`); showDiff(ev.diff); break;
    case 'gave_up': line(`<span class="bad">Gave up after ${ev.attempts} attempts.</span>`); break;
    case 'error': line(`<span class="bad">${esc(ev.message)}</span>`); break;
  }
}

$('go').onclick = async () => {
  $('go').disabled = true; $('log').className = ''; $('log').textContent = '';
  $('diff').className = 'dim'; $('diff').textContent = 'Working...';
  try {
    const resp = await fetch('/api/fix', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ source: $('src').value, args: $('args').value, stdin: $('stdin').value,
                             filename: $('examples').value || 'program.c' }) });
    if (!resp.ok) { line(`<span class="bad">${esc(await resp.text())}</span>`); return; }
    const reader = resp.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const { value, done } = await reader.read(); if (done) break;
      buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf('\n')) >= 0) { const l = buf.slice(0, i); buf = buf.slice(i + 1); if (l) render(JSON.parse(l)); }
    }
    if ($('diff').textContent === 'Working...') $('diff').textContent = 'No patch.';
  } catch (e) { line(`<span class="bad">${esc(e)}</span>`); }
  finally { $('go').disabled = false; }
};
</script>
</body>
</html>
"""
