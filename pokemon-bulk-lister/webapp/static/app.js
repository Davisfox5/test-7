// Single-page UI logic for the bulk-lister. Vanilla JS, no framework.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = {
  cards: [],
  stats: {},
  sort: "confidence_asc",
  needsReview: false,
  unidentified: false,
  editing: null,
};

// ---------------------------------------------------------------- network
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}

// ---------------------------------------------------------------- render
function renderStats(stats) {
  $("#stats").innerHTML = `
    <span><strong>${stats.total ?? 0}</strong> cards</span>
    <span><strong>${stats.identified ?? 0}</strong> identified</span>
    <span><strong>${stats.priced ?? 0}</strong> priced</span>
    <span><strong>${stats.flagged ?? 0}</strong> flagged</span>
    <span><strong>${stats.uploaded ?? 0}</strong> uploaded</span>
    <span>est. value: <strong>$${(stats.total_value ?? 0).toFixed(2)}</strong></span>
  `;
}

function fmt(v) {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return `$${v.toFixed(2)}`;
  return v;
}

function bestEbay(card) {
  // Prefer Terapeak's 365-day median when present (deeper signal),
  // otherwise fall back to the 30-day MI median.
  if (card.terapeak_median_usd != null && card.terapeak_median_usd > 0) {
    return { value: card.terapeak_median_usd, window: "365d", count: card.terapeak_sold_count_365d };
  }
  if (card.ebay_median_30d != null && card.ebay_median_30d > 0) {
    return { value: card.ebay_median_30d, window: "30d", count: card.ebay_sold_count_30d };
  }
  return { value: null, window: null, count: 0 };
}

function renderRow(card) {
  const flagClass = card.needs_review ? "flag" : "";
  const setBits = [card.set_name, card.card_number ? `#${card.card_number}` : "", card.rarity]
    .filter(Boolean)
    .join(" · ");
  const ebay = bestEbay(card);
  const ebayCell = ebay.value == null
    ? `<span class="muted">—</span>`
    : `${fmt(ebay.value)}<div class="muted small">${ebay.window} · n=${ebay.count}</div>`;
  return `
    <tr class="${flagClass}" data-id="${card.id}">
      <td class="thumb"><img src="/${card.crop_path}" alt="" loading="lazy" /></td>
      <td>
        <strong>${escapeHtml(card.name) || "<span class='muted'>(unidentified)</span>"}</strong>
        ${card.is_holo ? "<span class='muted'> · holo</span>" : ""}
        <div class="muted small">${escapeHtml(card.condition_guess || "")}</div>
      </td>
      <td class="muted small">${escapeHtml(setBits)}</td>
      <td class="price">${fmt(card.tcgplayer_market)}</td>
      <td class="price">${fmt(card.cardmarket_trend_usd)}</td>
      <td class="price">${ebayCell}</td>
      <td class="price"><strong>${fmt(card.final_price)}</strong></td>
      <td class="conf">${(card.pricing_confidence ?? 0).toFixed(2)}</td>
      <td class="actions">
        <button data-act="edit">Edit</button>
        <button data-act="price">Price</button>
      </td>
    </tr>
  `;
}

