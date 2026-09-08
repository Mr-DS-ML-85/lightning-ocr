"""
lightning-ocr · Modern 2026 single-file UI
Glassmorphism + neon accents, real-time log stream, drag-and-drop, batch mode,
job history panel, hardware status badges.
"""

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>⚡ lightning-ocr</title>
<style>
/* ── Reset & tokens ─────────────────────────────────────────────── */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:        #05060f;
  --surface:   rgba(12,16,40,0.82);
  --surface2:  rgba(18,24,56,0.75);
  --border:    rgba(80,120,255,0.18);
  --accent1:   #4f8eff;
  --accent2:   #a259ff;
  --accent3:   #00e5c8;
  --text:      #dde8ff;
  --muted:     #7a90c4;
  --danger:    #ff4f6e;
  --success:   #00e5a0;
  --warn:      #ffba00;
  --radius:    16px;
  --radius-sm: 10px;
  --blur:      18px;
  --font:      'Inter','Segoe UI',system-ui,sans-serif;
  --mono:      'JetBrains Mono','Fira Code',monospace;
  --shadow:    0 8px 40px rgba(0,0,0,.5);
}
html{height:100%;scroll-behavior:smooth}
body{
  min-height:100vh;background:var(--bg);color:var(--text);
  font-family:var(--font);font-size:14px;line-height:1.6;
  background-image:
    radial-gradient(ellipse 80% 60% at 20% -10%, rgba(79,142,255,.12) 0%, transparent 70%),
    radial-gradient(ellipse 60% 50% at 80% 110%, rgba(162,89,255,.10) 0%, transparent 70%);
}

/* ── Scrollbar ──────────────────────────────────────────────────── */
::-webkit-scrollbar{width:6px;height:6px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:rgba(80,120,255,.35);border-radius:99px}

