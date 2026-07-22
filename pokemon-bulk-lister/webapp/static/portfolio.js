/* Portfolio dashboard: tiles, value chart, inventory, alerts, eBay actions. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const fmtUsd = (v) =>
  v == null ? "—" : v.toLocaleString("en-US", { style: "currency", currency: "USD" });

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 401) {
    window.location = "/login?next=/portfolio";
    throw new Error("login required");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status}`);
  return data;
}

/* ---------------- summary + inventory ---------------- */

let cardOptions = []; // [{catalog_card_id, name}] for the rule form

async function loadSummary() {
  const data = await api("/api/portfolio/summary");
  const t = data.totals;
  $("#t-value").textContent = fmtUsd(t.value);
  $("#t-cards").textContent = t.card_count;
  $("#t-cards-sub").textContent = `${t.priced_count}/${t.item_count} priced`;
  $("#t-cost").textContent = fmtUsd(t.cost_basis);
  if (t.cost_basis && t.value != null) {
    const gain = t.value - t.cost_basis;
    const el = $("#t-cost-sub");
    el.textContent = `${gain >= 0 ? "+" : ""}${fmtUsd(gain)} unrealized`;
    el.className = "tile-sub " + (gain >= 0 ? "up" : "down");
  }
  $("#alerts-unread").textContent = t.unread_alerts ? `(${t.unread_alerts} unread)` : "";
  const sched = data.scheduler || {};
  $("#t-value-sub").textContent = sched.last_cycle_at
    ? `prices checked ${new Date(sched.last_cycle_at).toLocaleTimeString()}`
    : "";
  renderInventory(data.items);

  cardOptions = [];
  const seen = new Set();
  for (const it of data.items) {
    if (!seen.has(it.catalog_card_id)) {
      seen.add(it.catalog_card_id);
      cardOptions.push({ id: it.catalog_card_id, name: it.name });
    }
  }
  const sel = document.querySelector('#rule-form select[name="catalog_card_id"]');
  sel.innerHTML = '<option value="">All my cards</option>' +
    cardOptions.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (m) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

function renderInventory(items) {
  const body = $("#inv-body");
  if (!items.length) {
    body.innerHTML =
      '<tr><td colspan="8" class="muted">No cards yet — price cards on the ' +
      '<a href="/">processing bench</a>, then click “Import priced bench cards”.</td></tr>';
    return;
  }
  body.innerHTML = items.map((it) => {
    const chg = it.change_7d_pct;
    const chgTxt = chg == null ? "—" : `${chg > 0 ? "+" : ""}${chg}%`;
    const chgCls = chg == null ? "" : chg >= 0 ? "up" : "down";
    const img = it.image_url
      ? `<img class="inv-thumb" src="${esc(it.image_url)}" alt="">`
      : it.crop_path ? `<img class="inv-thumb" src="/${esc(it.crop_path)}" alt="">` : "";
    return `<tr data-id="${it.id}">
      <td>${img}</td>
      <td>
        <div class="inv-name">${esc(it.name)}${it.is_holo ? " ✦" : ""}</div>
        <div class="inv-set">${esc(it.set_name || "")}${it.card_number ? " #" + esc(it.card_number) : ""}</div>
      </td>
      <td>${esc(it.condition)}</td>
      <td class="num">${it.quantity}</td>
      <td class="num">${fmtUsd(it.price)}</td>
      <td class="num"><span class="chg ${chgCls}">${chgTxt}</span></td>
      <td class="num">${fmtUsd(it.value)}</td>
      <td>
        <div class="row-actions">
          <button class="btn act-sell" data-price="${it.price ?? ""}"
                  title="List on the marketplace">Sell</button>
          ${ebayStatus.authorized
            ? `<button class="btn btn-ghost act-ebay" data-price="${it.price ?? ""}"
                  title="List on your eBay account">eBay</button>` : ""}
          <button class="btn btn-ghost act-del" title="Remove from library">✕</button>
        </div>
      </td>
    </tr>`;
  }).join("");
  markListedRows();
}

let activeListingByItem = {}; // inventory_item_id -> listing_id

function markListedRows() {
  document.querySelectorAll("#inv-body tr[data-id]").forEach((row) => {
    const btn = row.querySelector(".act-sell");
    if (!btn) return;
    const lid = activeListingByItem[row.dataset.id];
    if (lid) {
      btn.textContent = "Listed ✓";
      btn.disabled = true;
    } else if (btn.textContent === "Listed ✓") {
      btn.textContent = "Sell";
      btn.disabled = false;
    }
  });
}

$("#inv-body").addEventListener("click", async (e) => {
  const row = e.target.closest("tr[data-id]");
  if (!row) return;
  const id = row.dataset.id;
  if (e.target.classList.contains("act-del")) {
    if (!confirm("Remove this card from your library?")) return;
    await api(`/api/inventory/${id}`, { method: "DELETE" });
    refreshAll();
  } else if (e.target.classList.contains("act-ebay")) {
    listOnEbay(id, e.target.dataset.price, e.target);
  } else if (e.target.classList.contains("act-sell")) {
    const suggested = e.target.dataset.price;
    const raw = prompt("List for how much? ($)", suggested || "");
    if (raw === null) return;
    const price = parseFloat(raw);
    if (!price || price <= 0) { alert("Enter a price greater than 0."); return; }
    e.target.disabled = true;
    e.target.textContent = "Listing…";
    try {
      const res = await api(`/api/inventory/${id}/list-for-sale`, {
        method: "POST",
        body: JSON.stringify({ price }),
      });
      e.target.textContent = "Listed ✓";
      alert(`Listed at ${fmtUsd(res.price)} — platform fee on sale: ${fmtUsd(res.fee_on_sale)} (${res.fee_pct}%).`);
      loadMarket();
    } catch (err) {
      e.target.textContent = "Sell";
      e.target.disabled = false;
      alert(`Listing failed: ${err.message}`);
    }
  }
});

$("#btn-promote").addEventListener("click", async () => {
  const btn = $("#btn-promote");
  btn.disabled = true;
  try {
    const res = await api("/api/inventory/promote", {
      method: "POST",
      body: JSON.stringify({ all_priced: true }),
    });
    alert(`Imported ${res.promoted} cards` +
      (res.skipped_duplicates ? ` (${res.skipped_duplicates} already in your library)` : ""));
    refreshAll();
  } catch (err) {
    alert(err.message);
  } finally {
    btn.disabled = false;
  }
});

$("#btn-refresh").addEventListener("click", async () => {
  const btn = $("#btn-refresh");
  btn.disabled = true;
  btn.textContent = "Refreshing…";
  try {
    await api("/api/portfolio/refresh-prices", { method: "POST" });
    setTimeout(() => { refreshAll(); btn.disabled = false; btn.textContent = "Refresh prices"; }, 4000);
  } catch (err) {
    alert(err.message);
    btn.disabled = false;
    btn.textContent = "Refresh prices";
  }
});

/* ---------------- chart ---------------- */

let chartDays = 90;

async function loadChart() {
  const data = await api(`/api/portfolio/history?days=${chartDays}`);
  renderChart(data.series.filter((p) => p.value != null));
}

function renderChart(series) {
  const host = $("#chart");
  if (series.length < 2) {
    host.innerHTML = '<p class="muted">Not enough price history yet — the chart fills in as the scheduler collects snapshots.</p>';
    return;
  }
  const css = getComputedStyle(document.documentElement);
  const col = (name) => css.getPropertyValue(name).trim();

  const W = 960, H = 260, padL = 56, padR = 12, padT = 12, padB = 26;
  const xs = series.map((_, i) => i);
  const ys = series.map((p) => p.value);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);
  const span = yMax - yMin || yMax || 1;
  const lo = Math.max(0, yMin - span * 0.08), hi = yMax + span * 0.08;
  const X = (i) => padL + (i / (xs.length - 1)) * (W - padL - padR);
  const Y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);

  // ~4 horizontal gridlines at round values.
  const ticks = [];
  const step = niceStep((hi - lo) / 4);
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) ticks.push(v);

  const path = series.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.value).toFixed(1)}`).join("");

  host.innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="portfolio value over the last ${chartDays} days">
      ${ticks.map((v) => `
        <line x1="${padL}" x2="${W - padR}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"
              stroke="${col("--grid")}" stroke-width="1"/>
        <text x="${padL - 8}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end"
              font-size="11" fill="${col("--muted")}" style="font-variant-numeric:tabular-nums">${shortUsd(v)}</text>`).join("")}
      <line x1="${padL}" x2="${W - padR}" y1="${H - padB}" y2="${H - padB}" stroke="${col("--baseline")}" stroke-width="1"/>
      <text x="${padL}" y="${H - 8}" font-size="11" fill="${col("--muted")}">${series[0].date}</text>
      <text x="${W - padR}" y="${H - 8}" text-anchor="end" font-size="11" fill="${col("--muted")}">${series[series.length - 1].date}</text>
      <path d="${path}" fill="none" stroke="${col("--series-1")}" stroke-width="2"
            stroke-linejoin="round" stroke-linecap="round"/>
      <line id="ch-cross" y1="${padT}" y2="${H - padB}" stroke="${col("--baseline")}" stroke-width="1" style="display:none"/>
      <circle id="ch-dot" r="4" fill="${col("--series-1")}" stroke="${col("--surface-1")}" stroke-width="2" style="display:none"/>
      <rect x="${padL}" y="${padT}" width="${W - padL - padR}" height="${H - padT - padB}" fill="transparent" id="ch-hit"/>
    </svg>
    <div class="chart-tip" id="ch-tip"><span class="tip-date"></span><br><span class="tip-val"></span></div>`;

  const svg = host.querySelector("svg");
  const hit = $("#ch-hit"), cross = $("#ch-cross"), dot = $("#ch-dot"), tip = $("#ch-tip");
  const toIdx = (evt) => {
    const r = svg.getBoundingClientRect();
    const px = ((evt.clientX - r.left) / r.width) * W;
    return Math.max(0, Math.min(xs.length - 1, Math.round(((px - padL) / (W - padL - padR)) * (xs.length - 1))));
  };
  hit.addEventListener("mousemove", (evt) => {
    const i = toIdx(evt);
    const x = X(i), y = Y(series[i].value);
    cross.style.display = dot.style.display = "block";
    cross.setAttribute("x1", x); cross.setAttribute("x2", x);
    dot.setAttribute("cx", x); dot.setAttribute("cy", y);
    tip.querySelector(".tip-date").textContent = series[i].date;
    tip.querySelector(".tip-val").textContent = fmtUsd(series[i].value);
    const r = svg.getBoundingClientRect();
    const left = (x / W) * r.width;
    tip.style.display = "block";
    tip.style.left = Math.min(r.width - 130, Math.max(0, left + 10)) + "px";
    tip.style.top = (y / H) * r.height - 48 + "px";
  });
  hit.addEventListener("mouseleave", () => {
    cross.style.display = dot.style.display = tip.style.display = "none";
  });
}

