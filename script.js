// Place at: static/script.js
/**
 * static/script.js — HealthNav Frontend Logic v2.1
 * Fixes:
 *   - doLogout() now clears chat + all data displays (patient isolation)
 *   - onLoggedIn() clears previous user's chat on every new login
 *   - loadChatHistory() loads chat_messages from DB on login
 *   - Chat messages saved to DB per patient (persistent across logins)
 */

const API = '';
let SESSION = null;
let CHAT_FILE = null;
let SELECTED_FILE = null;
let POLL_INTERVALS = {};

// ── Utilities ──────────────────────────────────────────────────────────────

async function apiRequest(url, options = {}) {
  options.credentials = 'include';
  const r = await fetch(API + url, options);
  return r;
}

async function apiJSON(url, body, method = 'POST') {
  return apiRequest(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function today() { return new Date().toISOString().split('T')[0]; }
function esc(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }
function val(id) { return (document.getElementById(id) || {}).value || ''; }
function setVal(id, v) { const e = document.getElementById(id); if (e) e.value = v; }
function setText(id, v) { const e = document.getElementById(id); if (e) e.textContent = v; }

function toast(msg, type = 'info') {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  document.getElementById('toast-wrap').appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function formatDate(d) {
  if (!d) return '';
  try {
    return new Date(d).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch { return String(d).split('T')[0]; }
}

// ── Chat reset helper ──────────────────────────────────────────────────────

function resetChatDisplay() {
  const msgs = document.getElementById('chat-msgs');
  if (msgs) {
    msgs.innerHTML = '<div class="msg msg-ai"><div class="msg-bubble">Hello! I\'m HealthNav. Ask me about your health records, uploaded reports, or any medical questions. \uD83E\uDE7A</div></div>';
  }
}

function resetAllDataDisplays() {
  // Clear every data panel so previous user's data is never shown to next user
  const panels = [
    'labs-body', 'abnormal-body', 'vis-body', 'meds-body',
    'files-body', 'recent-up', 'ov-labs', 'ov-abn',
    's-labs', 's-abn', 's-vis', 's-med',
  ];
  panels.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = '';
  });
  resetChatDisplay();
}

// ── Auth ──────────────────────────────────────────────────────────────────

async function doLogin() {
  const u = val('login-user'), p = val('login-pass');
  setText('login-err', '');
  document.getElementById('login-btn').disabled = true;
  try {
    const r = await apiJSON('/api/login', { username: u, password: p });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Login failed');
    SESSION = d;
    onLoggedIn();
  } catch (e) {
    setText('login-err', e.message);
  } finally {
    document.getElementById('login-btn').disabled = false;
  }
}

async function doRegister() {
  setText('reg-err', '');
  try {
    const r = await apiJSON('/api/register', {
      username: val('reg-user'), email: val('reg-email'),
      full_name: val('reg-name'), password: val('reg-pass'),
      confirm_password: val('reg-pass2'),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Registration failed');
    SESSION = d;
    onLoggedIn();
  } catch (e) { setText('reg-err', e.message); }
}

async function doLogout() {
  await apiRequest('/api/logout', { method: 'POST' });
  SESSION = null;
  // CRITICAL: clear all data so next user never sees previous user's info
  resetAllDataDisplays();
  document.getElementById('app').style.display = 'none';
  document.getElementById('auth-screen').classList.add('active');
}

async function checkSession() {
  try {
    const r = await apiRequest('/api/me');
    if (r.ok) { SESSION = await r.json(); onLoggedIn(); }
  } catch (_) {}
}

function onLoggedIn() {
  document.getElementById('auth-screen').classList.remove('active');
  document.getElementById('app').style.display = 'flex';
  const ref = (SESSION.patient_id || '').slice(-8);
  setText('topbar-patient', `${SESSION.username} · ${ref}`);

  // Always reset displays when a user logs in — prevents data bleed between users
  resetAllDataDisplays();

  // Load this user's chat history from DB, then load their data
  loadChatHistory().then(() => {
    loadOverview();
    loadRecentUploads();
  });
}

// ── Chat history (persistent per patient) ─────────────────────────────────

async function loadChatHistory() {
  try {
    const r = await apiRequest('/api/chat/history');
    if (!r.ok) return; // endpoint may not exist yet — silently skip
    const d = await r.json();
    const messages = d.messages || [];
    if (!messages.length) return; // no history — keep the welcome message

    const msgs = document.getElementById('chat-msgs');
    if (!msgs) return;

    // Clear welcome message and load real history
    msgs.innerHTML = '';
    messages.forEach(m => {
      const div = document.createElement('div');
      div.className = m.role === 'user' ? 'msg msg-user' : 'msg msg-ai';
      div.innerHTML = m.role === 'user'
        ? `<div class="msg-bubble">${esc(m.message_text)}</div>`
        : `<div class="msg-bubble">${m.message_text.replace(/\n/g, '<br>')}</div>`;
      msgs.appendChild(div);
    });
    msgs.scrollTop = msgs.scrollHeight;
  } catch (_) {
    // API endpoint not yet implemented — just show welcome message
  }
}

// ── Navigation ────────────────────────────────────────────────────────────

function go(name) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const sec = document.getElementById(`section-${name}`);
  if (sec) sec.classList.add('active');
  const nav = document.getElementById(`nav-${name}`);
  if (nav) nav.classList.add('active');
  const loaders = {
    labs: loadLabs, visits: loadVisits, medications: loadMeds,
    files: loadFiles, overview: loadOverview,
  };
  if (loaders[name]) loaders[name]();
  if (name === 'add-lab') setVal('al-date', today());
  if (name === 'add-visit') setVal('av-date', today());
}

// ── Data loaders ──────────────────────────────────────────────────────────

async function loadOverview() {
  try {
    const [sum, labs, abn] = await Promise.all([
      apiRequest('/api/summary').then(r => r.json()),
      apiRequest('/api/labs?limit=10').then(r => r.json()),
      apiRequest('/api/labs/abnormal').then(r => r.json()),
    ]);
    setText('s-labs', sum.total_labs ?? 0);
    setText('s-abn', sum.abnormal_count ?? 0);
    setText('s-vis', sum.total_visits ?? 0);
    setText('s-med', sum.active_meds ?? 0);
    renderLabsTable('ov-labs', labs.labs || []);
    renderAbnTable('ov-abn', abn.labs || []);
  } catch (e) { console.error('Overview failed', e); }
}

async function loadLabs() {
  try {
    const d = await apiRequest('/api/labs?limit=200').then(r => r.json());
    renderLabsTableFull('labs-body', d.labs || []);
  } catch (_) {}
}

async function loadVisits() {
  try {
    const d = await apiRequest('/api/visits').then(r => r.json());
    renderVisitsTable('vis-body', d.visits || []);
  } catch (_) {}
}

async function loadMeds() {
  try {
    const d = await apiRequest('/api/medications').then(r => r.json());
    renderMedsTable('meds-body', d.medications || []);
  } catch (_) {}
}

async function loadFiles() {
  try {
    const d = await apiRequest('/api/files').then(r => r.json());
    renderFilesTable('files-body', d.files || []);
  } catch (_) {}
}

async function loadRecentUploads() {
  try {
    const d = await apiRequest('/api/files').then(r => r.json());
    renderFilesTable('recent-up', (d.files || []).slice(0, 5));
  } catch (_) {}
}

// ── Renderers ─────────────────────────────────────────────────────────────

function abnBadge(isAbn) {
  return isAbn
    ? '<span class="badge badge-red">Abnormal</span>'
    : '<span class="badge badge-green">Normal</span>';
}

function renderLabsTable(id, rows) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!rows.length) { el.innerHTML = '<div class="empty-state"><div class="icon">🧪</div><p>No labs yet</p></div>'; return; }
  el.innerHTML = `<table class="data-table"><thead><tr><th>Test</th><th>Value</th><th>Unit</th><th>Date</th><th>Status</th></tr></thead><tbody>` +
    rows.map(l => `<tr><td>${esc(l.test_name||'')}</td><td><strong>${esc(l.test_value||'')}</strong></td><td>${esc(l.unit||'')}</td><td>${formatDate(l.test_date)}</td><td>${abnBadge(l.is_abnormal)}</td></tr>`).join('') +
    '</tbody></table>';
}

function renderLabsTableFull(id, rows) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!rows.length) { el.innerHTML = '<div class="empty-state"><div class="icon">🧪</div><p>No lab results yet</p></div>'; return; }
  el.innerHTML = `<table class="data-table"><thead><tr><th>Test</th><th>Value</th><th>Unit</th><th>Reference</th><th>Date</th><th>Lab</th><th>Status</th></tr></thead><tbody>` +
    rows.map(l => `<tr><td>${esc(l.test_name||'')}</td><td><strong>${esc(l.test_value||'')}</strong></td><td>${esc(l.unit||'')}</td><td style="font-size:12px;color:#6c757d">${esc(l.reference_range||'')}</td><td>${formatDate(l.test_date)}</td><td style="font-size:12px;color:#6c757d">${esc(l.lab_name||'')}</td><td>${abnBadge(l.is_abnormal)}</td></tr>`).join('') +
    '</tbody></table>';
}

