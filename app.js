const cardsEl = document.getElementById("cards");
const emptyStateEl = document.getElementById("emptyState");
const dealCountEl = document.getElementById("dealCount");
const updatedAtEl = document.getElementById("updatedAt");
const searchInput = document.getElementById("searchInput");
const sortSelect = document.getElementById("sortSelect");

const REMOVE_ASIN_WEB_APP_URL = "PASTE_YOUR_GOOGLE_APPS_SCRIPT_WEB_APP_URL_HERE";
const HIDDEN_DEALS_KEY = "keepa-dashboard-hidden-asins";
const REMOVE_QUEUE_KEY = "keepa-dashboard-remove-queue-asins";
const HIDE_FOR_HOURS = 24;

let allDeals = [];

function readHiddenMap() {
  try {
    const raw = JSON.parse(localStorage.getItem(HIDDEN_DEALS_KEY) || "{}");

    if (Array.isArray(raw)) {
      const upgraded = {};
      const hideUntil = Date.now() + HIDE_FOR_HOURS * 60 * 60 * 1000;
      raw.forEach((asin) => {
        upgraded[asin] = hideUntil;
      });
      localStorage.setItem(HIDDEN_DEALS_KEY, JSON.stringify(upgraded));
      return upgraded;
    }

    if (raw && typeof raw === "object") return raw;
  } catch {}

  return {};
}

function writeHiddenMap(values) {
  localStorage.setItem(HIDDEN_DEALS_KEY, JSON.stringify(values));
}

function readSet(key) {
  try {
    return new Set(JSON.parse(localStorage.getItem(key) || "[]"));
  } catch {
    return new Set();
  }
}

function writeSet(key, values) {
  localStorage.setItem(key, JSON.stringify([...values]));
}

function activeHiddenMap() {
  const hidden = readHiddenMap();
  const now = Date.now();
  const active = {};

  Object.entries(hidden).forEach(([asin, hideUntil]) => {
    if (Number(hideUntil) > now) {
      active[asin] = Number(hideUntil);
    }
  });

  if (Object.keys(active).length !== Object.keys(hidden).length) {
    writeHiddenMap(active);
  }

  return active;
}

function hiddenAsins() {
  return new Set(Object.keys(activeHiddenMap()));
}

function removeQueueAsins() {
  return readSet(REMOVE_QUEUE_KEY);
}

function hideDeal(asin) {
  const hidden = activeHiddenMap();
  hidden[asin] = Date.now() + HIDE_FOR_HOURS * 60 * 60 * 1000;
  writeHiddenMap(hidden);
  applySearch();
}

async function queueRemoveDeal(asin) {
  const confirmRemove = confirm(`Remove ASIN ${asin} from the spreadsheet?`);
  if (!confirmRemove) return;

  if (!REMOVE_ASIN_WEB_APP_URL || REMOVE_ASIN_WEB_APP_URL.includes("PASTE_YOUR_GOOGLE_APPS_SCRIPT_WEB_APP_URL_HERE")) {
    alert("Remove ASIN is not connected yet. Add your Google Apps Script Web App URL to app.js.");
    return;
  }

  try {
    await fetch(REMOVE_ASIN_WEB_APP_URL, {
      method: "POST",
      mode: "no-cors",
      body: JSON.stringify({ asin: asin })
    });

    hideDeal(asin);
    alert(`Remove request sent for ${asin}.`);
  } catch (error) {
    alert("Could not connect to the spreadsheet removal script.");
    console.error(error);
  }
}

function resetHiddenDeals() {
  localStorage.removeItem(HIDDEN_DEALS_KEY);
  applySearch();
}

function clearRemoveQueue() {
  localStorage.removeItem(REMOVE_QUEUE_KEY);
  applySearch();
}

async function copyRemoveQueue() {
  const removeQueue = [...removeQueueAsins()].sort();
  if (removeQueue.length === 0) return;

  const text = removeQueue.join("\n");
  try {
    await navigator.clipboard.writeText(text);
    alert(`Copied ${removeQueue.length} ASIN${removeQueue.length === 1 ? "" : "s"} to remove.`);
  } catch {
    prompt("Copy these ASINs and remove them from the Google Sheet:", text);
  }
}

