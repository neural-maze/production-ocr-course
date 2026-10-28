const state = {
  currentDoc: null,
  currentPageIdx: 0,
  activeTab: "blocks",
  activeBoxIdx: null,
  pollTimer: null,
  batchesExpanded: false,
};

const LABEL_PALETTE = {
  title: { hex: "#2563eb", bg: "#dbeafe" },
  doc_title: { hex: "#2563eb", bg: "#dbeafe" },
  section_header: { hex: "#4f46e5", bg: "#e0e7ff" },
  sub_title: { hex: "#4f46e5", bg: "#e0e7ff" },
  heading: { hex: "#4f46e5", bg: "#e0e7ff" },
  table: { hex: "#059669", bg: "#d1fae5" },
  cross_table: { hex: "#059669", bg: "#d1fae5" },
  append_table: { hex: "#059669", bg: "#d1fae5" },
  formula: { hex: "#d97706", bg: "#fef3c7" },
  equation: { hex: "#d97706", bg: "#fef3c7" },
  figure: { hex: "#9333ea", bg: "#f3e8ff" },
  image: { hex: "#9333ea", bg: "#f3e8ff" },
  chart: { hex: "#9333ea", bg: "#f3e8ff" },
  list_item: { hex: "#0891b2", bg: "#cffafe" },
  header: { hex: "#64748b", bg: "#f1f5f9" },
  footer: { hex: "#64748b", bg: "#f1f5f9" },
  page_number: { hex: "#64748b", bg: "#f1f5f9" },
  footnote: { hex: "#e11d48", bg: "#ffe4e6" },
  default: { hex: "#0284c7", bg: "#e0f2fe" },
};