function renderAbnTable(id, rows) {
  const el = document.getElementById(id);
  if (!el) return;
  const abn = rows.filter(l => l.is_abnormal);
  if (!abn.length) { el.innerHTML = '<div class="empty-state"><div class="icon">✅</div><p>No abnormal results</p></div>'; return; }
  el.innerHTML = `<table class="data-table"><thead><tr><th>Test</th><th>Value</th><th>Unit</th><th>Reference</th><th>Date</th></tr></thead><tbody>` +
    abn.map(l => `<tr><td><strong>${esc(l.test_name||'')}</strong></td><td style="color:#e5383b;font-weight:600">${esc(l.test_value||'')}</td><td>${esc(l.unit||'')}</td><td style="font-size:12px">${esc(l.reference_range||'')}</td><td>${formatDate(l.test_date)}</td></tr>`).join('') +
    '</tbody></table>';
}

function renderVisitsTable(id, rows) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!rows.length) { el.innerHTML = '<div class="empty-state"><div class="icon">📋</div><p>No visits yet</p></div>'; return; }
  el.innerHTML = rows.map(v => `
    <div class="card" style="margin-bottom:12px">
      <strong>${esc(v.visit_type||'Visit')}</strong>
      <span class="badge badge-blue" style="margin-left:8px">${formatDate(v.visit_date)}</span>
      ${v.doctor_name ? `<span style="font-size:12px;color:#6c757d;margin-left:8px">${esc(v.doctor_name)}</span>` : ''}
      ${v.chief_complaint ? `<div style="margin-top:6px;font-size:13px"><strong>Complaint:</strong> ${esc(v.chief_complaint)}</div>` : ''}
      ${v.clinical_notes ? `<div style="margin-top:4px;font-size:13px;color:#495057">${esc(v.clinical_notes)}</div>` : ''}
    </div>`).join('');
}