function visibleDeals() {
  const hidden = hiddenAsins();
  const removeQueue = removeQueueAsins();
  return allDeals.filter((deal) => !hidden.has(deal.asin) && !removeQueue.has(deal.asin));
}

function money(value) {
  if (value === null || value === undefined) return "N/A";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(value);
}

function formatDate(value) {
  if (!value) return "Not updated yet";
  return new Date(value).toLocaleString();
}

function formatShortDate(value) {
  if (!value) return "N/A";
  return new Date(value).toLocaleString([], {
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function numericValue(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function dateValue(value) {
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

function hoursUntil(value) {
  if (!value) return null;
  const diffMs = new Date(value).getTime() - Date.now();
  if (!Number.isFinite(diffMs)) return null;
  return Math.max(0, diffMs / (1000 * 60 * 60));
}

function sortDeals(deals) {
  const sortMode = sortSelect ? sortSelect.value : "newest";
  const sorted = [...deals];

  sorted.sort((a, b) => {
    if (sortMode === "highest-drop") {
      return (numericValue(b.drop_percent) || 0) - (numericValue(a.drop_percent) || 0);
    }

    if (sortMode === "highest-30-drop") {
      return (numericValue(b.drop_30_percent) || 0) - (numericValue(a.drop_30_percent) || 0);
    }

    if (sortMode === "lowest-price") {
      const aPrice = numericValue(a.current_price);
      const bPrice = numericValue(b.current_price);
      if (aPrice === null && bPrice === null) return 0;
      if (aPrice === null) return 1;
      if (bPrice === null) return -1;
      return aPrice - bPrice;
    }

    const aPosted = a.posted_at || a.first_seen_at || a.checked_at;
    const bPosted = b.posted_at || b.first_seen_at || b.checked_at;
    return dateValue(bPosted) - dateValue(aPosted);
  });

  return sorted;
}

function imageCandidatesForDeal(deal) {
  const asin = deal.asin;
  const candidates = [];

  if (deal.image) candidates.push(deal.image);

  if (asin) {
    candidates.push(`https://images-na.ssl-images-amazon.com/images/P/${asin}.01._SL500_.jpg`);
    candidates.push(`https://m.media-amazon.com/images/P/${asin}.01._SL500_.jpg`);
    candidates.push(`https://images-na.ssl-images-amazon.com/images/P/${asin}.01.LZZZZZZZ.jpg`);
    candidates.push(`https://ws-na.amazon-adsystem.com/widgets/q?_encoding=UTF8&MarketPlace=US&ASIN=${asin}&ServiceVersion=20070822&ID=AsinImage&WS=1&Format=_SL500_`);
  }

  return [...new Set(candidates.filter(Boolean))];
}

function buildImageMarkup(deal) {
  const candidates = imageCandidatesForDeal(deal);
  const encodedCandidates = encodeURIComponent(JSON.stringify(candidates));
  const firstImage = candidates[0] || "";

  if (!firstImage) return "";

  return `<img
    src="${firstImage}"
    alt="${deal.title}"
    loading="lazy"
    data-image-index="0"
    data-image-candidates="${encodedCandidates}"
    onerror="tryNextImage(this)"
  >`;
}

function tryNextImage(img) {
  const wrap = img.closest(".image-wrap");
  const candidates = JSON.parse(decodeURIComponent(img.dataset.imageCandidates || "%5B%5D"));
  const currentIndex = Number(img.dataset.imageIndex || 0);
  const nextIndex = currentIndex + 1;

  if (nextIndex < candidates.length) {
    img.dataset.imageIndex = String(nextIndex);
    img.src = candidates[nextIndex];
    return;
  }

  wrap.classList.add("image-missing");
  img.remove();
}

function updateCounts(renderedCount) {
  const hiddenCount = hiddenAsins().size;
  const removeCount = removeQueueAsins().size;
  const totalCount = allDeals.length;

  dealCountEl.innerHTML = `${renderedCount} visible active deal${renderedCount === 1 ? "" : "s"}`;

  if (totalCount !== renderedCount) {
    dealCountEl.innerHTML += ` <span class="count-note">${totalCount} total active</span>`;
  }

  if (hiddenCount > 0) {
    dealCountEl.innerHTML += ` <button class="reset-hidden" type="button" onclick="resetHiddenDeals()">Show hidden (${hiddenCount})</button>`;
  }

  if (removeCount > 0) {
    dealCountEl.innerHTML += ` <button class="copy-remove" type="button" onclick="copyRemoveQueue()">Copy removals (${removeCount})</button>`;
    dealCountEl.innerHTML += ` <button class="clear-remove" type="button" onclick="clearRemoveQueue()">Clear removals</button>`;
  }
}

function renderDeals(deals) {
  cardsEl.innerHTML = "";
  emptyStateEl.hidden = deals.length !== 0;
  updateCounts(deals.length);

  deals.forEach((deal) => {
    const card = document.createElement("article");
    card.className = "card";
    const postedAt = deal.posted_at || deal.first_seen_at || deal.checked_at;
    const expiresAt = deal.expires_at;
    const hoursLeft = hoursUntil(expiresAt);
    const expiresText = hoursLeft === null ? "N/A" : `${hoursLeft.toFixed(1)} hrs left`;

    card.innerHTML = `
      <a class="image-wrap" href="${deal.amazon_url}" target="_blank" rel="noopener noreferrer" aria-label="Open ${deal.title} on Amazon">
        ${buildImageMarkup(deal)}
        <div class="image-placeholder">
          <span>No image available</span>
          <small>${deal.asin}</small>
        </div>
      </a>
      <div class="card-body">
        <div class="card-top-row">
          <span class="badge">${deal.drop_percent}% below 7-day average</span>
          <div class="card-actions">
            <button class="hide-card" type="button" onclick="hideDeal('${deal.asin}')">Hide 24h</button>
            <button class="remove-card" type="button" onclick="queueRemoveDeal('${deal.asin}')">Remove ASIN</button>
          </div>
        </div>
        <div class="deal-time">
          <span>Posted: ${formatShortDate(postedAt)}</span>
          <span>${expiresText}</span>
        </div>
        <h2>${deal.title}</h2>
        <div class="asin">ASIN: ${deal.asin}</div>
        <div class="price-row">
          <div class="price-box">
            <span>Current</span>
            <strong>${money(deal.current_price)}</strong>
          </div>
          <div class="price-box">
            <span>7-Day Avg.</span>
            <strong>${money(deal.avg_7_price)}</strong>
          </div>
        </div>
        <div class="price-row">
          <div class="price-box">
            <span>30-Day Avg.</span>
            <strong>${money(deal.avg_30_price)}</strong>
          </div>
          <div class="price-box">
            <span>30-Day Drop</span>
            <strong>${deal.drop_30_percent === null || deal.drop_30_percent === undefined ? "N/A" : `${deal.drop_30_percent}%`}</strong>
          </div>
        </div>
        <div class="price-box">
          <span>7-Day Low</span>
          <strong>${money(deal.min_7_price)}</strong>
        </div>
        <a class="button" href="${deal.amazon_url}" target="_blank" rel="noopener noreferrer">Open on Amazon</a>
      </div>
    `;

    cardsEl.appendChild(card);
  });
}

function applySearch() {
  const term = searchInput.value.trim().toLowerCase();
  const baseDeals = sortDeals(visibleDeals());

  if (!term) {
    renderDeals(baseDeals);
    return;
  }

  const filtered = baseDeals.filter((deal) => {
    return (
      deal.title.toLowerCase().includes(term) ||
      deal.asin.toLowerCase().includes(term)
    );
  });

  renderDeals(filtered);
}

async function loadDeals() {
  try {
    const response = await fetch("data/deals.json", { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load deals.json");

    const data = await response.json();
    allDeals = data.deals || [];
    updatedAtEl.textContent = `Last updated: ${formatDate(data.updated_at)} · Deals kept for ${data.deal_ttl_hours || 24} hours`;
    applySearch();
  } catch (error) {
    dealCountEl.textContent = "Could not load deal data";
    updatedAtEl.textContent = error.message;
    emptyStateEl.hidden = false;
  }
}

searchInput.addEventListener("input", applySearch);
if (sortSelect) sortSelect.addEventListener("change", applySearch);
loadDeals();
