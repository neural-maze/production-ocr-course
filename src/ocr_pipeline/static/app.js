const state = {
  currentDoc: null,
  currentPageIdx: 0,
  activeTab: "rendered",
  pollTimer: null,
};

const LABEL_COLORS = {
  title: "#2563eb",
  doc_title: "#2563eb",
  section_header: "#4f46e5",
  sub_title: "#4f46e5",
  heading: "#4f46e5",
  table: "#059669",
  cross_table: "#059669",
  append_table: "#059669",
  formula: "#d97706",
  equation: "#d97706",
  figure: "#9333ea",
  image: "#9333ea",
  chart: "#9333ea",
  header: "#6b7280",
  footer: "#6b7280",
  page_number: "#6b7280",
  default: "#0284c7",
};

function colorForLabel(label) {
  const clean = (label || "text").toLowerCase().replace(/^(cross_|append_)/, "");
  return LABEL_COLORS[clean] || LABEL_COLORS[label] || LABEL_COLORS.default;
}

async function fetchSystemInfo() {
  try {
    const res = await fetch("/api/system");
    if (!res.ok) return;
    const info = await res.json();
    document.getElementById("badge-backend").textContent = `Backend: ${info.backend.toUpperCase()} (${info.platform})`;
    document.getElementById("badge-model").textContent = `Model: ${info.model_id}`;
    document.getElementById("link-inngest").href = info.inngest_url;
    document.getElementById("link-adminer").href = info.adminer_url;
    const select = document.getElementById("batch-size-select");
    if (select && info.default_batch_size) {
      select.value = String(info.default_batch_size);
    }
  } catch (err) {
    console.warn("Could not load system info:", err);
  }
}

async function fetchDocumentHistory(selectDocId = null) {
  try {
    const res = await fetch("/api/documents?limit=25");
    if (!res.ok) return;
    const docs = await res.json();
    const sel = document.getElementById("history-select");
    const currentVal = selectDocId || sel.value;
    sel.innerHTML = `<option value="">— Select a processed document (${docs.length}) —</option>`;
    for (const d of docs) {
      const opt = document.createElement("option");
      opt.value = d.id;
      const pagesTxt = d.total_pages ? `${d.total_pages}p` : "...";
      const tpsTxt = d.avg_generation_tps > 0 ? ` · ${d.avg_generation_tps.toFixed(1)} tok/s` : "";
      opt.textContent = `[${d.status.toUpperCase()}] ${d.filename} (${pagesTxt}, batch=${d.batch_size}${tpsTxt})`;
      sel.appendChild(opt);
    }
    if (currentVal) {
      sel.value = currentVal;
    }
  } catch (err) {
    console.warn("Failed to fetch document history:", err);
  }
}