function renderMedsTable(id, rows) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!rows.length) { el.innerHTML = '<div class="empty-state"><div class="icon">💊</div><p>No medications yet</p></div>'; return; }
  el.innerHTML = `<table class="data-table"><thead><tr><th>Medication</th><th>Dose</th><th>Frequency</th><th>Indication</th><th>Since</th><th>Status</th></tr></thead><tbody>` +
    rows.map(m => `<tr><td><strong>${esc(m.medication_name||'')}</strong></td><td>${esc(m.dosage||'')}</td><td>${esc(m.frequency||'')}</td><td style="font-size:12px;color:#6c757d">${esc(m.indication||'')}</td><td>${formatDate(m.start_date)}</td><td>${m.is_active ? '<span class="badge badge-green">Active</span>' : '<span class="badge badge-gray">Inactive</span>'}</td></tr>`).join('') +
    '</tbody></table>';
}

function renderFilesTable(id, rows) {
  const el = document.getElementById(id);
  if (!el) return;
  if (!rows.length) { el.innerHTML = '<div class="empty-state"><p>No files yet</p></div>'; return; }
  el.innerHTML = rows.map(f => {
    const st = f.parse_status || 'unknown';
    const cls = st === 'done' ? 'status-done' : ['queued','processing','analyzing'].includes(st) ? 'status-queued' : st === 'failed' ? 'status-failed' : 'status-unsupported';
    const labs = f.labs_parsed || 0, vis = f.visits_parsed || 0, meds = f.meds_parsed || 0;
    const det = st === 'done'
      ? (labs + vis + meds > 0 ? `${labs} labs · ${vis} visits · ${meds} meds` : 'No structured items — chat still works')
      : st === 'failed' ? 'Parsing failed' : st === 'queued' || st === 'processing' ? 'Parsing...' : '';
    return `<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid #e9ecef">
      <div><div style="font-size:13px;font-weight:500">${esc(f.original_filename||f.file_name||'')}</div>
      <div style="font-size:11px;color:#6c757d;margin-top:2px">${det} · ${formatDate(f.uploaded_at||f.upload_time)}</div></div>
      <div style="display:flex;align-items:center;gap:8px">
        <span style="font-size:12px"><span class="file-status-dot ${cls}"></span>${st}</span>
        <button class="btn btn-outline btn-sm" onclick="reparseFile(${f.id})">Re-parse</button>
      </div></div>`;
  }).join('');
}

// ── Upload ────────────────────────────────────────────────────────────────

