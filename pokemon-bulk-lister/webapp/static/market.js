/* Marketplace browse + buy. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const fmtUsd = (v) =>
  v == null ? "—" : v.toLocaleString("en-US", { style: "currency", currency: "USD" });

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (m) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
}

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (res.status === 401) {
    window.location = "/login?next=/market";
    throw new Error("login required");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `${res.status}`);
  return data;
}

async function load() {
  const data = await api("/api/market/listings");
  $("#market-meta").textContent =
    `${data.listings.length} listings · platform fee ${data.fee_pct}% (paid by seller)`;
  const grid = $("#market-grid");
  if (!data.listings.length) {
    grid.innerHTML = '<p class="muted">Nothing for sale right now. List one of your cards from your <a href="/portfolio">portfolio</a>.</p>';
    return;
  }
  grid.innerHTML = data.listings.map((l) => {
    const img = l.image_url
      ? `<img src="${esc(l.image_url)}" alt="">`
      : l.crop_path ? `<img src="/${esc(l.crop_path)}" alt="">` : "<div class='ph'></div>";
    const vs = l.vs_market_pct == null ? "" :
      `<span class="chg ${l.vs_market_pct <= 0 ? "up" : "down"}">${l.vs_market_pct > 0 ? "+" : ""}${l.vs_market_pct}% vs market</span>`;
    return `<article class="market-card">
      ${img}
      <div class="mc-body">
        <div class="inv-name">${esc(l.name)}${l.is_holo ? " ✦" : ""}</div>
        <div class="inv-set">${esc(l.set_name || "")}${l.card_number ? " #" + esc(l.card_number) : ""} · ${esc(l.condition)}</div>
        <div class="mc-price">${fmtUsd(l.price)} ${vs}</div>
        <div class="inv-set">seller: ${esc(l.seller)}</div>
        ${l.mine
          ? '<span class="muted">your listing</span>'
          : `<button class="btn btn-primary mc-buy" data-id="${l.listing_id}">Buy</button>`}
      </div>
    </article>`;
  }).join("");
}

$("#market-grid").addEventListener("click", async (e) => {
  const btn = e.target.closest(".mc-buy");
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = "Buying…";
  try {
    const res = await api(`/api/market/listings/${btn.dataset.id}/buy`, {
      method: "POST", body: JSON.stringify({}),
    });
    if (res.checkout_url) {
      window.location = res.checkout_url;
      return;
    }
    alert(res.note || "Order placed.");
    load();
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "Buy";
    alert(err.message);
  }
});

load();
setInterval(() => load().catch(() => {}), 60_000);
