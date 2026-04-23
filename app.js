const cardsEl = document.getElementById("cards");
const emptyStateEl = document.getElementById("emptyState");
const dealCountEl = document.getElementById("dealCount");
const updatedAtEl = document.getElementById("updatedAt");
const searchInput = document.getElementById("searchInput");

let allDeals = [];

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

function renderDeals(deals) {
  cardsEl.innerHTML = "";
  emptyStateEl.hidden = deals.length !== 0;

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
        <span class="badge">${deal.drop_percent}% below 7-day average</span>
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
  if (!term) {
    renderDeals(allDeals);
    return;
  }

  const filtered = allDeals.filter((deal) => {
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
    dealCountEl.textContent = `${allDeals.length} 7-day price drop${allDeals.length === 1 ? "" : "s"} found`;
    updatedAtEl.textContent = `Last updated: ${formatDate(data.updated_at)}`;
    renderDeals(allDeals);
  } catch (error) {
    dealCountEl.textContent = "Could not load deal data";
    updatedAtEl.textContent = error.message;
    emptyStateEl.hidden = false;
  }
}

searchInput.addEventListener("input", applySearch);
loadDeals();
