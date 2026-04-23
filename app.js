const cardsEl = document.getElementById("cards");
const emptyStateEl = document.getElementById("emptyState");
const dealCountEl = document.getElementById("dealCount");
const updatedAtEl = document.getElementById("updatedAt");
const searchInput = document.getElementById("searchInput");

const HIDDEN_DEALS_KEY = "keepa-dashboard-hidden-asins";
const REMOVE_QUEUE_KEY = "keepa-dashboard-remove-queue-asins";

let allDeals = [];

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

function hiddenAsins() {
  return readSet(HIDDEN_DEALS_KEY);
}

function removeQueueAsins() {
  return readSet(REMOVE_QUEUE_KEY);
}

function hideDeal(asin) {
  const hidden = hiddenAsins();
  hidden.add(asin);
  writeSet(HIDDEN_DEALS_KEY, hidden);
  applySearch();
}

function queueRemoveDeal(asin) {
  const removeQueue = removeQueueAsins();
  removeQueue.add(asin);
  writeSet(REMOVE_QUEUE_KEY, removeQueue);

  const hidden = hiddenAsins();
  hidden.add(asin);
  writeSet(HIDDEN_DEALS_KEY, hidden);

  applySearch();
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
    prompt("Copy these ASINs and remove them from asins.csv:", text);
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

  dealCountEl.innerHTML = `${renderedCount} visible price drop${renderedCount === 1 ? "" : "s"} found`;

  if (totalCount !== renderedCount) {
    dealCountEl.innerHTML += ` <span class="count-note">${totalCount} total</span>`;
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
            <button class="hide-card" type="button" onclick="hideDeal('${deal.asin}')">Hide</button>
            <button class="remove-card" type="button" onclick="queueRemoveDeal('${deal.asin}')">Remove ASIN</button>
          </div>
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
  const baseDeals = visibleDeals();

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
    updatedAtEl.textContent = `Last updated: ${formatDate(data.updated_at)}`;
    applySearch();
  } catch (error) {
    dealCountEl.textContent = "Could not load deal data";
    updatedAtEl.textContent = error.message;
    emptyStateEl.hidden = false;
  }
}

searchInput.addEventListener("input", applySearch);
loadDeals();