/* ── Layout ─────────────────────────────────────────────────────── */
.app-shell{display:grid;grid-template-rows:auto 1fr;min-height:100vh;gap:0}
.topbar{
  display:flex;align-items:center;gap:12px;
  padding:14px 28px;
  background:rgba(5,6,15,.7);
  border-bottom:1px solid var(--border);
  backdrop-filter:blur(var(--blur));
  position:sticky;top:0;z-index:100;
}
.logo{display:flex;align-items:center;gap:10px;font-size:20px;font-weight:800;letter-spacing:-.5px}
.logo span.bolt{font-size:26px;filter:drop-shadow(0 0 8px #ffba00)}
.logo span.name{background:linear-gradient(135deg,var(--accent1),var(--accent2));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent}
.badge{
  padding:3px 10px;border-radius:99px;font-size:11px;font-weight:700;
  border:1px solid;letter-spacing:.5px;text-transform:uppercase;
}
.badge-cuda{color:#76ff6e;border-color:#76ff6e33;background:#76ff6e0d}
.badge-sycl{color:#00d4ff;border-color:#00d4ff33;background:#00d4ff0d}
.badge-cpu {color:var(--warn);border-color:#ffba0033;background:#ffba000d}
.badge-ok  {color:var(--success);border-color:#00e5a033;background:#00e5a00d}
.badge-err {color:var(--danger);border-color:#ff4f6e33;background:#ff4f6e0d}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:10px}
.icon-btn{
  background:none;border:1px solid var(--border);border-radius:var(--radius-sm);
  color:var(--muted);padding:6px 10px;cursor:pointer;font-size:18px;line-height:1;
  transition:border-color .2s,color .2s;
}
.icon-btn:hover{border-color:var(--accent1);color:var(--accent1)}

.main-grid{
  display:grid;
  grid-template-columns:380px 1fr 320px;
  gap:0;
  max-height:calc(100vh - 57px);
  overflow:hidden;
}
@media(max-width:1200px){
  .main-grid{grid-template-columns:1fr;max-height:none;overflow:visible}
  .panel{max-height:none!important}
}

.panel{
  height:calc(100vh - 57px);
  overflow-y:auto;
  padding:20px;
  border-right:1px solid var(--border);
}
.panel:last-child{border-right:none;border-left:1px solid var(--border)}
.panel-title{
  font-size:11px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;
  color:var(--muted);margin-bottom:14px;display:flex;align-items:center;gap:8px;
}
.panel-title::after{content:'';flex:1;height:1px;background:var(--border)}

/* ── Cards / Glass ──────────────────────────────────────────────── */
.glass{
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:var(--radius);
  backdrop-filter:blur(var(--blur));
  box-shadow:var(--shadow);
}
.glass2{
  background:var(--surface2);
  border:1px solid var(--border);
  border-radius:var(--radius-sm);
}

/* ── Form elements ──────────────────────────────────────────────── */
label.field-label{
  display:block;font-size:12px;font-weight:600;color:var(--muted);
  margin:16px 0 6px;letter-spacing:.4px;
}
select,input[type=text],textarea{
  width:100%;padding:10px 13px;
  background:rgba(10,14,36,.8);
  border:1px solid var(--border);
  border-radius:var(--radius-sm);
  color:var(--text);font-family:var(--font);font-size:13px;
  transition:border-color .2s,box-shadow .2s;
  outline:none;appearance:none;
}
select:focus,input:focus,textarea:focus{
  border-color:var(--accent1);
  box-shadow:0 0 0 3px rgba(79,142,255,.12);
}
textarea{min-height:80px;resize:vertical;font-family:var(--mono)}

/* ── Mode pills ─────────────────────────────────────────────────── */
.mode-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.mode-pill{
  padding:8px 10px;border-radius:var(--radius-sm);
  border:1px solid var(--border);
  background:rgba(10,14,36,.6);
  color:var(--muted);font-size:12px;font-weight:600;
  cursor:pointer;text-align:center;transition:all .2s;
  user-select:none;
}
.mode-pill:hover{border-color:var(--accent1);color:var(--accent1)}
.mode-pill.active{
  background:linear-gradient(135deg,rgba(79,142,255,.2),rgba(162,89,255,.15));
  border-color:var(--accent1);color:#fff;
  box-shadow:0 0 12px rgba(79,142,255,.2);
}

/* ── Drop zone ──────────────────────────────────────────────────── */
.dropzone{
  border:2px dashed var(--border);border-radius:var(--radius);
  padding:28px 20px;text-align:center;cursor:pointer;
  transition:all .25s;color:var(--muted);
  background:rgba(10,14,36,.4);
  position:relative;
}
.dropzone:hover,.dropzone.drag-over{
  border-color:var(--accent1);
  background:rgba(79,142,255,.06);
  color:var(--text);
}
.dropzone input[type=file]{
  position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%;
}
.dropzone .drop-icon{font-size:32px;margin-bottom:8px;display:block}
.thumb-row{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.thumb{width:56px;height:56px;object-fit:cover;border-radius:8px;border:1px solid var(--border)}

/* ── Run button ─────────────────────────────────────────────────── */
.run-btn{
  width:100%;margin-top:18px;padding:13px;
  background:linear-gradient(135deg,var(--accent1),var(--accent2));
  border:none;border-radius:var(--radius);
  color:#fff;font-size:15px;font-weight:800;
  cursor:pointer;letter-spacing:.3px;
  transition:opacity .2s,transform .15s,box-shadow .2s;
  box-shadow:0 4px 20px rgba(79,142,255,.35);
}
.run-btn:hover{opacity:.92;transform:translateY(-1px);box-shadow:0 6px 28px rgba(79,142,255,.45)}
.run-btn:active{transform:translateY(0)}
.run-btn:disabled{opacity:.45;cursor:not-allowed;transform:none}

/* ── Progress bar ───────────────────────────────────────────────── */
.progress-wrap{height:3px;background:rgba(255,255,255,.07);border-radius:99px;margin-top:12px;overflow:hidden}
.progress-bar{height:100%;width:0;background:linear-gradient(90deg,var(--accent1),var(--accent2));
  transition:width .3s;border-radius:99px}

/* ── Status chip ────────────────────────────────────────────────── */
.status-row{display:flex;align-items:center;gap:8px;margin-top:10px;font-size:12px;color:var(--muted)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--muted);flex-shrink:0}
.dot.running{background:var(--accent1);animation:pulse 1s infinite}
.dot.ok{background:var(--success)}
.dot.err{background:var(--danger)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}

/* ── Result panel ───────────────────────────────────────────────── */
.result-toolbar{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.result-toolbar button{
  padding:6px 14px;border-radius:var(--radius-sm);
  background:rgba(79,142,255,.1);border:1px solid var(--border);
  color:var(--text);font-size:12px;font-weight:600;cursor:pointer;
  transition:all .2s;
}
.result-toolbar button:hover{border-color:var(--accent1);color:var(--accent1)}
.result-area{
  font-family:var(--mono);font-size:13px;line-height:1.7;
  white-space:pre-wrap;word-break:break-word;
  background:rgba(5,6,15,.6);
  border:1px solid var(--border);
  border-radius:var(--radius);
  padding:16px;min-height:300px;max-height:calc(100vh - 240px);
  overflow-y:auto;color:var(--text);
}
.result-meta{
  margin-top:10px;font-size:11px;color:var(--muted);
  display:flex;gap:14px;flex-wrap:wrap;align-items:center;
}
.result-meta span{display:flex;align-items:center;gap:4px}

/* ── Log stream ─────────────────────────────────────────────────── */
.log-area{
  font-family:var(--mono);font-size:11.5px;line-height:1.6;
  background:rgba(5,6,15,.7);border:1px solid var(--border);
  border-radius:var(--radius);padding:12px;
  height:220px;overflow-y:auto;color:#7aa0d4;
}
.log-area .ll-info{color:#7aa0d4}
.log-area .ll-ok  {color:var(--success)}
.log-area .ll-warn{color:var(--warn)}
.log-area .ll-err {color:var(--danger)}

/* ── History panel ──────────────────────────────────────────────── */
.history-item{
  padding:10px 12px;border-radius:var(--radius-sm);
  border:1px solid var(--border);background:rgba(10,14,36,.5);
  margin-bottom:8px;cursor:pointer;transition:all .2s;
}
.history-item:hover{border-color:var(--accent1);background:rgba(79,142,255,.06)}
.history-item .hi-file{font-weight:700;font-size:13px;color:var(--text)}
.history-item .hi-meta{font-size:11px;color:var(--muted);margin-top:3px}
.history-item .hi-snippet{
  font-size:12px;color:var(--muted);margin-top:6px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}

/* ── Tabs ───────────────────────────────────────────────────────── */
.tabs{display:flex;gap:2px;margin-bottom:14px;
  background:rgba(10,14,36,.5);border-radius:var(--radius-sm);padding:3px;}
.tab{flex:1;padding:7px;text-align:center;border-radius:8px;
  font-size:12px;font-weight:700;cursor:pointer;color:var(--muted);transition:all .2s}
.tab.active{background:linear-gradient(135deg,rgba(79,142,255,.25),rgba(162,89,255,.2));
  color:#fff;box-shadow:0 2px 10px rgba(79,142,255,.2)}

/* ── Toasts ─────────────────────────────────────────────────────── */
#toast-root{position:fixed;bottom:24px;right:24px;z-index:9999;display:flex;flex-direction:column-reverse;gap:8px}
.toast{
  padding:12px 18px;border-radius:var(--radius-sm);
  background:rgba(18,24,56,.95);border:1px solid var(--border);
  backdrop-filter:blur(var(--blur));font-size:13px;
  box-shadow:var(--shadow);animation:slideIn .25s ease;
  display:flex;align-items:center;gap:8px;max-width:360px;
}
.toast.ok {border-color:var(--success);color:var(--success)}
.toast.err{border-color:var(--danger);color:var(--danger)}
.toast.warn{border-color:var(--warn);color:var(--warn)}
@keyframes slideIn{from{opacity:0;transform:translateX(20px)}to{opacity:1;transform:none}}

/* ── Backends health mini-cards ─────────────────────────────────── */
.backend-card{
  display:flex;align-items:center;gap:10px;
  padding:9px 12px;border-radius:var(--radius-sm);
  border:1px solid var(--border);background:rgba(10,14,36,.5);
  margin-bottom:7px;
}
.backend-card .bc-icon{font-size:18px}
.backend-card .bc-info{flex:1;min-width:0}
.backend-card .bc-name{font-weight:700;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.backend-card .bc-url{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ── API reference ──────────────────────────────────────────────── */
.endpoint-row{
  display:flex;align-items:center;gap:8px;padding:7px 10px;
  border-radius:var(--radius-sm);border:1px solid var(--border);
  background:rgba(10,14,36,.4);margin-bottom:6px;font-family:var(--mono);font-size:12px;
  cursor:pointer;transition:border-color .2s;
}
.endpoint-row:hover{border-color:var(--accent1)}
.method{padding:3px 8px;border-radius:6px;font-weight:800;font-size:11px}
.method.GET {background:#00e5a011;color:var(--success)}
.method.POST{background:#4f8eff11;color:var(--accent1)}
.hw-mode-toggle{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.hw-btn{
  flex:1;min-width:80px;padding:8px 6px;border-radius:var(--radius-sm);
  border:1px solid var(--border);background:rgba(10,14,36,.6);
  color:var(--muted);font-size:12px;font-weight:700;cursor:pointer;
  text-align:center;transition:all .2s;
}
.hw-btn:hover{border-color:var(--accent3);color:var(--accent3)}
.hw-btn.active{
  background:rgba(0,229,200,.1);border-color:var(--accent3);color:var(--accent3);
}
</style>
</head>
<body>

<div id="toast-root"></div>

<div class="app-shell">
<!-- ── Top bar ──────────────────────────────────────────────────── -->
<header class="topbar">
  <div class="logo">
    <span class="bolt">⚡</span>
    <span class="name">lightning-ocr</span>
  </div>
  <span class="badge badge-ok" id="hw-badge">CPU</span>
  <div class="topbar-right">
    <a href="/docs" target="_blank"><button class="icon-btn" title="API Docs">📄</button></a>
    <a href="/redoc" target="_blank"><button class="icon-btn" title="ReDoc">📘</button></a>
    <button class="icon-btn" title="Refresh health" onclick="loadHealth()">🔄</button>
  </div>
</header>

<!-- ── Main grid ─────────────────────────────────────────────────── -->
<div class="main-grid">

<!-- ╔══════════════════════════════════════════════╗ -->
<!-- ║  LEFT: Controls                              ║ -->
<!-- ╚══════════════════════════════════════════════╝ -->
<aside class="panel">
  <div class="panel-title">⚙ Controls</div>

  <!-- Backend selector -->
  <label class="field-label">Backend</label>
  <select id="backend-sel"></select>

  <!-- HW mode quick-switch -->
  <label class="field-label">Hardware Mode</label>
  <div class="hw-mode-toggle" id="hw-toggle">
    <div class="hw-btn active" data-mode="auto">🔀 Auto</div>
    <div class="hw-btn" data-mode="cuda">🟢 CUDA</div>
    <div class="hw-btn" data-mode="sycl">🔵 SYCL/Intel</div>
    <div class="hw-btn" data-mode="cpu">🟡 CPU</div>
  </div>

  <!-- OCR mode pills -->
  <label class="field-label">OCR Mode</label>
  <div class="mode-grid" id="mode-grid">
    <div class="mode-pill active" data-mode="document">📄 Document→MD</div>
    <div class="mode-pill" data-mode="ocr">🔍 General OCR</div>
    <div class="mode-pill" data-mode="free">📝 Plain Text</div>
    <div class="mode-pill" data-mode="figure">📊 Chart/Figure</div>
    <div class="mode-pill" data-mode="describe">🖼 Describe</div>
    <div class="mode-pill" data-mode="find">📍 Find Term</div>
    <div class="mode-pill" data-mode="freeform">✏️ Custom</div>
  </div>

  <!-- Find term (shown for "find" mode) -->
  <div id="find-row" style="display:none">
    <label class="field-label">Find Term</label>
    <input type="text" id="find-term" placeholder="e.g. Total Amount"/>
  </div>

  <!-- Custom prompt (shown for "freeform") -->
  <div id="prompt-row" style="display:none">
    <label class="field-label">Custom Prompt</label>
    <textarea id="custom-prompt" placeholder="Describe what you want extracted…"></textarea>
  </div>

  <!-- Fallback toggle -->
  <label class="field-label" style="display:flex;align-items:center;gap:8px;cursor:pointer">
    <input type="checkbox" id="auto-fallback" checked style="width:auto"/>
    Auto-fallback (try Tesseract on failure)
  </label>

  <!-- Drop zone -->
  <label class="field-label">Image / PDF</label>
  <div class="dropzone" id="dropzone">
    <input type="file" id="file-input" accept="image/*,.pdf" multiple/>
    <span class="drop-icon">📂</span>
    <div>Drop files here or click to browse</div>
    <div style="font-size:11px;margin-top:4px;color:var(--muted)">PNG, JPG, WEBP, PDF • multiple files for batch</div>
    <div class="thumb-row" id="thumbs"></div>
  </div>

  <button class="run-btn" id="run-btn">⚡ Run OCR</button>
  <div class="progress-wrap"><div class="progress-bar" id="prog"></div></div>
  <div class="status-row">
    <div class="dot" id="status-dot"></div>
    <span id="status-text">Ready.</span>
  </div>
</aside>

<!-- ╔══════════════════════════════════════════════╗ -->
<!-- ║  CENTER: Result                              ║ -->
<!-- ╚══════════════════════════════════════════════╝ -->
<main class="panel" style="border-right:1px solid var(--border)">
  <div class="panel-title">📋 Result</div>

  <div class="result-toolbar">
    <button onclick="copyResult()">📋 Copy</button>
    <button onclick="downloadResult('txt')">⬇ .txt</button>
    <button onclick="downloadResult('md')">⬇ .md</button>
    <button onclick="downloadResult('json')">⬇ .json</button>
    <button onclick="clearResult()">🗑 Clear</button>
  </div>

  <div class="result-area" id="result-area">Select a backend, drop an image, and hit ⚡ Run OCR.</div>

  <div class="result-meta" id="result-meta" style="display:none">
    <span>🏭 <span id="meta-backend">—</span></span>
    <span>⏱ <span id="meta-ms">—</span>ms</span>
    <span>📦 <span id="meta-mode">—</span></span>
    <span id="meta-fallback" style="color:var(--warn);display:none">⚠ fallback used</span>
  </div>

  <!-- Log stream -->
  <div style="margin-top:18px">
    <div class="panel-title" style="margin-bottom:8px">🖥 Activity Log</div>
    <div class="log-area" id="log-area"></div>
  </div>
</main>

<!-- ╔══════════════════════════════════════════════╗ -->
<!-- ║  RIGHT: Backends + History + API             ║ -->
<!-- ╚══════════════════════════════════════════════╝ -->
<aside class="panel">
  <div class="tabs" id="right-tabs">
    <div class="tab active" data-tab="backends">Backends</div>
    <div class="tab" data-tab="history">History</div>
    <div class="tab" data-tab="api">API</div>
  </div>

  <!-- Backends tab -->
  <div id="tab-backends">
    <div class="panel-title">🏭 OCR Backends</div>
    <div id="backends-list"></div>
  </div>

  <!-- History tab -->
  <div id="tab-history" style="display:none">
    <div class="panel-title">🕐 Job History
      <button onclick="loadHistory()" style="margin-left:auto;background:none;border:none;color:var(--muted);cursor:pointer;font-size:14px">🔄</button>
    </div>
    <div id="history-list"><div style="color:var(--muted);text-align:center;padding:20px">Loading…</div></div>
  </div>

  <!-- API tab -->
  <div id="tab-api" style="display:none">
    <div class="panel-title">📡 API Reference</div>
    <div onclick="copyText('/api/ocr')" class="endpoint-row">
      <span class="method POST">POST</span><span>/api/ocr</span>
    </div>
    <div onclick="copyText('/api/models')" class="endpoint-row">
      <span class="method GET">GET</span><span>/api/models</span>
    </div>
    <div onclick="copyText('/api/health')" class="endpoint-row">
      <span class="method GET">GET</span><span>/api/health</span>
    </div>
    <div onclick="copyText('/api/history')" class="endpoint-row">
      <span class="method GET">GET</span><span>/api/history</span>
    </div>
    <div onclick="copyText('/mcp')" class="endpoint-row">
      <span class="method POST">POST</span><span>/mcp (MCP)</span>
    </div>
    <div onclick="window.open('/docs','_blank')" class="endpoint-row">
      <span class="method GET">GET</span><span>/docs (Swagger)</span>
    </div>
    <div style="margin-top:14px;font-size:12px;color:var(--muted)">
      <strong style="color:var(--text)">Quick curl:</strong>
      <pre style="margin-top:6px;background:rgba(5,6,15,.7);border:1px solid var(--border);
        border-radius:8px;padding:10px;font-size:11px;overflow-x:auto;color:#a0c0ff">curl -X POST http://localhost:8000/api/ocr \
  -F "backend_id=glm-ocr" \
  -F "mode=document" \
  -F "file=@sample.png"</pre>
    </div>
    <div style="margin-top:14px;font-size:12px;color:var(--muted)">
      <strong style="color:var(--text)">MCP (AI agents):</strong>
      <pre style="margin-top:6px;background:rgba(5,6,15,.7);border:1px solid var(--border);
        border-radius:8px;padding:10px;font-size:11px;overflow-x:auto;color:#a0c0ff">curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,
       "method":"tools/list","params":{}}'</pre>
    </div>
  </div>
</aside>

</div><!-- end main-grid -->
</div><!-- end app-shell -->

<script>
// ── State ───────────────────────────────────────────────────────────────────
const state = {
  mode: 'document',
  hwMode: 'auto',
  files: [],
  currentResult: null,
  activeTab: 'backends',
};

// ── Utilities ────────────────────────────────────────────────────────────────
function toast(msg, type='ok', dur=3000){
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = (type==='ok'?'✓ ':type==='err'?'✗ ':'⚠ ') + msg;
  document.getElementById('toast-root').appendChild(el);
  setTimeout(()=>el.remove(), dur);
}

function log(msg, lvl='info'){
  const el = document.getElementById('log-area');
  const ts = new Date().toLocaleTimeString();
  const line = document.createElement('div');
  line.className = `ll-${lvl}`;
  line.textContent = `[${ts}] ${msg}`;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

function setStatus(txt, state_='idle'){
  document.getElementById('status-text').textContent = txt;
  const dot = document.getElementById('status-dot');
  dot.className = 'dot ' + (state_==='running'?'running':state_==='ok'?'ok':state_==='err'?'err':'');
}

function setProgress(pct){
  document.getElementById('prog').style.width = pct + '%';
}

function copyText(t){ navigator.clipboard.writeText(t).then(()=>toast('Copied: '+t)) }

// ── Mode pills ───────────────────────────────────────────────────────────────
document.getElementById('mode-grid').addEventListener('click', e=>{
  const pill = e.target.closest('.mode-pill');
  if(!pill) return;
  document.querySelectorAll('.mode-pill').forEach(p=>p.classList.remove('active'));
  pill.classList.add('active');
  state.mode = pill.dataset.mode;
  document.getElementById('find-row').style.display   = state.mode==='find'    ?'block':'none';
  document.getElementById('prompt-row').style.display = state.mode==='freeform'?'block':'none';
});

// ── HW mode toggle ───────────────────────────────────────────────────────────
document.getElementById('hw-toggle').addEventListener('click', e=>{
  const btn = e.target.closest('.hw-btn');
  if(!btn) return;
  document.querySelectorAll('.hw-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  state.hwMode = btn.dataset.mode;
  log(`Hardware mode set to: ${state.hwMode}`, 'info');
  toast(`HW mode → ${state.hwMode}`,'ok',1500);
});

// ── Tabs ─────────────────────────────────────────────────────────────────────
document.getElementById('right-tabs').addEventListener('click', e=>{
  const tab = e.target.closest('.tab');
  if(!tab) return;
  const id = tab.dataset.tab;
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  tab.classList.add('active');
  ['backends','history','api'].forEach(t=>{
    document.getElementById(`tab-${t}`).style.display = t===id?'block':'none';
  });
  state.activeTab = id;
  if(id==='history') loadHistory();
});

// ── Drop zone ─────────────────────────────────────────────────────────────────
const dz = document.getElementById('dropzone');
const fi = document.getElementById('file-input');

dz.addEventListener('dragover', e=>{ e.preventDefault(); dz.classList.add('drag-over'); });
dz.addEventListener('dragleave', ()=>dz.classList.remove('drag-over'));
dz.addEventListener('drop', e=>{
  e.preventDefault(); dz.classList.remove('drag-over');
  handleFiles([...e.dataTransfer.files]);
});
fi.addEventListener('change', ()=>handleFiles([...fi.files]));

function handleFiles(files){
  state.files = files;
  const row = document.getElementById('thumbs');
  row.innerHTML = '';
  files.forEach(f=>{
    if(f.type.startsWith('image/')){
      const img = document.createElement('img');
      img.className = 'thumb';
      img.src = URL.createObjectURL(f);
      row.appendChild(img);
    } else {
      const tag = document.createElement('div');
      tag.className = 'thumb';
      tag.style.cssText = 'display:flex;align-items:center;justify-content:center;font-size:22px';
      tag.textContent = '📄';
      row.appendChild(tag);
    }
  });
  log(`${files.length} file(s) selected: ${files.map(f=>f.name).join(', ')}`, 'info');
}

// ── Run OCR ──────────────────────────────────────────────────────────────────
document.getElementById('run-btn').addEventListener('click', runOCR);

async function runOCR(){
  if(state.files.length === 0){ toast('Please select at least one file.','warn'); return; }
  const btn = document.getElementById('run-btn');
  btn.disabled = true;
  setStatus('Running…', 'running');
  setProgress(5);

  const backendId = document.getElementById('backend-sel').value;
  const mode      = state.mode;
  const fallback  = document.getElementById('auto-fallback').checked;

  // Batch mode
  const results = [];
  for(let i=0; i<state.files.length; i++){
    const f = state.files[i];
    setProgress(10 + (i/state.files.length)*80);
    setStatus(`Processing ${i+1}/${state.files.length}: ${f.name}`, 'running');
    log(`▶ [${i+1}/${state.files.length}] ${f.name} via ${backendId}…`, 'info');

    const fd = new FormData();
    fd.append('file', f);
    fd.append('backend_id', backendId);
    fd.append('mode', mode);
    fd.append('find_term', document.getElementById('find-term').value);
    fd.append('custom_prompt', document.getElementById('custom-prompt').value);
    fd.append('auto_fallback', fallback ? 'true':'false');

    try{
      const r = await fetch('/api/ocr', {method:'POST', body:fd});
      const data = await r.json();
      if(!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      results.push({file:f.name, ...data});
      log(`✓ ${f.name}: ${data.text.slice(0,80)}…  (${data.duration_ms}ms, ${data.backend.id})`, 'ok');
    } catch(e){
      results.push({file:f.name, error:String(e)});
      log(`✗ ${f.name}: ${e}`, 'err');
      toast(`Failed: ${f.name}`, 'err');
    }
  }

  setProgress(100);
  const ok = results.filter(r=>!r.error);

  if(ok.length > 0){
    state.currentResult = results;
    const text = ok.map((r,i)=>{
      const hdr = state.files.length > 1 ? `\n\n## File ${i+1}: ${r.file}\n\n` : '';
      return hdr + r.text;
    }).join('\n\n---\n\n');

    document.getElementById('result-area').textContent = text;
    document.getElementById('result-meta').style.display = 'flex';
    document.getElementById('meta-backend').textContent = ok[0].backend?.id || '—';
    document.getElementById('meta-ms').textContent      = ok.reduce((s,r)=>s+(r.duration_ms||0),0);
    document.getElementById('meta-mode').textContent    = mode;
    const fallbackUsed = ok.some(r=>r.fallback);
    document.getElementById('meta-fallback').style.display = fallbackUsed ? 'flex':'none';

    setStatus(`Done — ${ok.length}/${results.length} succeeded`, 'ok');
    toast(`${ok.length} file(s) processed ✓`, 'ok');
    if(state.activeTab === 'history') loadHistory();
  } else {
    setStatus('All failed.', 'err');
    toast('All backends failed. Check logs.', 'err');
  }

  btn.disabled = false;
  setTimeout(()=>setProgress(0), 1200);
}

// ── Copy / download ──────────────────────────────────────────────────────────
function copyResult(){
  const t = document.getElementById('result-area').textContent;
  navigator.clipboard.writeText(t).then(()=>toast('Copied!'));
}

function downloadResult(ext){
  const t = document.getElementById('result-area').textContent;
  const payload = ext==='json' ? JSON.stringify(state.currentResult, null, 2) : t;
  const mime = ext==='json' ? 'application/json' : 'text/plain';
  const blob = new Blob([payload], {type: mime});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `lightning-ocr-result.${ext}`;
  a.click();
  toast(`Downloaded as .${ext}`);
}

function clearResult(){
  document.getElementById('result-area').textContent='Select a backend, drop an image, and hit ⚡ Run OCR.';
  document.getElementById('result-meta').style.display='none';
}

// ── Load backends ────────────────────────────────────────────────────────────
async function loadBackends(){
  try{
    const r = await fetch('/api/models');
    const data = await r.json();
    const sel = document.getElementById('backend-sel');
    sel.innerHTML = '';
    data.backends.forEach(b=>{
      const opt = document.createElement('option');
      opt.value = b.id;
      opt.textContent = `${b.label}`;
      sel.appendChild(opt);
    });
    log(`Loaded ${data.backends.length} backend(s)`, 'ok');
  } catch(e){ log('Failed to load backends: '+e,'err'); }
}

// ── Health ────────────────────────────────────────────────────────────────────
let healthInterval = null;

async function loadHealth(){
  try{
    const r = await fetch('/api/health');
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const list = document.getElementById('backends-list');
    list.innerHTML = '';
    const icons = {openai_compatible:'🤖', deepseek_webui:'🧠', tesseract:'📝', easyocr:'👁', glm_engine:'🧬'};
    data.backends.forEach(b=>{
      const ok = b.status==='ok';
      list.innerHTML += `
        <div class="backend-card">
          <span class="bc-icon">${icons[b.kind]||'🔌'}</span>
          <div class="bc-info">
            <div class="bc-name">${b.label}</div>
            <div class="bc-url">${b.kind}</div>
          </div>
          <span class="badge ${ok?'badge-ok':'badge-err'}">${b.status}</span>
        </div>`;
    });

    const anyGpu = data.backends.some(b=>b.detail?.gpu);
    const hwBadge = document.getElementById('hw-badge');
    // Update based on current HW mode UI
    const hwMode = state.hwMode;
    if(hwMode==='cuda'){hwBadge.textContent='CUDA';hwBadge.className='badge badge-cuda'}
    else if(hwMode==='sycl'){hwBadge.textContent='SYCL';hwBadge.className='badge badge-sycl'}
    else {hwBadge.textContent='CPU';hwBadge.className='badge badge-cpu'}
  } catch(e){ log('Health check failed: '+e,'err'); }
}

function startHealthInterval(){
  if(healthInterval) clearInterval(healthInterval);
  healthInterval = setInterval(loadHealth, 30000);
}

// ── History ───────────────────────────────────────────────────────────────────
async function loadHistory(){
  try{
    const r = await fetch('/api/history?limit=30');
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    const list = document.getElementById('history-list');
    if(!data.jobs || !data.jobs.length){
      list.innerHTML='<div style="color:var(--muted);text-align:center;padding:20px">No jobs yet.</div>';
      return;
    }
    list.innerHTML = data.jobs.map(j=>`
      <div class="history-item" onclick="loadJobResult(${j.id})">
        <div class="hi-file">${j.filename||'(no file)'}</div>
        <div class="hi-meta">${j.backend_id} · ${j.mode} · ${j.duration_ms}ms · ${new Date(j.created_at*1000).toLocaleString()}</div>
        <div class="hi-snippet">${(j.text_result||j.error||'').slice(0,100)}</div>
      </div>`).join('');
  } catch(e){ log('History load failed: '+e,'err'); }
}

async function loadJobResult(id){
  try{
    const r = await fetch(`/api/history/${id}`);
    if(!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    document.getElementById('result-area').textContent = j.text_result || j.error || '(empty)';
    document.getElementById('result-meta').style.display='flex';
    document.getElementById('meta-backend').textContent = j.backend_id;
    document.getElementById('meta-ms').textContent = j.duration_ms;
    document.getElementById('meta-mode').textContent = j.mode;
    toast(`Loaded job #${id}`,'ok',1500);
  } catch(e){
    log('Failed to load job: '+e,'err');
    toast('Failed to load job','err');
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
loadBackends();
loadHealth();
startHealthInterval();
log('⚡ lightning-ocr UI initialised', 'ok');
</script>
</body>
</html>
"""