function styleForLabel(label) {
  const clean = (label || "text").toLowerCase().replace(/^(cross_|append_)/, "");
  return LABEL_PALETTE[clean] || LABEL_PALETTE[label] || LABEL_PALETTE.default;
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function fetchSystemInfo() {
  try {
    const res = await fetch("/api/system");
    if (!res.ok) return;
    const info = await res.json();
    const shortModel = (info.model_id || "").split("/").pop() || info.model_id;
    document.getElementById("badge-backend").textContent = `${info.backend.toUpperCase()} · ${info.platform}`;
    document.getElementById("badge-model").textContent = shortModel;
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
    sel.innerHTML = `<option value="">— Select a document (${docs.length}) —</option>`;
    for (const d of docs) {
      const opt = document.createElement("option");
      opt.value = d.id;
      const pagesTxt = d.total_pages ? `${d.total_pages}p` : "...";
      const tpsTxt = d.avg_generation_tps > 0 ? ` · ${d.avg_generation_tps.toFixed(1)} tok/s` : "";
      opt.textContent = `[${d.status.toUpperCase()}] ${d.filename} (${pagesTxt}, b=${d.batch_size}${tpsTxt})`;
      sel.appendChild(opt);
    }
    if (currentVal) {
      sel.value = currentVal;
    } else if (docs.length > 0 && !state.currentDoc) {
      sel.value = docs[0].id;
      loadAndRenderDocument(docs[0].id);
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
      state.activeBoxIdx = null;
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
    `Pages: ${doc.total_pages || 0} · Batches: ${doc.batches_completed}/${doc.batches_total} (b=${doc.batch_size})`;

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
  const toggleBtn = document.getElementById("toggle-batches-btn");
  const tbody = document.getElementById("batches-tbody");

  if (!doc.batches || doc.batches.length === 0) {
    section.classList.add("hidden");
    toggleBtn.classList.add("hidden");
    return;
  }

  toggleBtn.classList.remove("hidden");
  toggleBtn.textContent = state.batchesExpanded
    ? `Hide Batch Breakdown (${doc.batches.length}) ▴`
    : `Show Batch Breakdown (${doc.batches.length}) ▾`;

  section.classList.toggle("hidden", !state.batchesExpanded);
  tbody.innerHTML = "";

  for (const b of doc.batches) {
    const tr = document.createElement("tr");
    const pageRange = b.start_page === b.end_page ? `Page ${b.start_page}` : `Pages ${b.start_page}–${b.end_page}`;
    tr.innerHTML = `
      <td><code>ocr-batch-${b.batch_index}</code></td>
      <td>${pageRange}</td>
      <td>${b.num_pages}</td>
      <td>${(b.latency_ms / 1000).toFixed(2)}s</td>
      <td>${b.prompt_tokens.toLocaleString()}</td>
      <td>${b.prompt_tps > 0 ? b.prompt_tps.toFixed(1) + " tok/s" : "—"}</td>
      <td>${b.generation_tokens.toLocaleString()}</td>
      <td><strong>${b.generation_tps > 0 ? b.generation_tps.toFixed(1) + " tok/s" : "—"}</strong></td>
      <td>${b.peak_memory_gb > 0 ? b.peak_memory_gb.toFixed(2) + " GB" : "—"}</td>
    `;
    tbody.appendChild(tr);
  }
}

function setActiveBox(idx, { scrollCard = false, scrollSvg = false } = {}) {
  state.activeBoxIdx = idx;

  document.querySelectorAll(".bbox-group").forEach((el) => {
    const isMatch = Number(el.dataset.idx) === idx;
    el.classList.toggle("active", isMatch);
    if (isMatch && scrollSvg) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });

  document.querySelectorAll(".block-card").forEach((el) => {
    const isMatch = Number(el.dataset.idx) === idx;
    el.classList.toggle("active", isMatch);
    if (isMatch && scrollCard) {
      el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });
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
    bboxes.forEach((box, idx) => {
      const palette = styleForLabel(box.label);
      const w = Math.max(6, box.xmax - box.xmin);
      const h = Math.max(6, box.ymax - box.ymin);

      const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
      g.setAttribute("class", `bbox-group${state.activeBoxIdx === idx ? " active" : ""}`);
      g.dataset.idx = String(idx);

      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", String(box.xmin));
      rect.setAttribute("y", String(box.ymin));
      rect.setAttribute("width", String(w));
      rect.setAttribute("height", String(h));
      rect.setAttribute("stroke", palette.hex);
      rect.setAttribute("fill", palette.hex);
      rect.setAttribute("rx", "4");
      rect.setAttribute("class", "bbox-rect");
      g.appendChild(rect);

      // Numbered tag badge on top-left corner of bounding box
      const tagText = `#${idx + 1}`;
      const tagW = Math.max(30, tagText.length * 12 + 10);
      const tagH = 24;
      const tagY = Math.max(0, box.ymin - tagH);

      const tagBg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      tagBg.setAttribute("x", String(box.xmin));
      tagBg.setAttribute("y", String(tagY));
      tagBg.setAttribute("width", String(tagW));
      tagBg.setAttribute("height", String(tagH));
      tagBg.setAttribute("rx", "4");
      tagBg.setAttribute("fill", palette.hex);
      tagBg.setAttribute("class", "bbox-tag-bg");
      g.appendChild(tagBg);

      const textEl = document.createElementNS("http://www.w3.org/2000/svg", "text");
      textEl.setAttribute("x", String(box.xmin + 6));
      textEl.setAttribute("y", String(tagY + 17));
      textEl.setAttribute("class", "bbox-tag-text");
      textEl.textContent = tagText;
      g.appendChild(textEl);

      g.addEventListener("mouseenter", () => {
        setActiveBox(idx, { scrollCard: true });
      });

      g.addEventListener("click", (e) => {
        e.stopPropagation();
        if (state.activeTab !== "blocks") {
          switchTab("blocks");
        }
        setActiveBox(idx, { scrollCard: true });
      });

      svg.appendChild(g);
    });
  }
}

function renderOutputPane() {
  const doc = state.currentDoc;
  const blocksEl = document.getElementById("tab-blocks");
  const renderedEl = document.getElementById("tab-rendered");
  const rawTextarea = document.getElementById("raw-textarea");
  const currentPageOnly = document.getElementById("scope-current-page").checked;

  if (!doc) return;

  const page = doc.pages && doc.pages[state.currentPageIdx] ? doc.pages[state.currentPageIdx] : null;
  const bboxes = page && page.bboxes ? page.bboxes : [];

  // 1. Render Grounded Box Cards for the current page
  if (bboxes.length === 0) {
    blocksEl.innerHTML = `
      <div class="empty-state">
        <div class="empty-illustration">${doc.status === "done" ? "📄" : "⏳"}</div>
        <div class="empty-title">${
          doc.status === "done" ? "No Grounded Boxes on This Page" : "Extracting Grounded Layout Blocks..."
        }</div>
        <div class="empty-desc">
          ${
            page && page.markdown
              ? "Switch to 'Rendered Doc' or 'Complete Raw' to view page content."
              : "Boxes will appear here automatically as each batch completes."
          }
        </div>
      </div>
    `;
  } else {
    blocksEl.innerHTML = "";
    bboxes.forEach((box, idx) => {
      const palette = styleForLabel(box.label);
      const card = document.createElement("div");
      card.className = `block-card${state.activeBoxIdx === idx ? " active" : ""}`;
      card.style.borderLeftColor = palette.hex;
      card.dataset.idx = String(idx);

      const contentText = (box.text || "").trim();
      const renderedContent = contentText
        ? window.marked
          ? window.marked.parse(contentText)
          : `<p>${escapeHtml(contentText)}</p>`
        : `<span style="color: var(--text-muted); font-style: italic;">(${escapeHtml(box.label)} region)</span>`;

      card.innerHTML = `
        <div class="block-card-header">
          <div class="block-meta-left">
            <span class="block-index" style="background: ${palette.hex};">#${idx + 1}</span>
            <span class="block-label-pill" style="background: ${palette.bg}; color: ${palette.hex};">
              ${escapeHtml(box.label)}
            </span>
            <span class="block-coords">[${box.xmin}, ${box.ymin}, ${box.xmax}, ${box.ymax}]</span>
          </div>
          <button type="button" class="btn-copy-mini" data-copy-idx="${idx}">Copy box</button>
        </div>
        <div class="block-card-body">${renderedContent}</div>
      `;

      card.addEventListener("mouseenter", () => {
        setActiveBox(idx, { scrollSvg: false });
      });

      card.addEventListener("click", () => {
        setActiveBox(idx, { scrollSvg: true });
      });

      const copyBtn = card.querySelector(".btn-copy-mini");
      copyBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const rawBlock = `<BLOCK>${box.label}[${box.xmin},${box.ymin},${box.xmax},${box.ymax}]<FORK>${box.text || ""}`;
        await navigator.clipboard.writeText(box.text || rawBlock);
        copyBtn.textContent = "✓ Copied";
        setTimeout(() => {
          copyBtn.textContent = "Copy box";
        }, 1100);
      });

      blocksEl.appendChild(card);
    });
  }

  // 2. Render Clean Markdown Tab
  let mdText = doc.markdown || "";
  if (!mdText && doc.pages && doc.pages.length > 0) {
    mdText = doc.pages.map((p) => p.markdown).filter(Boolean).join("\n\n---\n\n");
  }
  if (mdText.trim()) {
    renderedEl.innerHTML = window.marked ? window.marked.parse(mdText) : escapeHtml(mdText);
  } else {
    renderedEl.innerHTML = `<div class="empty-state"><div class="empty-desc">Waiting for extracted Markdown...</div></div>`;
  }

  // 3. Render Complete Raw Output Tab
  let rawText = doc.raw_output || "";
  if (currentPageOnly && page) {
    rawText = page.raw_text || "";
  } else if (!rawText && doc.pages && doc.pages.length > 0) {
    rawText = doc.pages.map((p) => `<PAGE>${p.raw_text || ""}`).join("\n");
  }
  rawTextarea.value = rawText || "";
}

function switchTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabName);
  });
  document.getElementById("tab-blocks").classList.toggle("hidden", tabName !== "blocks");
  document.getElementById("tab-rendered").classList.toggle("hidden", tabName !== "rendered");
  document.getElementById("tab-raw").classList.toggle("hidden", tabName !== "raw");
}