function selectFile(f) {
  if (!f) return;
  SELECTED_FILE = f;
  setText('up-filename', f.name);
  document.getElementById('up-preview').style.display = 'block';
  document.getElementById('up-btn').disabled = false;
}

const upZone = document.getElementById && document.getElementById('upzone');
if (upZone) {
  upZone.addEventListener('dragover', e => { e.preventDefault(); upZone.classList.add('dragover'); });
  upZone.addEventListener('dragleave', () => upZone.classList.remove('dragover'));
  upZone.addEventListener('drop', e => {
    e.preventDefault(); upZone.classList.remove('dragover');
    const f = e.dataTransfer.files[0]; if (f) selectFile(f);
  });
}

async function doUpload() {
  if (!SELECTED_FILE) return;
  document.getElementById('up-btn').disabled = true;
  showProg('Uploading...', 25);
  const fd = new FormData();
  fd.append('file', SELECTED_FILE);
  fd.append('category', val('up-cat'));
  fd.append('notes', val('up-note'));
  try {
    const r = await apiRequest('/api/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Upload failed');
    toast('Uploaded. Parsing...', 'success');
    showProg('AI extracting data...', 55);
    loadRecentUploads();
    if (d.file_id && d.can_parse) pollParse(d.file_id, SELECTED_FILE.name);
    else { hideProg(); showUploadResult({ parse_status: 'unsupported', labs_inserted: 0 }, SELECTED_FILE.name); document.getElementById('up-btn').disabled = false; }
  } catch (e) { hideProg(); toast(e.message, 'error'); document.getElementById('up-btn').disabled = false; }
}

function pollParse(fid, fname) {
  let n = 0;
  const iv = setInterval(async () => {
    n++;
    try {
      const r = await apiRequest(`/api/files/${fid}/parse-status`);
      const d = await r.json();
      if (d.done || n > 60) {
        clearInterval(iv);
        hideProg();
        showUploadResult(d, fname);
        loadOverview(); loadRecentUploads();
        document.getElementById('up-btn').disabled = false;
        const total = (d.labs_inserted || 0) + (d.visits_inserted || 0) + (d.meds_inserted || 0);
        if (total > 0) toast(`✓ Extracted: ${d.labs_inserted || 0} labs · ${d.visits_inserted || 0} visits · ${d.meds_inserted || 0} meds`, 'success');
      } else {
        const labels = { queued: 'Queued...', parsing: 'Parsing document...', analyzing: 'AI extracting data...', embedding: 'Finalizing...' };
        showProg(labels[d.parse_status] || 'Processing...', 65);
      }
    } catch (_) { if (n > 60) clearInterval(iv); }
  }, 2500);
}

async function reparseFile(fid) {
  try {
    await apiRequest(`/api/files/${fid}/reparse`, { method: 'POST' });
    toast('Re-parse queued.', 'info');
    setTimeout(() => { loadFiles(); loadRecentUploads(); }, 600);
    pollFileStatus(fid);
  } catch (e) { toast('Failed to reparse', 'error'); }
}

function pollFileStatus(fid) {
  let n = 0;
  const iv = setInterval(async () => {
    n++;
    const r = await apiRequest(`/api/files/${fid}/parse-status`);
    const d = await r.json();
    if (d.done || n > 40) { clearInterval(iv); loadFiles(); loadRecentUploads(); loadOverview(); }
  }, 2500);
}

function showProg(lbl, pct) {
  document.getElementById('up-progress').style.display = 'block';
  setText('up-prog-label', lbl);
  document.getElementById('up-prog-fill').style.width = pct + '%';
}
function hideProg() { document.getElementById('up-progress').style.display = 'none'; }

