from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ..config import DEMO_USER_ID
from ..db import get_conn, row_to_dict
from ..services.session_manager import session_manager

router = APIRouter(tags=["dashboard"])

_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SceneCopilot Control Deck</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #081018;
      --panel: #0f1b2d;
      --panel-2: #13233a;
      --text: #edf4ff;
      --muted: #9fb3c8;
      --accent: #5eead4;
      --accent-2: #fbbf24;
      --danger: #fb7185;
      --line: rgba(255,255,255,0.09);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(94,234,212,0.18), transparent 32%),
        radial-gradient(circle at top right, rgba(251,191,36,0.12), transparent 22%),
        linear-gradient(180deg, #081018, #060b12 55%, #08131d);
      color: var(--text);
    }
    .shell {
      max-width: 1440px;
      margin: 0 auto;
      padding: 28px 24px 40px;
    }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 34px; letter-spacing: -0.03em; }
    h2 { font-size: 18px; margin-bottom: 12px; }
    p.meta { color: var(--muted); margin-top: 8px; max-width: 820px; line-height: 1.5; }
    .metrics, .grid { display: grid; gap: 16px; }
    .metrics { grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 24px; }
    .grid { grid-template-columns: 1.1fr 0.9fr; margin-top: 20px; align-items: start; }
    .stack { display: grid; gap: 16px; }
    .panel {
      background: linear-gradient(180deg, rgba(15,27,45,0.98), rgba(9,18,31,0.98));
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px;
      box-shadow: 0 16px 45px rgba(0,0,0,0.28);
    }
    .metric {
      min-height: 118px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }
    .metric .label { color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; }
    .metric .value { font-size: 30px; font-weight: 700; margin-top: 10px; }
    .metric .hint { color: var(--muted); font-size: 13px; line-height: 1.4; }
    .actions {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .field { display: grid; gap: 8px; margin-top: 12px; }
    label { color: var(--muted); font-size: 13px; }
    input, textarea, button {
      width: 100%;
      border-radius: 14px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.03);
      color: var(--text);
      font: inherit;
      padding: 12px 14px;
    }
    textarea { min-height: 96px; resize: vertical; }
    button {
      background: linear-gradient(135deg, #14b8a6, #0f766e);
      cursor: pointer;
      font-weight: 600;
      transition: transform 0.18s ease, filter 0.18s ease;
    }
    button.secondary {
      background: linear-gradient(135deg, #334155, #1f2937);
    }
    button.warn {
      background: linear-gradient(135deg, #f59e0b, #b45309);
    }
    button.reject {
      background: linear-gradient(135deg, #f43f5e, #be123c);
    }
    button:hover { transform: translateY(-1px); filter: brightness(1.05); }
    .row { display: flex; gap: 10px; flex-wrap: wrap; }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 12px;
      border-radius: 999px;
      background: rgba(94,234,212,0.09);
      color: var(--accent);
      font-size: 13px;
      margin-top: 10px;
    }
    .pill.warn { background: rgba(251,191,36,0.12); color: var(--accent-2); }
    .pill.danger { background: rgba(244,63,94,0.12); color: #fda4af; }
    .list { display: grid; gap: 10px; }
    .item {
      border: 1px solid rgba(255,255,255,0.07);
      border-radius: 16px;
      padding: 12px 14px;
      background: rgba(255,255,255,0.025);
    }
    .item strong { display: block; margin-bottom: 6px; }
    .item small { color: var(--muted); display: block; margin-top: 6px; }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "SFMono-Regular", ui-monospace, monospace;
      font-size: 12px;
      color: #d6e7fb;
    }
    .split { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .muted { color: var(--muted); }
    .live {
      max-height: 360px;
      overflow: auto;
      display: grid;
      gap: 10px;
      padding-right: 4px;
    }
    .event {
      border-left: 3px solid rgba(94,234,212,0.55);
      padding: 10px 12px;
      background: rgba(255,255,255,0.03);
      border-radius: 12px;
    }
    .event h4 { margin: 0 0 6px; font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--accent); }
    .banner {
      margin-top: 18px;
      padding: 14px 16px;
      border-radius: 18px;
      background: linear-gradient(135deg, rgba(94,234,212,0.16), rgba(59,130,246,0.14));
      border: 1px solid rgba(94,234,212,0.16);
      color: #d9fff9;
    }
    @media (max-width: 1080px) {
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .grid { grid-template-columns: 1fr; }
      .actions, .split { grid-template-columns: 1fr; }
    }
    @media (max-width: 720px) {
      .metrics { grid-template-columns: 1fr; }
      .shell { padding: 20px 16px 30px; }
      h1 { font-size: 28px; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <h1>SceneCopilot Control Deck</h1>
      <p class="meta">A browser workspace for runs, approvals, uploads, retrieval, and live agent telemetry. The Android Java app remains the primary field client, and this deck gives you the thicker control-plane surface that demo-heavy repos usually need.</p>
      <div class="banner">Tip: launch a text or image run here, then click a run in the right column to open its live SSE stream and detail view.</div>
    </header>

    <section class="metrics">
      <div class="panel metric"><div class="label">Scheduler</div><div class="value" id="metricScheduler">--</div><div class="hint" id="metricSchedulerHint">Pending and active runs</div></div>
      <div class="panel metric"><div class="label">Documents</div><div class="value" id="metricDocs">--</div><div class="hint" id="metricDocsHint">Indexed knowledge base</div></div>
      <div class="panel metric"><div class="label">Action Cards</div><div class="value" id="metricCards">--</div><div class="hint" id="metricCardsHint">Open and historical recommendations</div></div>
      <div class="panel metric"><div class="label">Recent Runs</div><div class="value" id="metricRuns">--</div><div class="hint" id="metricRunsHint">Latest execution snapshots</div></div>
    </section>

    <section class="grid">
      <div class="stack">
        <div class="panel">
          <h2>Launch Run</h2>
          <div class="actions">
            <div>
              <div class="field">
                <label for="promptInput">Prompt</label>
                <textarea id="promptInput">Inspect this scene and tell me what I should do next.</textarea>
              </div>
              <div class="field">
                <label for="sessionInput">Session ID (optional)</label>
                <input id="sessionInput" placeholder="Reuse a session or leave blank" />
              </div>
            </div>
            <div>
              <div class="field">
                <label for="scanImage">Optional image for scene analysis</label>
                <input id="scanImage" type="file" accept="image/*" />
              </div>
              <div class="field">
                <label for="visibleTextInput">Visible text hint (optional)</label>
                <textarea id="visibleTextInput" placeholder="Paste visible text if you already have OCR from another device"></textarea>
              </div>
            </div>
          </div>
          <div class="row" style="margin-top:14px;">
            <button id="launchRunButton">Launch Run</button>
            <button id="refreshAllButton" class="secondary">Refresh Dashboard</button>
          </div>
          <div class="pill" id="launchStatus">Ready</div>
        </div>

        <div class="split">
          <div class="panel">
            <h2>Upload Knowledge</h2>
            <div class="field">
              <label for="docFile">Document file</label>
              <input id="docFile" type="file" />
            </div>
            <div class="field">
              <label for="docTitle">Title override</label>
              <input id="docTitle" placeholder="Optional title" />
            </div>
            <div class="field">
              <label for="docTags">Tags</label>
              <input id="docTags" placeholder="safety, wearable, sop" />
            </div>
            <button id="uploadDocButton" class="warn">Upload Document</button>
            <div class="pill warn" id="uploadStatus">Waiting for upload</div>
          </div>

          <div class="panel">
            <h2>Knowledge Search</h2>
            <div class="field">
              <label for="searchQuery">Search query</label>
              <input id="searchQuery" placeholder="warning panel battery leak" />
            </div>
            <div class="row">
              <label class="pill"><input type="checkbox" id="includeExternal" style="width:auto; padding:0; margin:0;" /> Include external results</label>
            </div>
            <button id="searchDocsButton" class="secondary">Search</button>
            <div class="list" id="searchResults" style="margin-top:12px;"></div>
          </div>
        </div>

        <div class="split">
          <div class="panel">
            <h2>Recent Documents</h2>
            <div class="list" id="documentsList"></div>
          </div>
          <div class="panel">
            <h2>Action Cards</h2>
            <div class="list" id="cardsList"></div>
          </div>
        </div>

        <div class="panel">
          <h2>Recent Captures</h2>
          <div class="list" id="capturesList"></div>
        </div>
      </div>

      <div class="stack">
        <div class="panel">
          <h2>Recent Runs</h2>
          <div class="list" id="runsList"></div>
        </div>

        <div class="panel">
          <h2>Selected Run</h2>
          <div id="runMeta" class="muted">Select a run from the list or launch one from the left.</div>
          <div class="row" style="margin-top:14px;">
            <button id="approveButton" class="warn" style="display:none;">Approve</button>
            <button id="rejectButton" class="reject" style="display:none;">Reject</button>
          </div>
          <div class="field" id="approvalNoteWrap" style="display:none;">
            <label for="approvalNote">Approval note</label>
            <input id="approvalNote" placeholder="Optional note for this decision" />
          </div>
          <div class="split" style="margin-top:14px;">
            <div>
              <h3 style="margin-bottom:10px;">Artifacts</h3>
              <div class="list" id="artifactsList"></div>
            </div>
            <div>
              <h3 style="margin-bottom:10px;">Approvals</h3>
              <div class="list" id="approvalsList"></div>
            </div>
          </div>
          <div style="margin-top:14px;">
            <h3 style="margin-bottom:10px;">Audit Trail</h3>
            <div class="list" id="auditList"></div>
          </div>
        </div>

        <div class="panel">
          <h2>Live Event Stream</h2>
          <div class="live" id="eventsList"></div>
        </div>
      </div>
    </section>
  </div>

  <script>
    let selectedRunId = null;
    let selectedSessionId = null;
    let source = null;

    const qs = (id) => document.getElementById(id);

    async function fetchJson(url, options) {
      const response = await fetch(url, options);
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || ('Request failed: ' + response.status));
      }
      return response.json();
    }

    function listHtml(items, renderer, emptyText) {
      if (!items || !items.length) return `<div class="item"><span class="muted">${emptyText}</span></div>`;
      return items.map(renderer).join("");
    }

    function metric(id, value, hint) {
      qs(id).textContent = value;
      if (hint) qs(id + "Hint").textContent = hint;
    }

    function formatJson(value) {
      return JSON.stringify(value, null, 2);
    }

    async function refreshDashboard() {
      const [health, state] = await Promise.all([
        fetchJson('/api/health'),
        fetchJson('/api/state'),
      ]);
      metric('metricScheduler', `${health.scheduler.active_runs}/${health.scheduler.max_concurrent_runs}`, `Pending ${health.scheduler.pending_runs} · submitted ${health.scheduler.submitted_runs}`);
      metric('metricDocs', String(state.documents.length), 'Latest uploaded and seeded docs');
      metric('metricCards', String(state.action_cards.length), 'Recent recommended actions');
      metric('metricRuns', String(state.recent_runs.length), 'Recent run snapshots');

      qs('documentsList').innerHTML = listHtml(state.documents, (item) => `
        <div class="item">
          <strong>${item.title}</strong>
          <div>${item.summary || ''}</div>
          <small>${item.source_path || 'local upload'}</small>
        </div>
      `, 'No documents yet.');

      qs('cardsList').innerHTML = listHtml(state.action_cards, (item) => `
        <div class="item">
          <strong>${item.title}</strong>
          <div>${item.detail}</div>
          <small>${item.priority} · ${item.status}</small>
        </div>
      `, 'No action cards yet.');

      qs('capturesList').innerHTML = listHtml(state.recent_captures, (item) => `
        <div class="item">
          <strong>${item.scene_summary}</strong>
          <div>${item.ocr_text || 'No OCR text saved'}</div>
          <small>${item.risk_level} · run ${item.run_id || 'n/a'}</small>
        </div>
      `, 'No captures yet.');

      qs('runsList').innerHTML = listHtml(state.recent_runs, (item) => `
        <button class="item secondary" style="text-align:left;" onclick="selectRun('${item.id}', '${item.session_id}')">
          <strong>${item.route_name || 'run'} · ${item.status}</strong>
          <div>${item.user_message}</div>
          <small>${item.current_stage || 'queued'} · latency ${item.latency_ms || 'n/a'} ms</small>
        </button>
      `, 'No recent runs yet.');
    }

    async function selectRun(runId, sessionId) {
      selectedRunId = runId;
      selectedSessionId = sessionId;
      await loadRunDetail();
      openStream();
    }

    async function loadRunDetail() {
      if (!selectedRunId) return;
      const run = await fetchJson(`/api/runs/${selectedRunId}`);
      qs('runMeta').innerHTML = `
        <div class="pill">${run.status}</div>
        <p style="margin-top:10px;"><strong>Prompt:</strong> ${run.user_message}</p>
        <p class="muted" style="margin-top:8px;">Route ${run.route_name || 'n/a'} · Stage ${run.current_stage || 'n/a'} · Latency ${run.latency_ms || 'n/a'} ms</p>
        <p class="muted" style="margin-top:8px;">Session ${run.session_id} · Run ${run.id}</p>
        <pre style="margin-top:12px;">${run.output_text || 'No final output yet.'}</pre>
      `;
      qs('artifactsList').innerHTML = listHtml(run.artifacts, (item) => `
        <div class="item">
          <strong>${item.artifact_type}</strong>
          <div>${item.stage} · ${item.provider}</div>
          <small>${(item.content_json && item.content_json.preview) || (item.content_json && item.content_json.summary) || (item.content_json && item.content_json.query) || 'artifact recorded'}</small>
        </div>
      `, 'No artifacts yet.');
      qs('approvalsList').innerHTML = listHtml(run.approvals, (item) => `
        <div class="item">
          <strong>${item.status}</strong>
          <div>${item.reason}</div>
          <small>${item.risk_level} · ${item.reviewer_note || 'no reviewer note'}</small>
        </div>
      `, 'No approvals yet.');
      qs('auditList').innerHTML = listHtml(run.audit_log, (item) => `
        <div class="item">
          <strong>${item.event_type}</strong>
          <pre>${formatJson(item.detail_json || {})}</pre>
        </div>
      `, 'No audit events yet.');
      const waiting = run.status === 'waiting_for_approval';
      qs('approveButton').style.display = waiting ? 'inline-block' : 'none';
      qs('rejectButton').style.display = waiting ? 'inline-block' : 'none';
      qs('approvalNoteWrap').style.display = waiting ? 'grid' : 'none';
    }

    function openStream() {
      if (source) {
        source.close();
        source = null;
      }
      if (!selectedSessionId || !selectedRunId) return;
      qs('eventsList').innerHTML = '';
      source = new EventSource(`/api/events/${selectedSessionId}?run_id=${selectedRunId}`);
      source.onmessage = null;
      source.addEventListener('final', async (evt) => {
        appendEvent('final', JSON.parse(evt.data));
        await loadRunDetail();
        await refreshDashboard();
      });
      ['queued','run_started','stage','artifact','approval','approval_resolved','policy','run_plan','error'].forEach((eventName) => {
        source.addEventListener(eventName, async (evt) => {
          appendEvent(eventName, JSON.parse(evt.data));
          if (eventName === 'approval' || eventName === 'approval_resolved') {
            await loadRunDetail();
            await refreshDashboard();
          }
        });
      });
    }

    function appendEvent(eventType, event) {
      if (event.event_type === 'heartbeat') return;
      const wrap = document.createElement('div');
      wrap.className = 'event';
      wrap.innerHTML = `<h4>${eventType}</h4><pre>${formatJson(event.payload || {})}</pre>`;
      qs('eventsList').prepend(wrap);
    }

    async function launchRun() {
      const prompt = qs('promptInput').value.trim();
      const sessionId = qs('sessionInput').value.trim();
      const image = qs('scanImage').files[0];
      const visibleText = qs('visibleTextInput').value.trim();
      qs('launchStatus').textContent = 'Submitting run...';

      let payload;
      if (image) {
        const form = new FormData();
        form.append('image', image);
        form.append('prompt', prompt);
        if (sessionId) form.append('session_id', sessionId);
        if (visibleText) form.append('visible_text', visibleText);
        payload = await fetchJson('/api/scans/analyze', { method: 'POST', body: form });
      } else {
        payload = await fetchJson('/api/chat', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ message: prompt, session_id: sessionId || null }),
        });
      }
      qs('launchStatus').textContent = `Queued run ${payload.run_id} at position ${payload.queue_position}`;
      qs('sessionInput').value = payload.session_id;
      await selectRun(payload.run_id, payload.session_id);
      await refreshDashboard();
    }

    async function uploadDocument() {
      const file = qs('docFile').files[0];
      if (!file) {
        qs('uploadStatus').textContent = 'Choose a document first.';
        return;
      }
      const form = new FormData();
      form.append('file', file);
      if (qs('docTitle').value.trim()) form.append('title', qs('docTitle').value.trim());
      if (qs('docTags').value.trim()) form.append('tags', qs('docTags').value.trim());
      const payload = await fetchJson('/api/documents/upload', { method: 'POST', body: form });
      qs('uploadStatus').textContent = `Uploaded ${payload.title}`;
      qs('docFile').value = '';
      qs('docTitle').value = '';
      qs('docTags').value = '';
      await refreshDashboard();
    }

    async function searchDocs() {
      const query = qs('searchQuery').value.trim();
      if (!query) return;
      const includeExternal = qs('includeExternal').checked ? '&include_external=1' : '';
      const payload = await fetchJson(`/api/documents/search?q=${encodeURIComponent(query)}&limit=6${includeExternal}`);
      qs('searchResults').innerHTML = listHtml(payload.items, (item) => `
        <div class="item">
          <strong>${item.title}</strong>
          <div>${item.snippet || item.summary || ''}</div>
          <small>${item.source || 'local'} · ${item.source_path || ''}</small>
        </div>
      `, 'No search results yet.');
    }

    async function resolveApproval(decision) {
      if (!selectedRunId) return;
      await fetchJson(`/api/runs/${selectedRunId}/approve`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          decision,
          reviewer_note: qs('approvalNote').value.trim() || null,
        }),
      });
      qs('approvalNote').value = '';
      await loadRunDetail();
      await refreshDashboard();
    }

    qs('launchRunButton').addEventListener('click', () => launchRun().catch((err) => qs('launchStatus').textContent = err.message));
    qs('refreshAllButton').addEventListener('click', () => refreshDashboard().catch((err) => alert(err.message)));
    qs('uploadDocButton').addEventListener('click', () => uploadDocument().catch((err) => qs('uploadStatus').textContent = err.message));
    qs('searchDocsButton').addEventListener('click', () => searchDocs().catch((err) => alert(err.message)));
    qs('approveButton').addEventListener('click', () => resolveApproval('approve').catch((err) => alert(err.message)));
    qs('rejectButton').addEventListener('click', () => resolveApproval('reject').catch((err) => alert(err.message)));

    refreshDashboard().catch((err) => {
      qs('launchStatus').textContent = err.message;
    });
    setInterval(() => refreshDashboard().catch(() => {}), 8000);
  </script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def root_dashboard() -> HTMLResponse:
    return HTMLResponse(_DASHBOARD_HTML)


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page() -> HTMLResponse:
    return HTMLResponse(_DASHBOARD_HTML)