function renderCards() {
  const body = $("#cards-body");
  if (!state.cards.length) {
    body.innerHTML = `<tr><td colspan="10" class="muted">No cards yet. Upload a grid above.</td></tr>`;
    return;
  }
  body.innerHTML = state.cards.map(renderRow).join("");
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// ---------------------------------------------------------------- data fetch
async function loadCards() {
  const params = new URLSearchParams({ sort: state.sort });
  if (state.needsReview) params.set("needs_review", "1");
  if (state.unidentified) params.set("unidentified", "1");
  const data = await api(`/api/cards?${params}`);
  state.cards = data.cards;
  state.stats = data.stats;
  renderStats(state.stats);
  renderCards();
}

// ---------------------------------------------------------------- upload
function setupDropzone() {
  const dz = $("#dropzone");
  const input = $("#file-input");
  const status = $("#upload-status");

  dz.addEventListener("click", () => input.click());
  ["dragenter", "dragover"].forEach(e => dz.addEventListener(e, ev => {
    ev.preventDefault(); dz.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach(e => dz.addEventListener(e, ev => {
    ev.preventDefault(); dz.classList.remove("drag");
  }));
  dz.addEventListener("drop", ev => uploadFiles(ev.dataTransfer.files));
  input.addEventListener("change", () => uploadFiles(input.files));

  async function uploadFiles(files) {
    if (!files || !files.length) return;
    status.textContent = `Uploading ${files.length} file(s)…`;
    const fd = new FormData();
    for (const f of files) fd.append("file", f);
    try {
      const res = await fetch("/api/grids/upload", { method: "POST", body: fd });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      status.textContent = `Added ${data.total_crops} crop(s) from ${data.grids.length} grid(s).`;
      await loadCards();
    } catch (err) {
      status.textContent = `Upload failed: ${err.message}`;
    } finally {
      input.value = "";
    }
  }
}

// ---------------------------------------------------------------- table interactions
function setupTable() {
  $("#cards-body").addEventListener("click", async ev => {
    const btn = ev.target.closest("button");
    if (!btn) return;
    const tr = ev.target.closest("tr");
    const id = parseInt(tr.dataset.id, 10);
    if (btn.dataset.act === "edit") openEdit(id);
    else if (btn.dataset.act === "price") {
      btn.disabled = true; btn.textContent = "…";
      try {
        await api(`/api/cards/${id}/price`, { method: "POST" });
        await loadCards();
      } catch (err) {
        alert(err.message);
      } finally {
        btn.disabled = false; btn.textContent = "Price";
      }
    }
  });
}

// ---------------------------------------------------------------- modals
function openEdit(id) {
  const card = state.cards.find(c => c.id === id);
  if (!card) return;
  state.editing = id;
  $("#modal-title").textContent = `Edit · ${card.name || "(unidentified)"}`;
  $("#modal-img").src = `/${card.crop_path}`;
  const form = $("#edit-form");
  form.id.value = card.id;
  form.name.value = card.name || "";
  form.set_name.value = card.set_name || "";
  form.set_code.value = card.set_code || "";
  form.card_number.value = card.card_number || "";
  form.rarity.value = card.rarity || "";
  form.condition_guess.value = card.condition_guess || "";
  form.is_holo.checked = !!card.is_holo;
  form.id_confidence.value = card.id_confidence ?? 0;

  $("#modal-prices").innerHTML = `
    <div>TCG market<strong>${fmt(card.tcgplayer_market)}</strong></div>
    <div>CM trend (USD)<strong>${fmt(card.cardmarket_trend_usd)}</strong></div>
    <div>CM trend (EUR)<strong>${fmt(card.cardmarket_trend_eur)}</strong></div>
    <div>eBay median 30d<strong>${fmt(card.ebay_median_30d)}</strong></div>
    <div>eBay max 30d<strong>${fmt(card.ebay_max_30d)}</strong></div>
    <div>Terapeak median<strong>${fmt(card.terapeak_median_usd)}</strong></div>
    <div>eBay sold count<strong>${card.ebay_sold_count_30d || 0}</strong></div>
    <div>Final price<strong>${fmt(card.final_price)}</strong></div>
    <div>Pricing conf.<strong>${(card.pricing_confidence ?? 0).toFixed(2)}</strong></div>
    <div>Outlier<strong>${card.outlier_flag ? "yes" : "no"}</strong></div>
    <div>Image URL<strong>${card.image_url ? "uploaded" : "—"}</strong></div>
    <div>Notes<strong>${escapeHtml(card.pricing_notes || "")}</strong></div>
  `;
  $("#modal").classList.remove("hidden");
}

function closeEdit() { $("#modal").classList.add("hidden"); state.editing = null; }

async function saveEdit({ priceAfter = false } = {}) {
  if (!state.editing) return;
  const form = $("#edit-form");
  const saveBtn = form.querySelector('button[type="submit"]');
  const priceBtn = $("#modal-price-btn");
  const activeBtn = priceAfter ? priceBtn : saveBtn;
  const originalLabel = activeBtn.textContent;
  const allBtns = [saveBtn, priceBtn, $("#modal-upload-btn"), $("#modal-close")];
  allBtns.forEach(b => b.disabled = true);
  activeBtn.textContent = priceAfter ? "Saving…" : "Saving…";

  const patch = {
    name: form.name.value.trim(),
    set_name: form.set_name.value.trim(),
    set_code: form.set_code.value.trim(),
    card_number: form.card_number.value.trim(),
    rarity: form.rarity.value.trim(),
    condition_guess: form.condition_guess.value,
    is_holo: form.is_holo.checked,
    id_confidence: parseFloat(form.id_confidence.value) || 0,
  };
  try {
    await api(`/api/cards/${state.editing}`, { method: "PATCH", body: JSON.stringify(patch) });
    if (priceAfter) {
      activeBtn.textContent = "Pricing…";
      const updated = await api(`/api/cards/${state.editing}/price`, { method: "POST" });
      // Refresh the modal body with the new prices before closing.
      const idx = state.cards.findIndex(c => c.id === updated.id);
      if (idx >= 0) state.cards[idx] = updated;
      activeBtn.textContent = `Priced $${(updated.final_price ?? 0).toFixed(2)}`;
      await new Promise(r => setTimeout(r, 600));   // brief flash so user sees the new price
    } else {
      activeBtn.textContent = "Saved ✓";
      await new Promise(r => setTimeout(r, 350));
    }
    await loadCards();
    closeEdit();
  } catch (err) {
    activeBtn.textContent = originalLabel;
    alert(`Failed: ${err.message}`);
  } finally {
    allBtns.forEach(b => b.disabled = false);
    activeBtn.textContent = originalLabel;
  }
}

async function uploadCloudinary() {
  if (!state.editing) return;
  const btn = $("#modal-upload-btn");
  btn.disabled = true; const orig = btn.textContent; btn.textContent = "Uploading…";
  try {
    await api(`/api/cards/${state.editing}/upload-image`, { method: "POST" });
    await loadCards();
    btn.textContent = "Uploaded ✓";
  } catch (err) {
    btn.textContent = orig;
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
}

function setupModal() {
  $("#modal-close").addEventListener("click", closeEdit);
  $("#modal").addEventListener("click", ev => {
    if (ev.target.id === "modal") closeEdit();
  });
  $("#edit-form").addEventListener("submit", ev => { ev.preventDefault(); saveEdit(); });
  $("#modal-price-btn").addEventListener("click", () => saveEdit({ priceAfter: true }));
  $("#modal-upload-btn").addEventListener("click", uploadCloudinary);
}

// ---------------------------------------------------------------- toolbar
function setupToolbar() {
  $("#sort").addEventListener("change", e => { state.sort = e.target.value; loadCards(); });
  $("#filter-review").addEventListener("change", e => { state.needsReview = e.target.checked; loadCards(); });
  $("#filter-unid").addEventListener("change", e => { state.unidentified = e.target.checked; loadCards(); });

  $("#run-pricing-btn").addEventListener("click", async () => {
    const btn = $("#run-pricing-btn");
    btn.disabled = true;
    const useTerapeak = $("#use-terapeak").checked ? "1" : "0";
    try {
      await api(`/api/pricing/run-all?terapeak=${useTerapeak}`, { method: "POST" });
      pollJob();
    } catch (err) {
      alert(err.message);
      btn.disabled = false;
    }
  });

  // One-shot probe of Terapeak login state on page load.
  api("/api/terapeak/status").then(s => {
    const el = $("#terapeak-status");
    if (s.logged_in) {
      el.textContent = "(logged in)";
      el.style.color = "#15803d";
    } else {
      el.textContent = "(first run will prompt login)";
    }
  }).catch(() => {});

  $("#export-csv-btn").addEventListener("click", async () => {
    const btn = $("#export-csv-btn");
    btn.disabled = true; const orig = btn.textContent; btn.textContent = "Generating…";
    try {
      const data = await api("/api/export/csvs", { method: "POST" });
      const links = data.written.map(w => `<a href="/${w.file}" target="_blank">${w.file} (${w.rows})</a>`).join(" · ");
      $("#upload-status").innerHTML = `Wrote ${data.written.length} CSV(s): ${links}`;
    } catch (err) {
      alert(err.message);
    } finally {
      btn.disabled = false; btn.textContent = orig;
    }
  });
}

async function pollJob() {
  const bar = $("#job-bar");
  const fill = $("#progress-fill");
  const msg = $(".job-message");
  bar.classList.remove("hidden");
  const tick = async () => {
    try {
      const status = await api("/api/pricing/status");
      const pct = status.total ? (status.progress / status.total) * 100 : 0;
      fill.style.width = `${pct}%`;
      msg.textContent = status.message || "…";
      if (status.running) setTimeout(tick, 1500);
      else {
        await loadCards();
        $("#run-pricing-btn").disabled = false;
        setTimeout(() => bar.classList.add("hidden"), 1500);
      }
    } catch {
      setTimeout(tick, 2000);
    }
  };
  tick();
}

// ---------------------------------------------------------------- init
function setupKeyboard() {
  document.addEventListener("keydown", ev => {
    if (ev.key === "Escape") {
      closeEdit();
    }
  });
}

setupDropzone();
setupTable();
setupModal();
setupToolbar();
setupKeyboard();
loadCards();
