/* ── DocuMind — Frontend Logic ──────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);

// ── DOM refs ──────────────────────────────────────────────────────────────────
const chatWindow    = $('chat-window');
const queryInput    = $('query-input');
const sendBtn       = $('send-btn');
const statusDot     = $('status-dot');
const statusLabel   = $('status-label');
const vectorsNum    = $('vectors-num');
const docList       = $('doc-list');
const docCount      = $('doc-count');
const chunkCount    = $('chunk-count');
const contextList   = $('context-list');
const fileInput     = $('file-input');
const uploadZone    = $('upload-zone');
const browseBtn     = $('browse-btn');
const progressWrap  = $('progress-wrap');
const progressFill  = $('progress-fill');
const progressLabel = $('progress-label');
const reindexBtn    = $('reindex-btn');
const resetBtn      = $('reset-btn');

// ── State ──────────────────────────────────────────────────────────────────────
let isQuerying = false;

// ── Status ─────────────────────────────────────────────────────────────────────
function setStatus(state, label) {
  statusDot.className = `status-indicator ${state}`;
  statusLabel.textContent = label;
}

async function refreshStats() {
  try {
    const res  = await fetch('/api/stats');
    const data = await res.json();

    vectorsNum.textContent = data.vectors_indexed ?? 0;
    docCount.textContent   = `${data.document_count ?? 0} file${data.document_count !== 1 ? 's' : ''}`;

    renderDocList(data.documents || []);

    if (data.pipeline_ready) {
      setStatus('ready', 'Ready');
      sendBtn.disabled = false;
    } else {
      setStatus('', 'No documents');
      sendBtn.disabled = queryInput.value.trim() === '';
    }

    reindexBtn.style.display = data.document_count > 0 ? 'block' : 'none';
  } catch (e) {
    setStatus('error', 'Connection error');
  }
}

// ── Document list ──────────────────────────────────────────────────────────────
function renderDocList(docs) {
  if (!docs.length) {
    docList.innerHTML = '<p class="empty-hint">No documents indexed yet.</p>';
    return;
  }
  docList.innerHTML = docs.map(name => {
    const ext  = name.split('.').pop().toUpperCase();
    const base = name.length > 30 ? name.slice(0, 28) + '…' : name;
    return `
      <div class="doc-item">
        <div class="doc-icon">${ext}</div>
        <div class="doc-info">
          <div class="doc-name" title="${name}">${base}</div>
          <div class="doc-meta">indexed</div>
        </div>
      </div>`;
  }).join('');
}

// ── File upload ────────────────────────────────────────────────────────────────
browseBtn.addEventListener('click', (e) => { e.stopPropagation(); fileInput.click(); });
uploadZone.addEventListener('click', () => fileInput.click());

uploadZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  uploadZone.classList.add('drag-over');
});
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
uploadZone.addEventListener('drop', (e) => {
  e.preventDefault();
  uploadZone.classList.remove('drag-over');
  const files = e.dataTransfer.files;
  if (files.length) uploadFiles(files);
});

fileInput.addEventListener('change', () => {
  if (fileInput.files.length) uploadFiles(fileInput.files);
});

async function uploadFiles(files) {
  setStatus('loading', 'Uploading…');
  showProgress(0, 'Uploading files…');

  const formData = new FormData();
  for (const f of files) formData.append('files', f);

  try {
    showProgress(25, 'Transferring…');
    const res  = await fetch('/api/upload', { method: 'POST', body: formData });
    showProgress(55, 'Embedding & indexing…');
    const data = await res.json();

    if (data.status === 'success') {
      const ing = data.ingestion;
      showProgress(100, `✓ ${ing.chunks_created} chunks indexed from ${ing.files_loaded} file(s)`);
      appendSystemMsg(`
        ✓ Ingested <strong>${ing.files_loaded} file(s)</strong> →
        ${ing.pages_extracted} pages →
        <strong>${ing.chunks_created} chunks</strong> →
        ${ing.vectors_stored} vectors stored.
      `);
      await refreshStats();
    } else {
      showProgress(0, '');
      appendSystemMsg(`⚠ Upload error: ${data.message}`, true);
      setStatus('error', 'Upload failed');
    }
  } catch (err) {
    showProgress(0, '');
    appendSystemMsg('⚠ Network error during upload.', true);
    setStatus('error', 'Error');
  } finally {
    setTimeout(() => { progressWrap.style.display = 'none'; }, 3000);
    fileInput.value = '';
  }
}

function showProgress(pct, label) {
  progressWrap.style.display = 'block';
  progressFill.style.width   = `${pct}%`;
  progressLabel.textContent  = label;
}

// ── Re-index & Reset ──────────────────────────────────────────────────────────
reindexBtn.addEventListener('click', async () => {
  setStatus('loading', 'Re-indexing…');
  try {
    const res  = await fetch('/api/ingest', { method: 'POST' });
    const data = await res.json();
    if (data.result?.status === 'success') {
      appendSystemMsg(`↺ Re-indexed: ${data.result.chunks_created} chunks.`);
    } else {
      appendSystemMsg(`⚠ Re-index failed: ${data.result?.message}`, true);
    }
    await refreshStats();
  } catch { setStatus('error', 'Error'); }
});

resetBtn.addEventListener('click', async () => {
  if (!confirm('Clear the entire vector index? Uploaded files are kept.')) return;
  try {
    await fetch('/api/reset', { method: 'POST' });
    appendSystemMsg('✕ Vector index cleared. Upload new documents to continue.');
    await refreshStats();
  } catch { setStatus('error', 'Error'); }
});

// ── Chat input ────────────────────────────────────────────────────────────────
queryInput.addEventListener('input', () => {
  sendBtn.disabled = queryInput.value.trim() === '' || isQuerying;
  // Auto-grow textarea
  queryInput.style.height = 'auto';
  queryInput.style.height = Math.min(queryInput.scrollHeight, 140) + 'px';
});

queryInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    if (!sendBtn.disabled) submitQuery();
  }
});

sendBtn.addEventListener('click', submitQuery);

// ── Submit query ──────────────────────────────────────────────────────────────
async function submitQuery() {
  const question = queryInput.value.trim();
  if (!question || isQuerying) return;

  isQuerying = true;
  sendBtn.disabled = true;
  queryInput.value = '';
  queryInput.style.height = 'auto';
  setStatus('loading', 'Thinking…');

  // Clear context panel
  contextList.innerHTML = '<p class="empty-hint">Retrieving…</p>';
  chunkCount.textContent = '…';

  // Add user message
  appendUserMsg(question);

  // Typing indicator
  const typingId = appendTyping();

  try {
    const res  = await fetch('/api/chat', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ question }),
    });
    const data = await res.json();
    removeElement(typingId);

    if (data.status === 'ok') {
      appendAIMsg(data.answer, data.sources, data.elapsed_seconds);
      renderContext(data.context_chunks || []);
      setStatus('ready', 'Ready');
    } else {
      appendAIMsg(`⚠ ${data.message}`, [], null, true);
      setStatus('error', 'Error');
    }
  } catch (err) {
    removeElement(typingId);
    appendAIMsg('⚠ Could not reach the server. Please check your connection.', [], null, true);
    setStatus('error', 'Error');
  } finally {
    isQuerying = false;
    sendBtn.disabled = queryInput.value.trim() === '';
  }
}

// ── Message builders ──────────────────────────────────────────────────────────
function appendUserMsg(text) {
  const div = document.createElement('div');
  div.className = 'message-group';
  div.innerHTML = `
    <div class="msg-user">
      <div class="bubble">${escHtml(text)}</div>
    </div>`;
  chatWindow.appendChild(div);
  scrollBottom();
}

function appendAIMsg(answer, sources = [], elapsed = null, isError = false) {
  const div = document.createElement('div');
  div.className = 'message-group';

  const sourcesHtml = sources.length
    ? `<div class="sources-tag">${sources.map(s =>
        `<span class="source-pill">📄 ${escHtml(s)}</span>`
      ).join('')}</div>`
    : '';

  const metaHtml = elapsed
    ? `<div class="msg-meta">answered in ${elapsed}s</div>`
    : '';

  div.innerHTML = `
    <div class="msg-ai${isError ? ' msg-error' : ''}">
      <div class="ai-avatar">AI</div>
      <div class="bubble">
        ${formatAnswer(answer)}
        ${sourcesHtml}
      </div>
    </div>
    ${metaHtml}`;
  chatWindow.appendChild(div);
  scrollBottom();
}

function appendSystemMsg(html, isWarning = false) {
  const div = document.createElement('div');
  div.className = 'message-group system-message';
  div.innerHTML = `
    <div class="system-bubble" style="${isWarning ? 'border-color:rgba(255,75,110,.25)' : ''}">
      <div>
        <p class="sys-body" style="${isWarning ? 'color:var(--danger)' : ''}">${html}</p>
      </div>
    </div>`;
  chatWindow.appendChild(div);
  scrollBottom();
}

function appendTyping() {
  const id  = 'typing-' + Date.now();
  const div = document.createElement('div');
  div.id        = id;
  div.className = 'message-group';
  div.innerHTML = `
    <div class="msg-ai">
      <div class="ai-avatar">AI</div>
      <div class="bubble">
        <div class="typing-indicator">
          <div class="typing-dots"><span></span><span></span><span></span></div>
          <span class="typing-text">Searching documents and generating answer…</span>
        </div>
      </div>
    </div>`;
  chatWindow.appendChild(div);
  scrollBottom();
  return id;
}

// ── Context panel ─────────────────────────────────────────────────────────────
function renderContext(chunks) {
  chunkCount.textContent = `${chunks.length} chunks`;
  if (!chunks.length) {
    contextList.innerHTML = '<p class="empty-hint">No context retrieved.</p>';
    return;
  }
  contextList.innerHTML = chunks.map((c, i) => {
    const src  = (c.metadata?.source || 'unknown').split('/').pop().split('\\').pop();
    const page = c.metadata?.page ?? '?';
    return `
      <div class="context-card">
        <div class="context-card-header">
          <span class="chunk-label">Chunk ${i + 1}</span>
          <span class="chunk-src" title="${escHtml(src)} p.${page}">${escHtml(src)} p.${page}</span>
        </div>
        <p class="context-text">${escHtml(c.content || '')}</p>
      </div>`;
  }).join('');
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function scrollBottom() {
  requestAnimationFrame(() => {
    chatWindow.scrollTop = chatWindow.scrollHeight;
  });
}

function removeElement(id) {
  const el = $(id);
  if (el) el.remove();
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatAnswer(text) {
  // Convert newlines to paragraphs, bold **text**
  return text
    .split(/\n{2,}/)
    .map(p => `<p>${p
      .replace(/\n/g, '<br>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    }</p>`)
    .join('');
}

// ── Init ──────────────────────────────────────────────────────────────────────
setStatus('loading', 'Connecting…');
refreshStats();
setInterval(refreshStats, 15000);  // Refresh stats every 15s