@router.get("/api/dashboard/summary")
async def dashboard_summary() -> dict[str, object]:
    conn = get_conn()
    try:
        counts = {
            "documents": conn.execute("SELECT COUNT(*) FROM documents WHERE user_id = ?", (DEMO_USER_ID,)).fetchone()[0],
            "chunks": conn.execute("SELECT COUNT(*) FROM document_chunks WHERE user_id = ?", (DEMO_USER_ID,)).fetchone()[0],
            "action_cards": conn.execute("SELECT COUNT(*) FROM action_cards WHERE user_id = ?", (DEMO_USER_ID,)).fetchone()[0],
            "captures": conn.execute("SELECT COUNT(*) FROM scene_captures WHERE user_id = ?", (DEMO_USER_ID,)).fetchone()[0],
        }
        docs = conn.execute(
            "SELECT id, title, summary, source_path, created_at FROM documents WHERE user_id = ? ORDER BY created_at DESC LIMIT 8",
            (DEMO_USER_ID,),
        ).fetchall()
        cards = conn.execute(
            "SELECT id, title, detail, priority, status, created_at FROM action_cards WHERE user_id = ? ORDER BY created_at DESC LIMIT 8",
            (DEMO_USER_ID,),
        ).fetchall()
    finally:
        conn.close()
    recent_runs = await asyncio.to_thread(session_manager.list_recent_runs, user_id=DEMO_USER_ID, limit=8)
    return {
        "counts": counts,
        "documents": [row_to_dict(row) for row in docs],
        "action_cards": [row_to_dict(row) for row in cards],
        "recent_runs": recent_runs,
    }