function niceStep(raw) {
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  for (const m of [1, 2, 5, 10]) if (raw <= m * mag) return m * mag;
  return 10 * mag;
}
function shortUsd(v) {
  if (v >= 1000) return "$" + (v / 1000).toFixed(v >= 10000 ? 0 : 1) + "k";
  return "$" + Math.round(v);
}

document.querySelectorAll(".range-btn").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".range-btn").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    chartDays = +b.dataset.days;
    loadChart();
  }));

/* ---------------- alerts + rules ---------------- */

async function loadAlerts() {
  const data = await api("/api/alerts");
  const ul = $("#alerts-list");
  if (!data.alerts.length) {
    ul.innerHTML = '<li class="muted">No alerts yet. They appear here when your rules fire.</li>';
    return;
  }
  ul.innerHTML = data.alerts.map((a) => `
    <li class="${a.is_read ? "" : "unread"}">
      <span>${esc(a.message)}</span>
      <span class="alert-time">${new Date(a.created_at).toLocaleDateString()}</span>
    </li>`).join("");
}

$("#btn-read-all").addEventListener("click", async () => {
  await api("/api/alerts/read-all", { method: "POST" });
  loadAlerts();
  loadSummary();
});

const RULE_LABELS = {
  sell_signal: (r) => `Sell signal: up ≥${r.threshold}% in ${r.window_days}d`,
  pct_change: (r) => `Move ±${r.threshold}% in ${r.window_days}d`,
  price_above: (r) => `Price ≥ ${fmtUsd(r.threshold)}`,
  price_below: (r) => `Price ≤ ${fmtUsd(r.threshold)}`,
};

