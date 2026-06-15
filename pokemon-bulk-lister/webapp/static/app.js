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
  const badge = $("#tab-review-count");
  if (badge) {
    const n = stats.flagged ?? 0;
    badge.textContent = n;
    badge.classList.toggle("hidden", !n);
  }
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
  // Reset + load the catalog price-history chart for this card.
  $("#catalog-q").value = "";
  $("#catalog-results").classList.add("hidden");
  loadHistory(card.tcgplayer_product_id);
  $("#modal").classList.remove("hidden");
}

// ---------------------------------------------------------------- price-history chart
async function loadHistory(catalogId, opts = {}) {
  const chartSel = opts.chartSel || "#modal-chart";
  const metaSel = opts.metaSel || "#chart-meta";
  const el = $(chartSel);
  const meta = $(metaSel);
  if (!catalogId) {
    el.innerHTML = `<span class="muted">no catalog match yet — price this card to start its history</span>`;
    if (meta) meta.textContent = "";
    return;
  }
  el.innerHTML = `<span class="muted">loading…</span>`;
  try {
    const data = await api(`/api/catalog/${encodeURIComponent(catalogId)}/history`);
    renderChart(data.points || [], { chartSel, metaSel });
  } catch {
    el.innerHTML = `<span class="muted">history unavailable</span>`;
  }
}

function renderChart(points, opts = {}) {
  const el = $(opts.chartSel || "#modal-chart");
  const meta = $(opts.metaSel || "#chart-meta");
  if (!points.length) {
    el.innerHTML = `<span class="muted">no history yet — price this card to start it</span>`;
    if (meta) meta.textContent = "";
    return;
  }
  // Prefer the aggregated 'final' series; fall back to TCG market if sparse.
  let label = "final price";
  let series = points.filter(p => p.source === "final");
  if (series.length < 2) {
    const tcg = points.filter(p => p.source === "tcgplayer_market");
    if (tcg.length > series.length) { series = tcg; label = "TCG market"; }
  }
  series = series
    .map(p => ({ t: new Date(p.captured_at.replace(" ", "T") + "Z").getTime(), v: p.price }))
    .filter(p => !isNaN(p.t))
    .sort((a, b) => a.t - b.t);

  const latest = series.length ? series[series.length - 1].v : null;
  if (meta) meta.textContent = latest != null ? `· ${label}, latest $${latest.toFixed(2)} · ${series.length} pt(s)` : "";

  if (series.length < 2) {
    el.innerHTML = `<span class="muted">${latest != null ? `$${latest.toFixed(2)} — price again to chart a trend` : "no points"}</span>`;
    return;
  }
  el.innerHTML = svgLine(series);
}

// Shared inline SVG line chart from a sorted [{t, v}] series.
function svgLine(series, color) {
  const W = 480, H = 76, pad = 6;
  const ts = series.map(s => s.t), vs = series.map(s => s.v);
  const tmin = Math.min(...ts), tmax = Math.max(...ts);
  const vmin = Math.min(...vs), vmax = Math.max(...vs);
  const x = t => pad + (tmax === tmin ? 0 : (t - tmin) / (tmax - tmin)) * (W - 2 * pad);
  const y = v => H - pad - (vmax === vmin ? 0.5 : (v - vmin) / (vmax - vmin)) * (H - 2 * pad);
  const poly = series.map(s => `${x(s.t).toFixed(1)},${y(s.v).toFixed(1)}`).join(" ");
  const last = series[series.length - 1];
  const c = color || (vs[vs.length - 1] >= vs[0] ? "#15803d" : "#b00020");
  return `
    <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none">
      <polyline points="${poly}" fill="none" stroke="${c}" stroke-width="2" />
      <circle cx="${x(last.t).toFixed(1)}" cy="${y(last.v).toFixed(1)}" r="3" fill="${c}" />
    </svg>
    <div class="chart-axis muted small"><span>$${vmin.toFixed(2)}</span><span>$${vmax.toFixed(2)}</span></div>`;
}

