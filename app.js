const cardsEl = document.getElementById("cards");
const selectedCardsEl = document.getElementById("selectedCards");
const selectedPostingSectionEl = document.getElementById("selectedPostingSection");
const selectedPostingCountEl = document.getElementById("selectedPostingCount");
const emptyStateEl = document.getElementById("emptyState");
const dealCountEl = document.getElementById("dealCount");
const updatedAtEl = document.getElementById("updatedAt");
const searchInput = document.getElementById("searchInput");
const sortSelect = document.getElementById("sortSelect");
const asinAddForm = document.getElementById("asinAddForm");
const asinAddInput = document.getElementById("asinAddInput");
const asinAddStatus = document.getElementById("asinAddStatus");
const asinBulkRemoveForm = document.getElementById("asinBulkRemoveForm");
const asinBulkRemoveInput = document.getElementById("asinBulkRemoveInput");
const asinBulkRemoveStatus = document.getElementById("asinBulkRemoveStatus");
const dashboardMode = document.body.dataset.dashboardMode || "price";
const dashboardDataUrl = document.body.dataset.dealsUrl || "data/deals.json";

const REMOVE_ASIN_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbxU4HTktR6zH5Wfbk58V24X-HAE9kZYlzdlm1gqMp1NL_ZGzF7p-0VAL5VeGNfnAyxESA/exec";
const HIDDEN_DEALS_KEY = "keepa-dashboard-hidden-asins";
const REMOVE_QUEUE_KEY = "keepa-dashboard-remove-queue-asins";
const SELECTED_FOR_POSTING_KEY = "keepa-dashboard-selected-for-posting-asins";
const PUBLISH_STATUS_KEY = "keepa-dashboard-publish-status";
const SHOW_ALL_DEALS_KEY = "keepa-dashboard-show-all-deals";
const HIDE_FOR_HOURS = 24;
const DEALS_PER_PAGE = 50;
const BULK_REMOVE_CHUNK_SIZE = 50;
const AFFILIATE_TAGS = {
  woodworkingPage: "page_page_page-20",
  blackLabPage: "blacklabdealsprime-20",
  default: "simplewoodsho-20",
};

let allDeals = [];
let shownRegularDealLimit = DEALS_PER_PAGE;
let currentRenderedDeals = [];
let loadMoreSectionEl = null;
let loadMoreButtonEl = null;
let loadMoreSummaryEl = null;
let loadMoreButtonListenerAttached = false;