async function loadRules() {
  const data = await api("/api/alert-rules");
  const ul = $("#rules-list");
  ul.innerHTML = data.rules.map((r) => `
    <li>
      <span>${RULE_LABELS[r.kind] ? RULE_LABELS[r.kind](r) : r.kind}
        <span class="muted"> — ${r.card_name ? esc(r.card_name) : "all cards"}</span></span>
      <button class="btn btn-ghost rule-del" data-id="${r.id}">✕</button>
    </li>`).join("") || '<li class="muted">No rules.</li>';
}

$("#rules-list").addEventListener("click", async (e) => {
  const btn = e.target.closest(".rule-del");
  if (!btn) return;
  await api(`/api/alert-rules/${btn.dataset.id}`, { method: "DELETE" });
  loadRules();
});

$("#rule-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/alert-rules", {
      method: "POST",
      body: JSON.stringify({
        kind: f.get("kind"),
        threshold: parseFloat(f.get("threshold")),
        window_days: parseInt(f.get("window_days") || "30", 10),
        catalog_card_id: f.get("catalog_card_id") || null,
      }),
    });
    e.target.reset();
    loadRules();
  } catch (err) {
    alert(err.message);
  }
});

/* ---------------- my sales & purchases ---------------- */

const ORDER_STATUS = {
  pending_payment: '<span class="chg down">awaiting payment</span>',
  paid: '<span class="chg up">paid</span>',
  completed: '<span class="chg up">completed</span>',
  cancelled: '<span class="muted">cancelled</span>',
};