// ---------------------------------------------------------------- catalog search (in modal)
function setupCatalogSearch() {
  const input = $("#catalog-q");
  const box = $("#catalog-results");
  if (!input) return;
  let timer = null;

  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { box.classList.add("hidden"); box.innerHTML = ""; return; }
    timer = setTimeout(async () => {
      try {
        const data = await api(`/api/catalog/search?q=${encodeURIComponent(q)}`);
        const rows = (data.results || []).slice(0, 8);
        if (!rows.length) { box.innerHTML = `<div class="muted small pad">no matches</div>`; }
        else {
          box.innerHTML = rows.map(r => `
            <div class="catalog-hit" data-id="${escapeHtml(r.id)}"
                 data-name="${escapeHtml(r.name || "")}" data-set="${escapeHtml(r.set_name || "")}"
                 data-setid="${escapeHtml(r.set_id || "")}" data-number="${escapeHtml(r.number || "")}">
              ${r.image_small ? `<img src="${escapeHtml(r.image_small)}" alt="" />` : ""}
              <span><strong>${escapeHtml(r.name || "")}</strong>
              <span class="muted small">${escapeHtml(r.set_name || "")} · #${escapeHtml(r.number || "")}</span></span>
            </div>`).join("");
        }
        box.classList.remove("hidden");
      } catch { box.classList.add("hidden"); }
    }, 300);
  });

  box.addEventListener("click", ev => {
    const hit = ev.target.closest(".catalog-hit");
    if (!hit) return;
    const form = $("#edit-form");
    form.name.value = hit.dataset.name;
    form.set_name.value = hit.dataset.set;
    form.set_code.value = hit.dataset.setid;
    form.card_number.value = hit.dataset.number;
    box.classList.add("hidden");
    input.value = "";
    loadHistory(hit.dataset.id);
  });
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
        loadPortfolio();   // a run records a portfolio snapshot — refresh the chart
        $("#run-pricing-btn").disabled = false;
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

// ---------------------------------------------------------------- invites (admin)
function setupInvite() {
  const btn = $("#invite-btn");
  if (!btn) return;  // non-admins don't see the button
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const data = await api("/api/invites", { method: "POST", body: JSON.stringify({ role: "member" }) });
      // Offer the redeem URL for copy/paste.
      window.prompt("Single-use invite link — send it to the new user:", data.url);
    } catch (err) {
      alert(`Could not create invite: ${err.message}`);
    } finally {
      btn.disabled = false;
    }
  });
}

// ---------------------------------------------------------------- tabs / views
function switchView(view) {
  state.view = view;
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === view));
  const showCollection = (view === "collection" || view === "review");
  $("#view-collection").classList.toggle("hidden", !showCollection);
  $("#view-catalog").classList.toggle("hidden", view !== "catalog");
  $("#view-publish").classList.toggle("hidden", view !== "publish");

  if (showCollection) {
    // Review is Collection pre-filtered to flagged cards; hide the upload +
    // portfolio panels so it reads as a focused queue.
    const review = view === "review";
    $("#upload-panel").classList.toggle("hidden", review);
    $("#portfolio-panel").classList.toggle("hidden", review);
    state.needsReview = review;
    const cb = $("#filter-review");
    if (cb) cb.checked = review;
    loadCards();
    if (!review) loadPortfolio();
  } else if (view === "catalog") {
    loadWatchlist();
  }
}

function setupTabs() {
  $("#tabs").addEventListener("click", ev => {
    const tab = ev.target.closest(".tab");
    if (tab) switchView(tab.dataset.view);
  });
}

// ---------------------------------------------------------------- portfolio value
async function loadPortfolio() {
  try {
    const data = await api("/api/portfolio/history");
    $("#portfolio-value").textContent = `$${(data.current?.total_value || 0).toFixed(2)}`;
    const series = (data.snapshots || [])
      .map(s => ({ t: new Date(s.captured_at.replace(" ", "T") + "Z").getTime(), v: s.total_value }))
      .filter(s => !isNaN(s.t))
      .sort((a, b) => a.t - b.t);
    const el = $("#portfolio-chart");
    if (series.length < 2) {
      el.innerHTML = `<span class="muted">${series.length ? "run pricing again to chart value over time" : "no snapshots yet — run pricing to record one"}</span>`;
    } else {
      el.innerHTML = svgLine(series, "#2563eb");
    }
  } catch {}
}

// ---------------------------------------------------------------- catalog browse
let catBrowseSelected = null;

function selectCatalogCard(id, name, set) {
  catBrowseSelected = id;
  $("#cat-detail-title").innerHTML = `<strong>${escapeHtml(name)}</strong> <span class="muted">${escapeHtml(set)}</span>`;
  $("#cat-watch-btn").classList.remove("hidden");
  loadHistory(id, { chartSel: "#cat-detail-chart", metaSel: "#cat-chart-meta" });
}