function setupSplitResizer() {
  const workspace = document.getElementById("split-workspace");
  const leftPane = document.getElementById("left-pane");
  const rightPane = document.getElementById("right-pane");
  const resizer = document.getElementById("split-resizer");

  let isDragging = false;

  const onMove = (clientX) => {
    if (!isDragging) return;
    const rect = workspace.getBoundingClientRect();
    const offsetX = clientX - rect.left;
    const pct = Math.max(22, Math.min(78, (offsetX / rect.width) * 100));
    leftPane.style.width = `${pct}%`;
    rightPane.style.width = `${100 - pct}%`;
  };

  resizer.addEventListener("mousedown", (e) => {
    e.preventDefault();
    isDragging = true;
    resizer.classList.add("dragging");
    document.body.style.cursor = "col-resize";
  });

  window.addEventListener("mousemove", (e) => onMove(e.clientX));

  window.addEventListener("mouseup", () => {
    if (isDragging) {
      isDragging = false;
      resizer.classList.remove("dragging");
      document.body.style.cursor = "";
    }
  });

  // Touch support
  resizer.addEventListener(
    "touchstart",
    () => {
      isDragging = true;
      resizer.classList.add("dragging");
    },
    { passive: true }
  );
  window.addEventListener(
    "touchmove",
    (e) => {
      if (isDragging && e.touches[0]) {
        onMove(e.touches[0].clientX);
      }
    },
    { passive: true }
  );
  window.addEventListener("touchend", () => {
    isDragging = false;
    resizer.classList.remove("dragging");
  });
}