async function loadMarket() {
  const data = await api("/api/market/my");
  $("#fee-note").textContent =
    `platform fee ${data.fee_pct}% · payments: ${data.payments}`;
  $("#t-balance").textContent = fmtUsd(data.balance);
  $("#t-balance-sub").textContent = data.fees_recorded
    ? `${fmtUsd(data.fees_recorded)} in platform fees` : "";

  activeListingByItem = {};
  for (const l of data.active_listings) activeListingByItem[l.inventory_item_id] = l.listing_id;
  markListedRows();

  const rows = [];
  for (const l of data.active_listings) {
    rows.push(`<li>
      <span>For sale: <strong>${esc(l.card)}</strong> at ${fmtUsd(l.price)}</span>
      <button class="btn btn-ghost mk-cancel" data-id="${l.listing_id}">Cancel</button>
    </li>`);
  }
  for (const o of data.sales) {
    const action = o.status === "pending_payment" && "offline" === o.payment_provider
      ? ` <button class="btn mk-paid" data-id="${o.order_id}">Mark paid</button>` : "";
    rows.push(`<li>
      <span>Sold: <strong>${esc(o.card)}</strong> for ${fmtUsd(o.amount)}
        <span class="muted">(you get ${fmtUsd(o.seller_proceeds)}, fee ${fmtUsd(o.platform_fee)})</span>
        ${ORDER_STATUS[o.status] || o.status}</span>
      <span>${action}<span class="alert-time"> ${new Date(o.created_at).toLocaleDateString()}</span></span>
    </li>`);
  }
  for (const o of data.purchases) {
    const action = o.status === "paid"
      ? ` <button class="btn mk-complete" data-id="${o.order_id}">Card arrived</button>` : "";
    rows.push(`<li>
      <span>Bought: <strong>${esc(o.card)}</strong> for ${fmtUsd(o.amount)}
        ${ORDER_STATUS[o.status] || o.status}</span>
      <span>${action}<span class="alert-time"> ${new Date(o.created_at).toLocaleDateString()}</span></span>
    </li>`);
  }
  $("#market-list").innerHTML = rows.join("") || '<li class="muted">Nothing yet — hit “Sell” on a card, or browse the <a href="/market">marketplace</a>.</li>';
}

$("#market-list").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-id]");
  if (!btn) return;
  try {
    if (btn.classList.contains("mk-cancel")) {
      await api(`/api/market/listings/${btn.dataset.id}/cancel`, { method: "POST" });
    } else if (btn.classList.contains("mk-paid")) {
      if (!confirm("Confirm you received payment? The card transfers to the buyer's library.")) return;
      await api(`/api/market/orders/${btn.dataset.id}/mark-paid`, { method: "POST" });
    } else if (btn.classList.contains("mk-complete")) {
      await api(`/api/market/orders/${btn.dataset.id}/complete`, { method: "POST" });
    }
    refreshAll();
  } catch (err) {
    alert(err.message);
  }
});