function setupCatalogBrowse() {
  const input = $("#cat-browse-q");
  const results = $("#cat-browse-results");
  if (!input) return;
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) return;
    timer = setTimeout(async () => {
      results.innerHTML = `<div class="muted pad">searching…</div>`;
      try {
        const data = await api(`/api/catalog/search?q=${encodeURIComponent(q)}`);
        const rows = data.results || [];
        if (!rows.length) { results.innerHTML = `<div class="muted pad">no matches</div>`; return; }
        results.innerHTML = rows.map(r => `
          <div class="cat-card" data-id="${escapeHtml(r.id)}" data-name="${escapeHtml(r.name || "")}" data-set="${escapeHtml(r.set_name || "")}">
            ${r.image_small ? `<img src="${escapeHtml(r.image_small)}" alt="" loading="lazy" />` : `<div class="noimg"></div>`}
            <div class="cat-card-name">${escapeHtml(r.name || "")}</div>
            <div class="muted small">${escapeHtml(r.set_name || "")} · #${escapeHtml(r.number || "")}</div>
          </div>`).join("");
      } catch { results.innerHTML = `<div class="muted pad">search failed</div>`; }
    }, 300);
  });
  results.addEventListener("click", ev => {
    const card = ev.target.closest(".cat-card");
    if (card) selectCatalogCard(card.dataset.id, card.dataset.name, card.dataset.set);
  });
  $("#cat-watch-btn").addEventListener("click", async () => {
    if (!catBrowseSelected) return;
    const btn = $("#cat-watch-btn");
    btn.disabled = true;
    try {
      await api("/api/watchlist", { method: "POST", body: JSON.stringify({ catalog_id: catBrowseSelected }) });
      await loadWatchlist();
      btn.textContent = "★ Added";
      setTimeout(() => { btn.textContent = "★ Add to watchlist"; }, 1200);
    } catch (err) { alert(err.message); } finally { btn.disabled = false; }
  });
}

// ---------------------------------------------------------------- watchlist
async function loadWatchlist() {
  const el = $("#watchlist-body");
  try {
    const data = await api("/api/watchlist");
    const items = data.watchlist || [];
    if (!items.length) {
      el.innerHTML = `<div class="muted pad">No watched cards yet. Search the catalog above and add some.</div>`;
      return;
    }
    el.innerHTML = `<table class="watch-table"><thead><tr><th></th><th>Card</th><th>Set</th><th>Latest</th><th></th></tr></thead><tbody>` +
      items.map(w => {
        const price = w.latest_final ?? w.latest_tcg;
        return `<tr data-id="${escapeHtml(w.id)}" data-name="${escapeHtml(w.name || "")}" data-set="${escapeHtml(w.set_name || "")}">
          <td class="thumb">${w.image_small ? `<img src="${escapeHtml(w.image_small)}" alt="" loading="lazy" />` : ""}</td>
          <td><strong>${escapeHtml(w.name || "")}</strong> <span class="muted small">#${escapeHtml(w.number || "")}</span></td>
          <td class="muted small">${escapeHtml(w.set_name || "")}</td>
          <td class="price">${price != null ? `$${price.toFixed(2)}` : "—"}</td>
          <td><button data-act="unwatch" class="link-btn">remove</button></td>
        </tr>`;
      }).join("") + `</tbody></table>`;
  } catch { el.innerHTML = `<div class="muted pad">could not load watchlist</div>`; }
}

function setupWatchlist() {
  $("#watchlist-body").addEventListener("click", async ev => {
    const unwatch = ev.target.closest("button[data-act='unwatch']");
    const tr = ev.target.closest("tr[data-id]");
    if (!tr) return;
    if (unwatch) {
      try { await api(`/api/watchlist/${encodeURIComponent(tr.dataset.id)}`, { method: "DELETE" }); await loadWatchlist(); }
      catch (err) { alert(err.message); }
    } else {
      selectCatalogCard(tr.dataset.id, tr.dataset.name, tr.dataset.set);
    }
  });
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
setupPublish();
setupInvite();
setupCatalogSearch();
setupTabs();
setupCatalogBrowse();
setupWatchlist();
setupKeyboard();
loadCards();
loadPortfolio();