async function uploadFile(file) {
  if (!file) return;
  const batchSize = parseInt(document.getElementById("batch-size-select").value || "4", 10);
  const formData = new FormData();
  formData.append("file", file);
  formData.append("batch_size", String(batchSize));

  showStatusBanner("queued", file.name, "Uploading document and triggering Inngest workflow...", 5);

  try {
    const res = await fetch("/api/documents", {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(errBody.detail || "Upload failed");
    }
    const data = await res.json();
    await fetchDocumentHistory(data.document_id);
    startPollingDocument(data.document_id);
  } catch (err) {
    showStatusBanner("failed", file.name, `Error: ${err.message}`, 100);
  }
}

function showStatusBanner(status, filename, stepText, progressPct) {
  const banner = document.getElementById("status-banner");
  const pill = document.getElementById("status-pill");
  const fname = document.getElementById("status-filename");
  const step = document.getElementById("status-step");
  const bar = document.getElementById("progress-bar");

  banner.classList.remove("hidden");
  pill.className = `status-pill ${status}`;
  pill.textContent = status;
  fname.textContent = filename || "";
  step.textContent = stepText || "";
  bar.style.width = `${Math.max(0, Math.min(100, progressPct))}%`;
}

function startPollingDocument(docId) {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  loadAndRenderDocument(docId);
  state.pollTimer = setInterval(() => {
    loadAndRenderDocument(docId);
  }, 1000);
}

async function loadAndRenderDocument(docId) {
  if (!docId) return;
  try {
    const res = await fetch(`/api/documents/${docId}`);
    if (!res.ok) return;
    const doc = await res.json();
    const isNewDoc = !state.currentDoc || state.currentDoc.id !== doc.id;
    state.currentDoc = doc;
    if (isNewDoc) {
      state.currentPageIdx = 0;
    }

    let pct = 10;
    if (doc.status === "rasterizing") pct = 20;
    else if (doc.status === "processing") {
      const totalB = Math.max(1, doc.batches_total);
      pct = 25 + Math.round((doc.batches_completed / totalB) * 70);
    } else if (doc.status === "done" || doc.status === "failed") {
      pct = 100;
    }

    showStatusBanner(doc.status, doc.filename, doc.error || doc.current_step, pct);
    renderMetrics(doc);
    renderBatchesTable(doc);
    renderPageInspector();
    renderOutputPane();

    if (doc.status === "done" || doc.status === "failed") {
      if (state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
      fetchDocumentHistory(doc.id);
    }
  } catch (err) {
    console.warn("Error polling document:", err);
  }
}

function renderMetrics(doc) {
  const m = doc.metrics || {};
  const totalSec = m.total_latency_ms > 0 ? `${(m.total_latency_ms / 1000).toFixed(2)}s` : "—";
  document.getElementById("metric-latency").textContent = totalSec;
  document.getElementById("metric-latency-sub").textContent =
    `Rasterize: ${(m.rasterize_ms || 0).toFixed(0)}ms · LLM: ${(m.llm_total_ms || 0).toFixed(0)}ms`;

  document.getElementById("metric-pps").textContent =
    m.pages_per_sec > 0 ? `${m.pages_per_sec.toFixed(2)} p/s` : "—";
  document.getElementById("metric-pps-sub").textContent =
    `Pages: ${doc.total_pages || 0} · Batches: ${doc.batches_completed}/${doc.batches_total} (size=${doc.batch_size})`;

  document.getElementById("metric-gen-tps").textContent =
    m.avg_generation_tps > 0 ? `${m.avg_generation_tps.toFixed(1)} tok/s` : "—";
  document.getElementById("metric-gen-sub").textContent =
    `Generated: ${(m.generation_tokens || 0).toLocaleString()} tokens`;

  document.getElementById("metric-prompt-tps").textContent =
    m.avg_prompt_tps > 0 ? `${m.avg_prompt_tps.toFixed(1)} tok/s` : "—";
  document.getElementById("metric-prompt-sub").textContent =
    `Prompt: ${(m.prompt_tokens || 0).toLocaleString()} tokens`;

  document.getElementById("metric-memory").textContent =
    m.peak_memory_gb > 0 ? `${m.peak_memory_gb.toFixed(2)} GB` : "—";
  document.getElementById("metric-memory-sub").textContent =
    `${doc.backend.toUpperCase()} · ${doc.model_id.split("/").pop()}`;
}

function renderBatchesTable(doc) {
  const section = document.getElementById("batches-section");
  const tbody = document.getElementById("batches-tbody");
  const summary = document.getElementById("batches-summary");

  if (!doc.batches || doc.batches.length === 0) {
    section.classList.add("hidden");
    return;
  }

  section.classList.remove("hidden");
  summary.textContent = `${doc.batches.length} of ${doc.batches_total} multi-page batch step(s) recorded`;
  tbody.innerHTML = "";

  for (const b of doc.batches) {
    const tr = document.createElement("tr");
    const pageRange = b.start_page === b.end_page ? `Page ${b.start_page}` : `Pages ${b.start_page}–${b.end_page}`;
    tr.innerHTML = `
      <td><code>ocr-batch-${b.batch_index}</code></td>
      <td>${pageRange}</td>
      <td>${b.num_pages}</td>
      <td>${(b.latency_ms / 1000).toFixed(2)}s (${b.latency_ms.toFixed(0)}ms)</td>
      <td>${b.prompt_tokens.toLocaleString()}</td>
      <td>${b.prompt_tps > 0 ? b.prompt_tps.toFixed(1) + " tok/s" : "—"}</td>
      <td>${b.generation_tokens.toLocaleString()}</td>
      <td><strong>${b.generation_tps > 0 ? b.generation_tps.toFixed(1) + " tok/s" : "—"}</strong></td>
      <td>${b.peak_memory_gb > 0 ? b.peak_memory_gb.toFixed(2) + " GB" : "—"}</td>
    `;
    tbody.appendChild(tr);
  }
}

function renderPageInspector() {
  const doc = state.currentDoc;
  const emptyState = document.getElementById("empty-page-state");
  const stage = document.getElementById("page-stage");
  const img = document.getElementById("page-image");
  const svg = document.getElementById("bbox-overlay");
  const indicator = document.getElementById("page-indicator");
  const prevBtn = document.getElementById("prev-page-btn");
  const nextBtn = document.getElementById("next-page-btn");
  const countBadge = document.getElementById("bbox-count-badge");
  const showBboxes = document.getElementById("toggle-bboxes").checked;

  if (!doc || !doc.pages || doc.pages.length === 0) {
    emptyState.classList.remove("hidden");
    stage.classList.add("hidden");
    indicator.textContent = "Page 0 / 0";
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    countBadge.textContent = "0 blocks";
    return;
  }

  emptyState.classList.add("hidden");
  stage.classList.remove("hidden");

  const total = doc.pages.length;
  state.currentPageIdx = Math.max(0, Math.min(total - 1, state.currentPageIdx));
  const page = doc.pages[state.currentPageIdx];

  indicator.textContent = `Page ${page.page_number} / ${total}`;
  prevBtn.disabled = state.currentPageIdx <= 0;
  nextBtn.disabled = state.currentPageIdx >= total - 1;

  const bboxes = page.bboxes || [];
  countBadge.textContent = `${bboxes.length} block${bboxes.length === 1 ? "" : "s"}`;

  if (img.dataset.pageKey !== `${doc.id}_${page.page_number}`) {
    img.dataset.pageKey = `${doc.id}_${page.page_number}`;
    img.src = page.image_data_uri;
  }

  svg.innerHTML = "";
  svg.setAttribute("viewBox", "0 0 1000 1000");

  if (showBboxes && bboxes.length > 0) {
    for (const box of bboxes) {
      const color = colorForLabel(box.label);
      const w = Math.max(4, box.xmax - box.xmin);
      const h = Math.max(4, box.ymax - box.ymin);

      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", String(box.xmin));
      rect.setAttribute("y", String(box.ymin));
      rect.setAttribute("width", String(w));
      rect.setAttribute("height", String(h));
      rect.setAttribute("stroke", color);
      rect.setAttribute("fill", color);
      rect.setAttribute("class", "bbox-rect");

      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      const snippet = (box.text || "").slice(0, 140);
      title.textContent = `${box.label} [${box.xmin}, ${box.ymin}, ${box.xmax}, ${box.ymax}]\n${snippet}`;
      rect.appendChild(title);
      svg.appendChild(rect);
    }
  }
}

function renderOutputPane() {
  const doc = state.currentDoc;
  const currentPageOnly = document.getElementById("scope-current-page").checked;
  const renderedEl = document.getElementById("tab-rendered");
  const sourceEl = document.getElementById("tab-source");
  const rawEl = document.getElementById("tab-raw");

  if (!doc) return;

  let mdText = doc.markdown || "";
  let rawText = doc.raw_output || "";

  if (currentPageOnly && doc.pages && doc.pages[state.currentPageIdx]) {
    const p = doc.pages[state.currentPageIdx];
    mdText = p.markdown || "";
    rawText = p.raw_text || "";
  } else if (!mdText && doc.pages && doc.pages.length > 0) {
    mdText = doc.pages.map((p) => p.markdown).filter(Boolean).join("\n\n---\n\n");
    rawText = doc.pages.map((p) => p.raw_text).filter(Boolean).join("\n\n<PAGE>\n\n");
  }

  if (mdText.trim()) {
    renderedEl.innerHTML = window.marked ? window.marked.parse(mdText) : mdText;
  } else {
    renderedEl.innerHTML = `<div class="empty-state">${
      doc.status === "done" ? "No text blocks extracted." : "Waiting for OCR batch output..."
    }</div>`;
  }

  sourceEl.textContent = mdText || "// No Markdown output yet";
  rawEl.textContent = rawText || "// No raw <BLOCK>...<FORK> tokens yet";
}

function setupEventListeners() {
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");

  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
      uploadFile(e.target.files[0]);
      e.target.value = "";
    }
  });

  dropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      uploadFile(e.dataTransfer.files[0]);
    }
  });

  document.getElementById("history-select").addEventListener("change", (e) => {
    if (e.target.value) {
      startPollingDocument(e.target.value);
    }
  });

  document.getElementById("refresh-btn").addEventListener("click", () => {
    fetchDocumentHistory(state.currentDoc ? state.currentDoc.id : null);
  });

  document.getElementById("prev-page-btn").addEventListener("click", () => {
    if (state.currentPageIdx > 0) {
      state.currentPageIdx--;
      renderPageInspector();
      renderOutputPane();
    }
  });

  document.getElementById("next-page-btn").addEventListener("click", () => {
    if (state.currentDoc && state.currentPageIdx < state.currentDoc.pages.length - 1) {
      state.currentPageIdx++;
      renderPageInspector();
      renderOutputPane();
    }
  });

  document.getElementById("toggle-bboxes").addEventListener("change", () => {
    renderPageInspector();
  });

  document.getElementById("scope-current-page").addEventListener("change", () => {
    renderOutputPane();
  });

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.activeTab = btn.dataset.tab;

      document.getElementById("tab-rendered").classList.toggle("hidden", state.activeTab !== "rendered");
      document.getElementById("tab-source").classList.toggle("hidden", state.activeTab !== "source");
      document.getElementById("tab-raw").classList.toggle("hidden", state.activeTab !== "raw");
    });
  });

  document.getElementById("copy-btn").addEventListener("click", async () => {
    const doc = state.currentDoc;
    if (!doc) return;
    const text = state.activeTab === "raw" ? doc.raw_output : doc.markdown;
    await navigator.clipboard.writeText(text || "");
    const btn = document.getElementById("copy-btn");
    const orig = btn.textContent;
    btn.textContent = "Copied!";
    setTimeout(() => (btn.textContent = orig), 1200);
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  setupEventListeners();
  await fetchSystemInfo();
  await fetchDocumentHistory();
});

