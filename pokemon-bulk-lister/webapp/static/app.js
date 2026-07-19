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
  selected: new Set(),
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
    <span><strong>${stats.ebay_listed ?? 0}</strong> on eBay</span>
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
  const checked = state.selected.has(card.id) ? "checked" : "";
  return `
    <tr class="${flagClass}" data-id="${card.id}">
      <td class="select"><input type="checkbox" data-act="select" ${checked} /></td>
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
    updateBundleButton();
    return;
  }
  // Drop selections that aren't in the current view.
  const visible = new Set(state.cards.map(c => c.id));
  for (const id of Array.from(state.selected)) {
    if (!visible.has(id)) state.selected.delete(id);
  }
  body.innerHTML = state.cards.map(renderRow).join("");
  syncSelectAllCheckbox();
  updateBundleButton();
}

function syncSelectAllCheckbox() {
  const all = $("#select-all");
  if (!all) return;
  const total = state.cards.length;
  const sel = state.cards.filter(c => state.selected.has(c.id)).length;
  all.checked = total > 0 && sel === total;
  all.indeterminate = sel > 0 && sel < total;
}

function updateBundleButton() {
  const btn = $("#bundle-btn");
  if (!btn) return;
  const n = state.selected.size;
  btn.textContent = `Bundle selected (${n})`;
  btn.disabled = n < 2;
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
    const cb = ev.target.closest("input[type=checkbox][data-act=select]");
    if (cb) {
      const tr = ev.target.closest("tr");
      const id = parseInt(tr.dataset.id, 10);
      if (cb.checked) state.selected.add(id);
      else state.selected.delete(id);
      syncSelectAllCheckbox();
      updateBundleButton();
      return;
    }
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

  $("#select-all").addEventListener("change", ev => {
    if (ev.target.checked) state.cards.forEach(c => state.selected.add(c.id));
    else state.cards.forEach(c => state.selected.delete(c.id));
    renderCards();
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

async function aiIdentify() {
  if (!state.editing) return;
  const btn = $("#modal-identify-btn");
  btn.disabled = true; const orig = btn.textContent; btn.textContent = "Identifying…";
  try {
    const updated = await api(`/api/cards/${state.editing}/identify`, { method: "POST" });
    const form = $("#edit-form");
    form.name.value = updated.name || "";
    form.set_name.value = updated.set_name || "";
    form.set_code.value = updated.set_code || "";
    form.card_number.value = updated.card_number || "";
    form.rarity.value = updated.rarity || "";
    form.condition_guess.value = updated.condition_guess || "";
    form.is_holo.checked = !!updated.is_holo;
    form.id_confidence.value = updated.id_confidence ?? 0;
    const idx = state.cards.findIndex(c => c.id === updated.id);
    if (idx >= 0) state.cards[idx] = updated;
    $("#modal-title").textContent = `Edit · ${updated.name || "(unidentified)"}`;
    btn.textContent = "Identified ✓";
    await new Promise(r => setTimeout(r, 400));
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false; btn.textContent = orig;
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
  $("#modal-identify-btn").addEventListener("click", aiIdentify);
  $("#modal-upload-btn").addEventListener("click", uploadCloudinary);
}

// ---------------------------------------------------------------- bundle modal
function selectedCards() {
  return state.cards.filter(c => state.selected.has(c.id));
}

function bundleSum() {
  return selectedCards().reduce((acc, c) => acc + (c.final_price || 0), 0);
}

function bundlePricePreview() {
  const form = $("#bundle-form");
  if (!form) return 0;
  const discount = form.discount.value;
  const sum = bundleSum();
  if (discount === "custom") {
    return parseFloat(form.price.value) || 0;
  }
  return Math.round(sum * (1 - parseFloat(discount)) * 100) / 100;
}

function refreshBundlePreview() {
  $("#bundle-count").textContent = state.selected.size;
  $("#bundle-sum").textContent = `$${bundleSum().toFixed(2)}`;
  $("#bundle-price-preview").textContent = `$${bundlePricePreview().toFixed(2)}`;
}

function openBundle() {
  if (state.selected.size < 2) return;
  const form = $("#bundle-form");
  form.reset();
  form.discount.value = "0.30";
  form.quantity.value = "1";
  $("#bundle-price-row").classList.add("hidden");
  $("#bundle-status").textContent = "";
  refreshBundlePreview();
  $("#bundle-modal").classList.remove("hidden");
}

function closeBundle() {
  $("#bundle-modal").classList.add("hidden");
}

function setupBundleModal() {
  $("#bundle-close").addEventListener("click", closeBundle);
  $("#bundle-modal").addEventListener("click", ev => {
    if (ev.target.id === "bundle-modal") closeBundle();
  });
  const form = $("#bundle-form");
  form.discount.addEventListener("change", () => {
    const custom = form.discount.value === "custom";
    $("#bundle-price-row").classList.toggle("hidden", !custom);
    if (custom && !form.price.value) {
      form.price.value = (bundleSum() * 0.7).toFixed(2);
    }
    refreshBundlePreview();
  });
  form.price.addEventListener("input", refreshBundlePreview);

  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    const ids = Array.from(state.selected);
    if (ids.length < 2) {
      $("#bundle-status").textContent = "Select at least 2 cards.";
      return;
    }
    const payload = {
      card_ids: ids,
      quantity: parseInt(form.quantity.value, 10) || 1,
    };
    const title = form.title.value.trim();
    if (title) payload.title = title;
    const note = form.note.value.trim();
    if (note) payload.note = note;
    const slug = form.slug.value.trim();
    if (slug) payload.slug = slug;
    if (form.discount.value === "custom") {
      payload.price = parseFloat(form.price.value) || 0;
    } else {
      payload.discount = parseFloat(form.discount.value);
    }

    const submitBtn = form.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    const origLabel = submitBtn.textContent;
    submitBtn.textContent = "Exporting…";
    $("#bundle-status").textContent = "";
    try {
      const data = await api("/api/export/lot", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const links = data.written.map(w => `<a href="/${w.file}" target="_blank">${w.file}</a>`).join(" · ");
      const s = data.summary || {};
      $("#bundle-status").innerHTML =
        `Wrote ${data.written.length} lot CSV(s) — bundle price $${(s.bundle_price ?? 0).toFixed(2)} (sum was $${(s.sum_of_final_prices ?? 0).toFixed(2)}). ${links}`;
      $("#upload-status").innerHTML = `Lot exported: ${links}`;
    } catch (err) {
      $("#bundle-status").textContent = `Failed: ${err.message}`;
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = origLabel;
    }
  });
}

// ---------------------------------------------------------------- toolbar
function setupToolbar() {
  $("#sort").addEventListener("change", e => { state.sort = e.target.value; loadCards(); });
  $("#filter-review").addEventListener("change", e => { state.needsReview = e.target.checked; loadCards(); });
  $("#filter-unid").addEventListener("change", e => { state.unidentified = e.target.checked; loadCards(); });

  $("#identify-btn").addEventListener("click", async () => {
    const btn = $("#identify-btn");
    btn.disabled = true;
    try {
      await api("/api/identify/run-all", { method: "POST" });
      pollJob();
    } catch (err) {
      alert(err.message);
      btn.disabled = false;
    }
  });

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

  $("#bundle-btn").addEventListener("click", openBundle);

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
        const identifyBtn = $("#identify-btn");
        if (identifyBtn) identifyBtn.disabled = false;
        setTimeout(() => bar.classList.add("hidden"), 1500);
      }
    } catch {
      setTimeout(tick, 2000);
    }
  };
  tick();
}

// ---------------------------------------------------------------- publish
function setupPublish() {
  const buttons = {
    ebay: $("#publish-ebay-btn"),
    tcgplayer: $("#publish-tcg-btn"),
    whatnot: $("#publish-whatnot-btn"),
  };

  async function refreshStatus() {
    let s;
    try {
      s = await api("/api/publish/status");
    } catch {
      return;
    }
    for (const site of ["ebay", "tcgplayer", "whatnot"]) {
      const el = $(`#pub-${site}`);
      const ready = s[site] && s[site].ready;
      if (el) {
        el.textContent = ready ? "(ready)" : "(setup needed)";
        el.style.color = ready ? "#15803d" : "#b45309";
        if (!ready && s[site] && s[site].reason) buttons[site].title = s[site].reason;
      }
    }
  }

  async function publish(site) {
    const btn = buttons[site];
    btn.disabled = true;
    const endpoint = site === "ebay" ? "/api/publish/ebay" : "/api/publish/portal";
    const opts = site === "ebay"
      ? { method: "POST" }
      : { method: "POST", body: JSON.stringify({ site }) };
    try {
      await api(endpoint, opts);
      pollPublishJob();
    } catch (err) {
      alert(err.message);
      btn.disabled = false;
    }
  }

  Object.entries(buttons).forEach(([site, btn]) => {
    if (btn) btn.addEventListener("click", () => publish(site));
  });
  refreshStatus();
}

async function pollPublishJob() {
  const bar = $("#publish-bar");
  const fill = $("#publish-progress-fill");
  const msg = $("#publish-bar-msg");
  bar.classList.remove("hidden");
  const tick = async () => {
    try {
      const status = await api("/api/publish/job-status");
      const pct = status.total ? (status.progress / status.total) * 100 : 0;
      fill.style.width = `${pct}%`;
      msg.textContent = `${status.site || "publish"}: ${status.message || "…"}`;
      if (status.running) setTimeout(tick, 1500);
      else {
        await loadCards();
        $("#publish-msg").textContent = status.message || "";
        ["#publish-ebay-btn", "#publish-tcg-btn", "#publish-whatnot-btn"].forEach(s => $(s).disabled = false);
        setTimeout(() => bar.classList.add("hidden"), 2500);
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
      closeBundle();
    }
  });
}

setupDropzone();
setupTable();
setupModal();
setupBundleModal();
setupToolbar();
setupPublish();
setupKeyboard();
loadCards();