async function flashButtonCopied(btn, label = "✓ Copied!") {
  const orig = btn.innerHTML;
  btn.innerHTML = label;
  setTimeout(() => {
    btn.innerHTML = orig;
  }, 1300);
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

  document.getElementById("toggle-batches-btn").addEventListener("click", () => {
    state.batchesExpanded = !state.batchesExpanded;
    if (state.currentDoc) {
      renderBatchesTable(state.currentDoc);
    }
  });

  document.getElementById("prev-page-btn").addEventListener("click", () => {
    if (state.currentPageIdx > 0) {
      state.currentPageIdx--;
      state.activeBoxIdx = null;
      renderPageInspector();
      renderOutputPane();
    }
  });

  document.getElementById("next-page-btn").addEventListener("click", () => {
    if (state.currentDoc && state.currentPageIdx < state.currentDoc.pages.length - 1) {
      state.currentPageIdx++;
      state.activeBoxIdx = null;
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
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  document.getElementById("copy-raw-btn").addEventListener("click", async (e) => {
    const doc = state.currentDoc;
    if (!doc) return;
    let raw = doc.raw_output || "";
    if (!raw && doc.pages && doc.pages.length > 0) {
      raw = doc.pages.map((p) => `<PAGE>${p.raw_text || ""}`).join("\n");
    }
    await navigator.clipboard.writeText(raw);
    flashButtonCopied(e.currentTarget, "✓ Copied Complete Raw!");
  });

  document.getElementById("copy-md-btn").addEventListener("click", async (e) => {
    const doc = state.currentDoc;
    if (!doc) return;
    let md = doc.markdown || "";
    if (!md && doc.pages && doc.pages.length > 0) {
      md = doc.pages.map((p) => p.markdown).filter(Boolean).join("\n\n---\n\n");
    }
    await navigator.clipboard.writeText(md);
    flashButtonCopied(e.currentTarget, "✓ Copied Markdown!");
  });

  setupSplitResizer();
}

document.addEventListener("DOMContentLoaded", async () => {
  setupEventListeners();
  await fetchSystemInfo();
  await fetchDocumentHistory();
});