function showUploadResult(d, fname) {
  document.getElementById('up-result').style.display = 'block';
  setText('up-result-title', d.parse_status === 'done' ? `✓ Parsing complete — ${fname}` : `Parsing ${d.parse_status} — ${fname}`);
  const labs = d.labs_inserted || 0, vis = d.visits_inserted || 0, meds = d.meds_inserted || 0;
  document.getElementById('up-result-body').innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:12px">
      <div class="stat-card"><div class="stat-num blue">${labs}</div><div class="stat-label">Lab results</div></div>
      <div class="stat-card"><div class="stat-num blue">${vis}</div><div class="stat-label">Visit notes</div></div>
      <div class="stat-card"><div class="stat-num blue">${meds}</div><div class="stat-label">Medications</div></div>
    </div>
    ${d.confidence ? `<div style="font-size:12px;color:#6c757d">Confidence: <strong style="color:${d.confidence==='high'?'#2dc653':d.confidence==='low'?'#e5383b':'#fca311'}">${d.confidence}</strong></div>` : ''}
    ${labs + vis + meds === 0 && d.parse_status === 'done' ? '<div style="margin-top:10px;padding:10px 14px;background:#fff8e1;border-radius:8px;font-size:13px;color:#7d5a00">No structured data extracted. You can still ask HealthNav about this file using chat.</div>' : ''}
    ${labs > 0 ? '<div style="margin-top:10px"><a href="#" onclick="go(\'labs\');return false" style="color:#4361ee;font-size:13px">View extracted labs →</a></div>' : ''}
  `;
}

// ── Add data forms ─────────────────────────────────────────────────────────

async function saveLab() {
  const nm = val('al-name'), dt = val('al-date');
  if (!nm || !dt) { toast('Test name and date required', 'error'); return; }
  try {
    const r = await apiJSON('/api/labs/add', {
      test_name: nm, test_date: dt, test_value: val('al-val'),
      unit: val('al-unit'), reference_range: val('al-range'),
      lab_name: val('al-lab'), is_abnormal: document.getElementById('al-abn').checked,
    });
    if (!r.ok) throw new Error((await r.json()).detail);
    toast('Lab saved!', 'success'); go('labs');
  } catch (e) { toast(e.message, 'error'); }
}

async function saveVisit() {
  const dt = val('av-date');
  if (!dt) { toast('Visit date required', 'error'); return; }
  try {
    const r = await apiJSON('/api/visits/add', {
      visit_date: dt, visit_type: val('av-type'),
      chief_complaint: val('av-comp'), clinical_notes: val('av-notes'),
      doctor_name: val('av-doc'),
    });
    if (!r.ok) throw new Error((await r.json()).detail);
    toast('Visit saved!', 'success'); go('visits');
  } catch (e) { toast(e.message, 'error'); }
}

async function saveMed() {
  const nm = val('am-name');
  if (!nm) { toast('Medication name required', 'error'); return; }
  try {
    const r = await apiJSON('/api/medications/add', {
      medication_name: nm, dosage: val('am-dose'), frequency: val('am-freq'),
      start_date: val('am-start') || null, prescribing_doctor: val('am-doc'),
      indication: val('am-ind'), is_active: true,
    });
    if (!r.ok) throw new Error((await r.json()).detail);
    toast('Medication saved!', 'success'); go('medications');
  } catch (e) { toast(e.message, 'error'); }
}

// ── Chat ───────────────────────────────────────────────────────────────────

function qask(q) { setVal('chat-in', q); sendChat(); }

async function sendChat() {
  const q = (val('chat-in') || '').trim();
  if (!q) return;
  setVal('chat-in', '');
  appendMsg(q, 'user');
  const tid = appendMsg('Thinking...', 'think');
  document.getElementById('chat-btn').disabled = true;
  const file = document.getElementById('chat-file').files[0];
  const save = document.getElementById('chat-save').checked;
  try {
    let r, d;
    if (file) {
      const fd = new FormData();
      fd.append('question', q); fd.append('save_to_record', save); fd.append('file', file);
      r = await apiRequest('/api/chat-with-file', { method: 'POST', body: fd });
      document.getElementById('chat-file').value = '';
    } else {
      r = await apiJSON('/api/chat', { question: q, role: 'patient' });
    }
    d = await r.json();
    removeMsg(tid);
    const src = d.sources || {};
    const sl = [src.used_records ? '📋 records' : '', src.used_textbook ? '📚 textbook' : '', src.used_attachment ? '📄 attachment' : ''].filter(Boolean).join(', ');
    appendMsg(d.answer || 'No response.', 'ai', sl);
  } catch (e) {
    removeMsg(tid);
    appendMsg('Sorry, something went wrong. Please try again.', 'ai');
  } finally {
    document.getElementById('chat-btn').disabled = false;
  }
}

function appendMsg(text, role, src = '') {
  const id = 'm' + Date.now() + Math.random().toString(36).slice(2);
  const d = document.createElement('div');
  d.id = id;
  d.className = 'msg ' + (role === 'user' ? 'msg-user' : role === 'think' ? 'msg-ai msg-thinking' : 'msg-ai');
  d.innerHTML = role === 'ai' ? text.replace(/\n/g, '<br>') + (src ? `<div class="msg-sources">Sources: ${src}</div>` : '') : esc(text);
  const msgs = document.getElementById('chat-msgs');
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
  return id;
}

function removeMsg(id) { document.getElementById(id)?.remove(); }

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  checkSession();
});