function base64EncodeBytes(bytes) {
  const chunkSize = 0x8000;
  let binary = "";
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

function ensureLoadMoreControls() {
  if (loadMoreSectionEl && loadMoreButtonEl && loadMoreSummaryEl) {
    return;
  }

  loadMoreSectionEl = document.getElementById("loadMoreSection");

  if (!loadMoreSectionEl) {
    loadMoreSectionEl = document.createElement("section");
    loadMoreSectionEl.id = "loadMoreSection";
    loadMoreSectionEl.className = "load-more-section";
    cardsEl.insertAdjacentElement("afterend", loadMoreSectionEl);
  }

  loadMoreSummaryEl = document.getElementById("loadMoreSummary");
  if (!loadMoreSummaryEl) {
    loadMoreSummaryEl = document.createElement("p");
    loadMoreSummaryEl.id = "loadMoreSummary";
    loadMoreSummaryEl.className = "load-more-summary";
    loadMoreSectionEl.appendChild(loadMoreSummaryEl);
  }

  loadMoreButtonEl = document.getElementById("loadMoreButton");
  if (!loadMoreButtonEl) {
    loadMoreButtonEl = document.createElement("button");
    loadMoreButtonEl.id = "loadMoreButton";
    loadMoreButtonEl.className = "load-more-button";
    loadMoreButtonEl.type = "button";
    loadMoreSectionEl.appendChild(loadMoreButtonEl);
  }

  if (!loadMoreButtonListenerAttached) {
    loadMoreButtonEl.addEventListener("click", loadMoreDeals);
    loadMoreButtonListenerAttached = true;
  }
}

function resetDealLimit() {
  shownRegularDealLimit = DEALS_PER_PAGE;
}

function loadMoreDeals() {
  shownRegularDealLimit += DEALS_PER_PAGE;
  renderDeals(currentRenderedDeals);
}

function updateLoadMoreControls(showingRegularCount, totalRegularCount) {
  ensureLoadMoreControls();

  const remainingCount = Math.max(0, totalRegularCount - showingRegularCount);
  loadMoreSectionEl.hidden = totalRegularCount <= DEALS_PER_PAGE || remainingCount === 0;
  loadMoreSummaryEl.textContent = `Showing ${showingRegularCount} of ${totalRegularCount} unselected deal${totalRegularCount === 1 ? "" : "s"}.`;
  loadMoreButtonEl.textContent = `Load ${Math.min(DEALS_PER_PAGE, remainingCount)} more deal${Math.min(DEALS_PER_PAGE, remainingCount) === 1 ? "" : "s"}`;
}

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

function readPublishStatusMap() {
  try {
    const raw = JSON.parse(localStorage.getItem(PUBLISH_STATUS_KEY) || "{}");
    if (raw && typeof raw === "object") return raw;
  } catch {}
  return {};
}

function writePublishStatus(asin, payload) {
  const values = readPublishStatusMap();
  values[asin] = {
    ...payload,
    updated_at: new Date().toISOString(),
  };
  localStorage.setItem(PUBLISH_STATUS_KEY, JSON.stringify(values));
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

function showAllDealsEnabled() {
  return localStorage.getItem(SHOW_ALL_DEALS_KEY) === "true";
}

function toggleShowAllDeals() {
  localStorage.setItem(SHOW_ALL_DEALS_KEY, showAllDealsEnabled() ? "false" : "true");
  applySearch();
}

function selectedForPostingAsins() {
  return readSet(SELECTED_FOR_POSTING_KEY);
}

function writeSelectedForPostingAsins(values) {
  writeSet(SELECTED_FOR_POSTING_KEY, values);
}

function toggleSelectedForPosting(asin) {
  const selected = selectedForPostingAsins();

  if (selected.has(asin)) {
    selected.delete(asin);
  } else {
    selected.add(asin);
  }

  writeSelectedForPostingAsins(selected);
  applySearch(false);
}

function removeFromSelectedForPosting(asin) {
  const selected = selectedForPostingAsins();
  if (!selected.has(asin)) return;

  selected.delete(asin);
  writeSelectedForPostingAsins(selected);
}

function hideDeal(asin) {
  const hidden = activeHiddenMap();
  hidden[asin] = Date.now() + HIDE_FOR_HOURS * 60 * 60 * 1000;
  writeHiddenMap(hidden);
  removeFromSelectedForPosting(asin);
  applySearch(false);
}

function hideDealsLocally(asins) {
  const hidden = activeHiddenMap();
  const hideUntil = Date.now() + HIDE_FOR_HOURS * 60 * 60 * 1000;

  asins.forEach((asin) => {
    hidden[asin] = hideUntil;
    removeFromSelectedForPosting(asin);
  });

  writeHiddenMap(hidden);
  applySearch(false);
}

function callAsinScript(action, params = {}) {
  return new Promise((resolve, reject) => {
    if (!REMOVE_ASIN_WEB_APP_URL || REMOVE_ASIN_WEB_APP_URL.includes("PASTE_YOUR_GOOGLE_APPS_SCRIPT_WEB_APP_URL_HERE")) {
      reject(new Error("ASIN tools script is not connected yet."));
      return;
    }

    const callbackName = `handleAsinTool_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    const script = document.createElement("script");
    const url = new URL(REMOVE_ASIN_WEB_APP_URL);
    let timeoutId = null;

    function cleanup() {
      if (timeoutId) clearTimeout(timeoutId);
      delete window[callbackName];
      script.remove();
    }

    window[callbackName] = (payload) => {
      cleanup();
      resolve(payload);
    };

    script.onerror = () => {
      cleanup();
      reject(new Error("Could not connect to the ASIN tools script."));
    };

    const timeoutMs = action === "publishDeal" ? 60000 : 15000;
    timeoutId = setTimeout(() => {
      cleanup();
      reject(new Error("The ASIN tools script did not respond."));
    }, timeoutMs);

    url.searchParams.set("action", action);
    Object.entries(params).forEach(([key, value]) => {
      url.searchParams.set(key, value);
    });
    url.searchParams.set("callback", callbackName);
    script.src = url.toString();
    document.head.appendChild(script);
  });
}

function removeAsinWithScript(asin) {
  return callAsinScript("removeAsin", { asin });
}

async function removeAsinsOneAtATime(asins) {
  const removedAsins = [];
  const notFound = [];

  for (const asin of asins) {
    const result = await removeAsinWithScript(asin);
    if (result && result.ok) {
      removedAsins.push(asin);
    } else {
      notFound.push(asin);
    }
  }

  return {
    ok: true,
    requested: asins.length,
    removed: removedAsins.length,
    removed_asins: removedAsins,
    not_found: notFound,
  };
}

async function removeAsinsWithScript(asins) {
  const summary = {
    ok: true,
    requested: asins.length,
    removed: 0,
    removed_asins: [],
    not_found: [],
  };

  for (let index = 0; index < asins.length; index += BULK_REMOVE_CHUNK_SIZE) {
    const chunk = asins.slice(index, index + BULK_REMOVE_CHUNK_SIZE);
    const result = await callAsinScript("removeAsins", { asins: chunk.join("\n") });

    if (result && result.ok) {
      summary.removed += Number(result.removed || result.found || 0);
      summary.removed_asins.push(...(result.removed_asins || chunk.filter((asin) => !(result.not_found || []).includes(asin))));
      summary.not_found.push(...(result.not_found || []));
      continue;
    }

    if (result && /unknown action/i.test(result.error || "")) {
      return removeAsinsOneAtATime(asins);
    }

    throw new Error(result && result.error ? result.error : "The source sheet did not confirm bulk removal.");
  }

  return summary;
}

function parseAsinsFromText(value) {
  return [...new Set((String(value || "").toUpperCase().match(/\bB[0-9A-Z]{9}\b/g) || []))];
}

function setAsinAddStatus(message) {
  if (asinAddStatus) asinAddStatus.textContent = message;
}

function setAsinBulkRemoveStatus(message) {
  if (asinBulkRemoveStatus) asinBulkRemoveStatus.textContent = message;
}

function initAsinAddForm() {
  if (!asinAddForm || !asinAddInput) return;

  asinAddForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const asins = parseAsinsFromText(asinAddInput.value);
    const button = asinAddForm.querySelector("button[type='submit']");

    if (!asins.length) {
      setAsinAddStatus("Paste one or more valid ASINs first.");
      return;
    }

    try {
      if (button) button.disabled = true;
      setAsinAddStatus(`Adding ${asins.length} ASIN${asins.length === 1 ? "" : "s"} to the scan sheet...`);
      const result = await callAsinScript("addAsins", { asins: asins.join("\n") });
      if (!result || result.ok === false) {
        throw new Error(result && result.error ? result.error : "Unknown add ASIN error.");
      }

      const addedCount = Number(result.addedCount || 0);
      const duplicateCount = Number(result.duplicateCount || 0);
      asinAddInput.value = "";
      setAsinAddStatus(`Added ${addedCount} new ASIN${addedCount === 1 ? "" : "s"} to ASIN_List. Skipped ${duplicateCount} duplicate${duplicateCount === 1 ? "" : "s"}.`);
    } catch (error) {
      setAsinAddStatus(`Could not add ASINs: ${error.message}`);
    } finally {
      if (button) button.disabled = false;
    }
  });
}

function initAsinBulkRemoveForm() {
  if (!asinBulkRemoveForm || !asinBulkRemoveInput) return;

  asinBulkRemoveForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const asins = parseAsinsFromText(asinBulkRemoveInput.value);
    const button = asinBulkRemoveForm.querySelector("button[type='submit']");

    if (!asins.length) {
      setAsinBulkRemoveStatus("Paste one or more valid ASINs first.");
      return;
    }

    try {
      if (button) button.disabled = true;
      setAsinBulkRemoveStatus(`Removing ${asins.length} ASIN${asins.length === 1 ? "" : "s"} from the scan sheet...`);
      const result = await removeAsinsWithScript(asins);

      if (!result || !result.ok) {
        throw new Error(result && result.error ? result.error : "Unknown bulk remove error.");
      }

      const removedAsins = result.removed_asins && result.removed_asins.length ? result.removed_asins : asins;
      hideDealsLocally(removedAsins);
      asinBulkRemoveInput.value = "";

      const removedCount = Number(result.removed || removedAsins.length || 0);
      const notFoundCount = (result.not_found || []).length;
      setAsinBulkRemoveStatus(`Removed ${removedCount} ASIN${removedCount === 1 ? "" : "s"} from ASIN_List. ${notFoundCount ? `${notFoundCount} not found.` : "They are hidden here for 24 hours."}`);
    } catch (error) {
      const removeQueue = removeQueueAsins();
      asins.forEach((asin) => {
        removeQueue.add(asin);
        removeFromSelectedForPosting(asin);
      });
      writeSet(REMOVE_QUEUE_KEY, removeQueue);
      hideDealsLocally(asins);
      setAsinBulkRemoveStatus(`${error.message} ${asins.length} ASIN${asins.length === 1 ? "" : "s"} queued locally. Use "Copy removals" if needed.`);
    } finally {
      if (button) button.disabled = false;
    }
  });
}

async function cleanSourceSheet() {
  const confirmClean = confirm("Scan the source sheet and remove duplicate ASINs?");
  if (!confirmClean) return;

  try {
    const result = await callAsinScript("cleanSheet");

    if (!result || !result.ok) {
      const message = result && result.error ? result.error : "The source sheet did not confirm cleanup.";
      alert(`Could not clean sheet: ${message}`);
      return;
    }

    alert(`Cleaned ${result.sheet || "the source sheet"}. Removed ${result.duplicateRemovedCount || 0} duplicate ASINs.`);
  } catch (error) {
    alert(`${error.message} The sheet was not cleaned.`);
  }
}

async function queueRemoveDeal(asin) {
  try {
    const result = await removeAsinWithScript(asin);

    if (!result || !result.ok) {
      const message = result && result.error ? result.error : "The source sheet did not confirm removal.";
      alert(`Could not remove ${asin}: ${message}`);
      return;
    }

    hideDeal(asin);
  } catch (error) {
    const removeQueue = removeQueueAsins();
    removeQueue.add(asin);
    writeSet(REMOVE_QUEUE_KEY, removeQueue);
    removeFromSelectedForPosting(asin);
    applySearch(false);

    alert(`${error.message} ${asin} was queued locally instead. Use "Copy removals" at the top of the dashboard if you need to remove it manually.`);
  }
}

function resetHiddenDeals() {
  localStorage.removeItem(HIDDEN_DEALS_KEY);
  applySearch();
}

function clearSelectedForPosting() {
  localStorage.removeItem(SELECTED_FOR_POSTING_KEY);
  applySearch(false);
}

function clearRemoveQueue() {
  localStorage.removeItem(REMOVE_QUEUE_KEY);
  applySearch(false);
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

async function copySelectedLinks() {
  const selectedAsins = selectedForPostingAsins();
  const selectedDeals = sortDeals(visibleDeals()).filter((deal) => selectedAsins.has(deal.asin));

  if (selectedDeals.length === 0) {
    alert("No selected links to copy.");
    return;
  }

  const text = selectedDeals
    .map((deal) => `${deal.title}\n${deal.amazon_url}`)
    .join("\n\n");

  try {
    await navigator.clipboard.writeText(text);
    alert(`Copied ${selectedDeals.length} selected link${selectedDeals.length === 1 ? "" : "s"}.`);
  } catch {
    prompt("Copy these selected links:", text);
  }
}

function publishDelayLabel(delayMinutes) {
  return delayMinutes === 0 ? "now" : `+${delayMinutes} min`;
}

function bestImageForPublish(deal) {
  return imageCandidatesForDeal(deal)[0] || "";
}

function affiliateTagForTarget(target) {
  return AFFILIATE_TAGS[target] || AFFILIATE_TAGS.default;
}

function affiliateUrlForTarget(deal, target) {
  const asin = String((deal && deal.asin) || "").trim();
  const tag = affiliateTagForTarget(target);
  if (asin) return `https://www.amazon.com/dp/${encodeURIComponent(asin)}?tag=${encodeURIComponent(tag)}`;

  const rawUrl = String((deal && deal.amazon_url) || "").trim();
  if (!rawUrl) return "";
  try {
    const url = new URL(rawUrl, window.location.href);
    if (url.hostname.includes("amazon.")) url.searchParams.set("tag", tag);
    return url.toString();
  } catch {
    return rawUrl;
  }
}

async function publishDeal(deal, target, delayMinutes, button) {
  const targetLabels = {
    woodworkingGroup: "Woodworking Page + Group",
    dadDealsGroup: "Dad Deals Group CSV",
    woodworkingPage: "Woodworking Page",
    blackLabPage: "Black Lab Page",
    groupCsv: "Group CSV",
    page: "Page",
  };
  const targetLabel = targetLabels[target] || target;
  const originalText = button ? button.textContent : "";

  if (button) {
    button.disabled = true;
    button.textContent = target === "groupCsv" ? "Adding..." : "Publishing...";
  }

  try {
    const result = await callAsinScript("publishDeal", {
      target,
      delayMinutes,
      asin: deal.asin,
      title: deal.title,
      currentPrice: deal.current_price || "",
      avg30Price: deal.avg_30_price || "",
      drop30Percent: deal.drop_30_percent || "",
      amazonUrl: affiliateUrlForTarget(deal, target),
      affiliateTag: affiliateTagForTarget(target),
      imageUrl: bestImageForPublish(deal),
    });

    if (!result || !result.ok) {
      const message = result && result.error ? result.error : "Publishing did not return a success response.";
      throw new Error(message);
    }

    writePublishStatus(deal.asin, {
      target,
      delay_minutes: delayMinutes,
      status: result.status || (String(target).includes("Group") || target === "groupCsv" ? "queued" : "sent"),
      scheduled_for: result.scheduled_for || "",
      publer_job_id: result.publer_job_id || "",
    });

    alert(`${targetLabel} ${publishDelayLabel(delayMinutes)}: ${result.message || "sent successfully."}`);
    hideDeal(deal.asin);
  } catch (error) {
    if (/already sent/i.test(error.message || "")) {
      hideDeal(deal.asin);
    }
    alert(`${targetLabel} ${publishDelayLabel(delayMinutes)} failed for ${deal.asin}: ${error.message}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalText;
    }
  }
}

function visibleDeals() {
  const hidden = hiddenAsins();
  const removeQueue = removeQueueAsins();
  return allDeals.filter((deal) => {
    const postingTier = typeof window.dealPostingTier === "function" ? window.dealPostingTier(deal) : "";
    return !hidden.has(deal.asin) && !removeQueue.has(deal.asin) && (showAllDealsEnabled() || postingTier !== "Skip");
  });
}

function money(value) {
  if (value === null || value === undefined) return "N/A";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(value);
}

function livePriceNote(deal) {
  if (!deal || !deal.live_price_lower_than_keepa) return "";
  const keepaPrice = numericValue(deal.keepa_current_price);
  const livePrice = numericValue(deal.current_price);
  if (keepaPrice === null || livePrice === null || livePrice >= keepaPrice) return "";
  return `<div class="live-price-note">Amazon live price is below Keepa current (${money(keepaPrice)})</div>`;
}

function couponNote(deal) {
  if (!deal || !deal.coupon_label) return "";
  const afterCoupon = deal.after_coupon_price ? ` After coupon: ${money(deal.after_coupon_price)}.` : "";
  return `<div class="coupon-note">${deal.coupon_label}.${afterCoupon}</div>`;
}

function keepaOfferNote(deal) {
  if (!deal) return "";
  const offer = deal.keepa_offer || {};
  const lightning = deal.lightning_deal || {};
  if (!deal.price_type_label && !offer.shipping_visible && deal.price_type !== "lightning_deal") return "";
  const parts = [];
  if (deal.price_type_label) parts.push(deal.price_type_label);
  if (deal.price_type === "lightning_deal") {
    const shipping = lightning.shipping_cents;
    if (shipping === 0) {
      parts.push("shipping shown as $0");
    } else if (lightning.is_prime || lightning.is_fba || lightning.is_amazon) {
      parts.push("Prime/FBA shipping signal");
    }
  } else if (offer.free_shipping_seen) {
    parts.push("free shipping shown");
  } else if (offer.shipping_visible) {
    parts.push("shipping shown as $0");
  } else if (deal.price_type === "keepa_preferred_offer") {
    parts.push("shipping not visible");
  }
  return `<div class="offer-note">${parts.join(" - ")}</div>`;
}

function primaryDealBadge(deal) {
  if (deal && deal.price_type === "lightning_deal") {
    return `<span class="badge lightning">Lightning Deal</span>`;
  }
  return `<span class="badge">${deal.drop_30_percent}% below 30-day average</span>`;
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

function compareNullableNumbers(aValue, bValue, direction = "desc") {
  const aNumber = numericValue(aValue);
  const bNumber = numericValue(bValue);

  if (aNumber === null && bNumber === null) return 0;
  if (aNumber === null) return 1;
  if (bNumber === null) return -1;

  return direction === "asc" ? aNumber - bNumber : bNumber - aNumber;
}

function dollarDrop(deal) {
  const currentPrice = numericValue(deal.current_price);
  const avg7Price = numericValue(deal.avg_7_price);

  if (currentPrice === null || avg7Price === null) return null;
  return Math.max(0, avg7Price - currentPrice);
}

function hasCreatorCampaign(deal) {
  return Boolean(
    deal && (
      deal.has_creator_campaign ||
      deal.creator_campaign ||
      deal.creator_commission_rate
    )
  );
}

function creatorCampaignEndDateValue(deal) {
  const campaign = deal && deal.creator_campaign;
  if (!campaign || !campaign.campaign_end_date) return Number.MAX_SAFE_INTEGER;
  const endTime = dateValue(campaign.campaign_end_date);
  return endTime || Number.MAX_SAFE_INTEGER;
}

function dealScore(deal) {
  const dropPercent = numericValue(deal.drop_percent) || 0;
  const drop30Percent = numericValue(deal.drop_30_percent) || 0;
  const savings = dollarDrop(deal) || 0;
  const freshnessHours = hoursUntil(deal.expires_at);
  const freshnessBonus = freshnessHours === null ? 0 : Math.max(0, Math.min(24, freshnessHours)) / 24;

  return (dropPercent * 2) + drop30Percent + Math.min(savings, 100) + freshnessBonus;
}

function compareText(aValue, bValue) {
  return String(aValue || "").localeCompare(String(bValue || ""), undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

function postedDateValue(deal) {
  return dateValue(deal.posted_at || deal.first_seen_at || deal.checked_at);
}

function checkedDateValue(deal) {
  return dateValue(deal.last_checked_at || deal.checked_at);
}

function expiresDateValue(deal) {
  return dateValue(deal.expires_at);
}

function sortDeals(deals) {
  const sortMode = sortSelect ? sortSelect.value : "best-score";
  const sorted = [...deals];

  sorted.sort((a, b) => {
    if (sortMode === "best-score") {
      const scoreCompare = compareNullableNumbers(dealScore(a), dealScore(b));
      return scoreCompare || postedDateValue(b) - postedDateValue(a);
    }

    if (sortMode === "creator-first") {
      const creatorCompare = Number(hasCreatorCampaign(b)) - Number(hasCreatorCampaign(a));
      const endDateCompare = creatorCampaignEndDateValue(a) - creatorCampaignEndDateValue(b);
      const scoreCompare = compareNullableNumbers(dealScore(a), dealScore(b));
      return creatorCompare || endDateCompare || scoreCompare || postedDateValue(b) - postedDateValue(a);
    }

    if (sortMode === "newest-checked") {
      return checkedDateValue(b) - checkedDateValue(a);
    }

    if (sortMode === "expiring-soon") {
      return expiresDateValue(a) - expiresDateValue(b);
    }

    if (sortMode === "highest-drop") {
      return compareNullableNumbers(a.drop_percent, b.drop_percent);
    }

    if (sortMode === "highest-30-drop") {
      return compareNullableNumbers(a.drop_30_percent, b.drop_30_percent);
    }

    if (sortMode === "highest-dollar-drop") {
      return compareNullableNumbers(dollarDrop(a), dollarDrop(b));
    }

    if (sortMode === "lowest-price") {
      return compareNullableNumbers(a.current_price, b.current_price, "asc");
    }

    if (sortMode === "highest-price") {
      return compareNullableNumbers(a.current_price, b.current_price);
    }

    if (sortMode === "title-az") {
      return compareText(a.title, b.title);
    }

    if (sortMode === "asin-az") {
      return compareText(a.asin, b.asin);
    }

    return postedDateValue(b) - postedDateValue(a);
  });

  return sorted;
}

function imageCandidatesForDeal(deal) {
  const asin = deal.asin;
  const candidates = [];
  const adWidgetImage = asin ? `https://ws-na.amazon-adsystem.com/widgets/q?_encoding=UTF8&MarketPlace=US&ASIN=${asin}&ServiceVersion=20070822&ID=AsinImage&WS=1&Format=_SL500_` : "";

  if (Array.isArray(deal.image_candidates)) {
    deal.image_candidates.forEach((image) => {
      if (image && !String(image).includes("amazon-adsystem.com")) candidates.push(String(image));
    });
  }

  if (deal.image && !deal.image.includes("amazon-adsystem.com")) candidates.push(deal.image);

  if (asin) {
    candidates.push(`https://m.media-amazon.com/images/P/${asin}.01._SL500_.jpg`);
    candidates.push(`https://images-na.ssl-images-amazon.com/images/P/${asin}.01._SL500_.jpg`);
    candidates.push(`https://images.amazon.com/images/P/${asin}.01._SL500_.jpg`);
    candidates.push(`https://images-na.ssl-images-amazon.com/images/P/${asin}.01.LZZZZZZZ.jpg`);
    if (deal.image && deal.image.includes("amazon-adsystem.com")) candidates.push(deal.image);
    if (adWidgetImage) candidates.push(adWidgetImage);
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

function updateCounts(renderedCount, selectedCount, totalMatchingCount) {
  const hiddenCount = hiddenAsins().size;
  const removeCount = removeQueueAsins().size;
  const totalCount = allDeals.length;

  dealCountEl.innerHTML = `${renderedCount} shown of ${totalMatchingCount} visible active deal${totalMatchingCount === 1 ? "" : "s"}`;

  if (selectedCount > 0) {
    dealCountEl.innerHTML += ` <span class="count-note">${selectedCount} selected for posting</span>`;
    dealCountEl.innerHTML += ` <button class="copy-selected" type="button" onclick="copySelectedLinks()">Copy selected links</button>`;
  }

  if (totalCount !== totalMatchingCount) {
    dealCountEl.innerHTML += ` <span class="count-note">${totalCount} total active</span>`;
  }

  dealCountEl.innerHTML += ` <button class="reset-hidden" type="button" onclick="toggleShowAllDeals()">${showAllDealsEnabled() ? "Show posting candidates" : "Show all deals"}</button>`;

  if (hiddenCount > 0) {
    dealCountEl.innerHTML += ` <button class="reset-hidden" type="button" onclick="resetHiddenDeals()">Show hidden (${hiddenCount})</button>`;
  }

  if (selectedCount > 0) {
    dealCountEl.innerHTML += ` <button class="clear-selected" type="button" onclick="clearSelectedForPosting()">Clear selected</button>`;
  }

  if (removeCount > 0) {
    dealCountEl.innerHTML += ` <button class="copy-remove" type="button" onclick="copyRemoveQueue()">Copy removals (${removeCount})</button>`;
    dealCountEl.innerHTML += ` <button class="clear-remove" type="button" onclick="clearRemoveQueue()">Clear removals</button>`;
  }

}

function buildCard(deal, isSelected, isSelectedSection) {
  const card = document.createElement("article");
  card.className = isSelected ? "card selected-card" : "card";
  const postedAt = deal.posted_at || deal.first_seen_at || deal.checked_at;
  const expiresAt = deal.expires_at;
  const hoursLeft = hoursUntil(expiresAt);
  const expiresText = hoursLeft === null ? "N/A" : `${hoursLeft.toFixed(1)} hrs left`;
  const dealJson = JSON.stringify(deal).replace(/</g, "\\u003c").replace(/'/g, "\\u0027");

  const publishRows = dashboardMode === "best-sellers" ? `
      <div class="publish-tool-row">
        <span>Black Lab</span>
        <button type="button" onclick='publishDeal(${dealJson}, "blackLabPage", 0, this)'>Now</button>
        <button type="button" onclick='publishDeal(${dealJson}, "blackLabPage", 60, this)'>60</button>
        <button type="button" onclick='publishDeal(${dealJson}, "blackLabPage", 90, this)'>90</button>
        <button type="button" onclick='publishDeal(${dealJson}, "blackLabPage", 120, this)'>120</button>
      </div>
  ` : `
      <div class="publish-tool-row">
        <span>Wood Page</span>
        <button type="button" onclick='publishDeal(${dealJson}, "woodworkingPage", 0, this)'>Now</button>
        <button type="button" onclick='publishDeal(${dealJson}, "woodworkingPage", 60, this)'>60</button>
        <button type="button" onclick='publishDeal(${dealJson}, "woodworkingPage", 90, this)'>90</button>
        <button type="button" onclick='publishDeal(${dealJson}, "woodworkingPage", 120, this)'>120</button>
      </div>
  `;

  const selectedPostingTools = isSelectedSection ? `
    <div class="posting-helper-box">
      <p>Publish direct to Publer pages.</p>
      ${publishRows}
      <button class="posted-hide-card" type="button" onclick="hideDeal('${deal.asin}')">Posted - Hide Card</button>
    </div>
  ` : "";

  card.innerHTML = `
    <div class="select-posting-row">
      <label class="select-posting-control">
        <input type="checkbox" ${isSelected ? "checked" : ""} onchange="toggleSelectedForPosting('${deal.asin}')">
        <span>Select for posting</span>
      </label>
      ${isSelected ? `<span class="selected-pill">Selected</span>` : ""}
    </div>
    <a class="image-wrap" href="${deal.amazon_url}" target="_blank" rel="noopener noreferrer" aria-label="Open ${deal.title} on Amazon">
      ${buildImageMarkup(deal)}
      <div class="image-placeholder">
        <span>No image available</span>
        <small>${deal.asin}</small>
      </div>
    </a>
    <div class="card-body">
      <div class="card-top-row">
        ${primaryDealBadge(deal)}
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
          ${livePriceNote(deal)}
          ${couponNote(deal)}
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
        ${keepaOfferNote(deal)}
      </div>
      ${selectedPostingTools}
      <a class="button" href="${deal.amazon_url}" target="_blank" rel="noopener noreferrer">Open on Amazon</a>
    </div>
  `;

  return card;
}

function renderDeals(deals) {
  currentRenderedDeals = deals;

  const selectedAsins = selectedForPostingAsins();
  const selectedDeals = deals.filter((deal) => selectedAsins.has(deal.asin));
  const regularDeals = deals.filter((deal) => !selectedAsins.has(deal.asin));
  const visibleRegularDeals = regularDeals.slice(0, shownRegularDealLimit);

  cardsEl.innerHTML = "";
  selectedCardsEl.innerHTML = "";

  const totalRendered = selectedDeals.length + visibleRegularDeals.length;
  emptyStateEl.hidden = totalRendered !== 0;
  updateCounts(totalRendered, selectedDeals.length, deals.length);

  selectedPostingSectionEl.hidden = selectedDeals.length === 0;
  selectedPostingCountEl.textContent = `${selectedDeals.length} selected`;

  selectedDeals.forEach((deal) => {
    selectedCardsEl.appendChild(buildCard(deal, true, true));
  });

  visibleRegularDeals.forEach((deal) => {
    cardsEl.appendChild(buildCard(deal, false, false));
  });

  updateLoadMoreControls(visibleRegularDeals.length, regularDeals.length);
}

function applySearch(resetLimit = true) {
  if (resetLimit) {
    resetDealLimit();
  }

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
    const response = await fetch(dashboardDataUrl, { cache: "no-store" });
    if (!response.ok) throw new Error(`Could not load ${dashboardDataUrl}`);

    const data = await response.json();
    const creatorConnections = data.creator_connections || {};
    const creatorUpdatedAt = creatorConnections.latest_csv_updated_at
      ? ` - Creator CSV: ${formatDate(creatorConnections.latest_csv_updated_at)}`
      : "";
    allDeals = data.deals || [];
    updatedAtEl.textContent = `Last updated: ${formatDate(data.updated_at)} - Deals kept for ${data.deal_ttl_hours || 24} hours${creatorUpdatedAt}`;
    applySearch();
  } catch (error) {
    dealCountEl.textContent = "Could not load deal data";
    updatedAtEl.textContent = error.message;
    emptyStateEl.hidden = false;
  }
}

searchInput.addEventListener("input", () => applySearch());
if (sortSelect) sortSelect.addEventListener("change", () => applySearch());
initAsinAddForm();
initAsinBulkRemoveForm();
// Creator upload is handled by creator-upload.js.
loadDeals();