/* ---------------- Advanced settings: bring-your-own eBay keys ---------------- */

let ebayStatus = { configured: false, authorized: false };

async function loadEbayStatus() {
  try {
    ebayStatus = await api("/api/settings/ebay");
  } catch (e) {
    ebayStatus = { configured: false, authorized: false };
  }
  const st = $("#ebay-status");
  if (!ebayStatus.configured) {
    st.textContent = "No keys saved — eBay listing is off.";
  } else if (!ebayStatus.authorized) {
    st.textContent = `Keys saved (${ebayStatus.client_id_hint}, ${ebayStatus.env}). ` +
      "Now connect your eBay account below.";
  } else {
    st.textContent = `✓ Connected (${ebayStatus.client_id_hint}, ${ebayStatus.env}) — ` +
      "“eBay” appears on each card in “My cards”.";
  }
  $("#ebay-remove").classList.toggle("hidden", !ebayStatus.configured);
  const connect = $("#ebay-connect");
  connect.classList.toggle("hidden", !ebayStatus.configured || ebayStatus.authorized);
  if (ebayStatus.configured && !ebayStatus.authorized) {
    try {
      const res = await api("/api/settings/ebay/consent-url");
      $("#ebay-consent-link").href = res.url;
    } catch (err) {
      st.textContent += ` (consent-url error: ${err.message})`;
    }
  }
}

$("#btn-advanced").addEventListener("click", () => {
  const panel = $("#advanced-panel");
  panel.classList.toggle("hidden");
  if (!panel.classList.contains("hidden")) {
    loadEbayStatus();
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
});

$("#ebay-keys-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    await api("/api/settings/ebay", {
      method: "POST",
      body: JSON.stringify({
        client_id: f.get("client_id"),
        client_secret: f.get("client_secret"),
        ru_name: f.get("ru_name"),
        env: f.get("env"),
      }),
    });
    e.target.reset();
    await loadEbayStatus();
  } catch (err) {
    alert(`Could not save keys: ${err.message}`);
  }
});

$("#ebay-remove").addEventListener("click", async () => {
  if (!confirm("Remove your eBay keys and disconnect? Existing eBay listings are unaffected.")) return;
  await api("/api/settings/ebay", { method: "DELETE" });
  await loadEbayStatus();
  loadSummary().catch(console.error);
});

$("#ebay-authorize").addEventListener("click", async () => {
  const pasted = $("#ebay-redirect").value.trim();
  if (!pasted) { alert("Open the consent link, approve, then paste the URL you land on."); return; }
  try {
    await api("/api/settings/ebay/authorize", {
      method: "POST",
      body: JSON.stringify({ redirect: pasted }),
    });
    $("#ebay-redirect").value = "";
    await loadEbayStatus();
    loadSummary().catch(console.error); // reveal the eBay buttons
  } catch (err) {
    alert(`Authorization failed: ${err.message}`);
  }
});

async function listOnEbay(itemId, suggested, btn) {
  const raw = prompt("List on eBay for how much? ($)", suggested || "");
  if (raw === null) return;
  const price = parseFloat(raw);
  if (!price || price <= 0) { alert("Enter a price greater than 0."); return; }
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = "Listing…";
  try {
    const res = await api("/api/settings/ebay/list-item", {
      method: "POST",
      body: JSON.stringify({ item_id: itemId, price }),
    });
    btn.textContent = "On eBay ✓";
    alert(res.listing && res.listing.url
      ? `Live: ${res.listing.url}`
      : `Published (status: ${res.status}).`);
  } catch (err) {
    btn.textContent = orig;
    btn.disabled = false;
    alert(`eBay listing failed: ${err.message}`);
  }
}

/* ---------------- boot ---------------- */

function refreshAll() {
  loadEbayStatus()
    .catch(() => {})
    .then(() => loadSummary().then(loadMarket))
    .catch(console.error);
  loadChart().catch(console.error);
  loadAlerts().catch(console.error);
  loadRules().catch(console.error);
}

refreshAll();
setInterval(() => { loadSummary().then(loadMarket).catch(() => {}); loadAlerts().catch(() => {}); }, 60_000);
