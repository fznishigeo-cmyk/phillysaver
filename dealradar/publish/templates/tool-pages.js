var analyzeExclude = toolSettings.exclude ? new RegExp(toolSettings.exclude, "iu") : null;
function analyzeAdult(text) { return !!analyzeExclude && analyzeExclude.test(text); }
function searchText() { return document.getElementById("q").value.trim(); }
function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, ch =>
    ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[ch]));
}
function track(name, params) {
  if (typeof window.wpTrack === "function") window.wpTrack(name, params || {});
}
function toolQuery(value) {
  return searchAmazonQuery(value);
}
function toolDiscount(value) {
  var input = analyzeInput(value), link = discountsLink();
  var raw = link ? link.words : value;
  var words = input.kind === "query" || link ? toolQuery(raw) : "";
  if (link) words = words.split(/\s+/).slice(0, 8).join(" ");
  if (analyzeAdult(raw)) { words = ""; link = null; }
  var asin = link && link.asin || "";
  var url = "https://www.amazon.com/" + (words ? "s?k=" + encodeURIComponent(words) +
    "&rh=p_n_deal_type%3A23566065011&" : asin ? "dp/" + asin + "?" : "deals?") +
    "tag=" + encodeURIComponent(toolSettings.tag);
  return {words:words, asin:asin, url:url};
}
function toolAmazon(asin) {
  return "https://www.amazon.com/dp/" + asin + "?tag=" + encodeURIComponent(toolSettings.tag);
}
function toolLink(url) {
  try {
    var parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.port) return "";
    if (/^(?:(?:www|smile|m)\.)?amazon\.com$/.test(parsed.hostname)) {
      var asin = analyzeInput(url).asin;
      return asin ? toolAmazon(asin) : "";
    }
    return parsed.href;
  } catch (_) { return ""; }
}
function toolPhoto(url, title) {
  if (typeof url !== "string" || !/^https:\/\//.test(url)) return "";
  try { var parsed = new URL(url); if (parsed.username || parsed.password || parsed.port) return ""; }
  catch (_) { return ""; }
  return '<div class="photo"><img src="' + esc(url) + '" alt="' + esc(title) +
    '" width="300" height="300" loading="lazy" decoding="async" referrerpolicy="no-referrer"></div>';
}
function toolButton(url, label, event) {
  return '<a class="amazon-button" href="' + esc(url) + '" target="_blank" rel="sponsored noopener" data-tool-event="' +
    event + '">' + esc(label) + '</a>';
}
function toolBadgeHtml(price, list) {
  var badge = toolBadge(price, list);
  return '<span class="badge ' + badge.kind + '">' + esc(badge.label) + '</span>';
}
function toolPrice(item) {
  var list = Number.isSafeInteger(item.list_price_cents) && item.list_price_cents > item.price_cents;
  return '<div class="price">$' + (item.price_cents / 100).toFixed(2) + '</div>' +
    (list ? '<p class="price-note"><s>$' + (item.list_price_cents / 100).toFixed(2) + '</s> Amazon list price</p>' : '');
}
function toolLiveCard(item, lead) {
  var time = Math.max(0, Math.floor((Date.now() - Date.parse(item.as_of)) / 1000));
  var heading = '<' + (lead ? 'h2' : 'h3') + '>' + esc(item.title) + '</' + (lead ? 'h2' : 'h3') + '>';
  var badge = toolBadgeHtml(item.price_cents, item.list_price_cents);
  var body = heading + toolPrice(item) +
    (lead ? '<p class="price-note" data-price-time="' + esc(item.as_of) + '">price checked ' + time + ' sec ago on Amazon</p>' : '') +
    (monthlyBoughtLabel(item.monthlySold) ? '<div class="monthly">' + monthlyBoughtLabel(item.monthlySold) + '</div>' : '') +
    toolButton(toolAmazon(item.asin), 'View at Amazon' + (lead ? ' →' : ''), 'search_amazon');
  return lead ? toolPhoto(item.image, item.title) + '<div>' + badge + body + '</div>' :
    '<article class="product-card">' + badge + toolPhoto(item.image, item.title) + body + '</article>';
}
function toolVerified(d) {
  return (d.badge === "verified" || d.badge === "history-checked") &&
    !d.price_unconfirmed && !d.price_aged && !d.click_price_unknown &&
    !/not yet confirmed/.test(d.headline || "");
}
function toolDealCard(d) {
  var url = toolLink(d.url), amazon = /^https:\/\/www\.amazon\.com\//.test(url);
  if (!url || typeof d.final_price !== "number" || !Number.isFinite(d.final_price) || d.final_price <= 0) return "";
  // Only the published current list-price basis can enter an Amazon badge.
  var price = Math.round(d.final_price * 100), list = amazon && d.regular_price_basis === "List price" &&
    typeof d.regular_price === "number" ? Math.round(d.regular_price * 100) : null;
  var steps = d.needs_steps && /^\/deal\/[a-z0-9-]+\/$/.test(d.deal_path || "") ?
    '<a class="price-steps" href="' + esc(d.deal_path) + '">How to get this price</a>' : '';
  if (d.needs_steps && !steps) return "";
  return '<article class="product-card">' + (amazon ? toolBadgeHtml(price, list) : '<span class="badge">Verified deal</span>') +
    toolPhoto(d.image_url, d.item) + '<h3>' + esc(d.item) + '</h3>' + toolPrice({price_cents:price, list_price_cents:list}) +
    (monthlyBoughtLabel(d.monthlySold) ? '<div class="monthly">' + monthlyBoughtLabel(d.monthlySold) + '</div>' : '') +
    steps + toolButton(url, 'View at ' + (amazon ? 'Amazon' : d.store || 'store'), amazon ? 'search_amazon' : 'deal_click') + '</article>';
}
var toolInventoryPromise, toolSeen = new Map(), toolActive;
function toolInventory() {
  if (!toolInventoryPromise) {
    var controller = new AbortController(), timer;
    toolInventoryPromise = Promise.race([
      fetch('/deals.json', {cache:'no-store', credentials:'omit', signal:controller.signal})
        .then(response => { if (!response.ok) throw Error('unavailable'); return response.json(); }),
      new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(Error('unavailable')); }, 6000); })
    ]).then(data => {
      var seen = new Set();
      return (data.deals || []).concat((data.spotlight || []).filter(d =>
        d.spotlight && Date.parse(d.spotlight.until) > Date.now())).filter(d => {
        var key = d.key || d.dedupe_key;
        if (!key || seen.has(key) || d.locked || d.status !== 'alive' || analyzeAdult(d.item) || !toolVerified(d)) return false;
        seen.add(key); return true;
      });
    }).catch(() => { toolInventoryPromise = null; return []; }).finally(() => clearTimeout(timer));
  }
  return toolInventoryPromise;
}
function toolCurrent(task) { return toolActive === task && !task.cancelled; }
function toolPause(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
async function toolLookup(task) {
  if (toolSeen.has(task.key)) return toolSeen.get(task.key);
  for (var retry = 0; retry < 3 && toolCurrent(task); retry++) {
    var controller = new AbortController(), timer, data;
    task.controller = controller;
    try {
      data = await Promise.race([
        fetch('/api/search?' + task.key, {signal:controller.signal, cache:'no-store', credentials:'omit', referrerPolicy:'no-referrer'})
          .then(response => response.json()),
        new Promise((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(Error('timeout')); }, 6000); })
      ]);
      if (!data || typeof data !== 'object') data = {items:[], reason:'unavailable'};
    } catch (error) { data = {items:[], reason:error.message === 'timeout' ? 'timeout' : 'unavailable'}; }
    finally { clearTimeout(timer); }
    if (!toolCurrent(task)) return {items:[]};
    if (['rate_limited', 'timeout'].includes(data.reason) && retry < 2) { await toolPause(1500); continue; }
    if (toolSeen.size >= 50) toolSeen.delete(toolSeen.keys().next().value);
    toolSeen.set(task.key, data);
    return data;
  }
  return {items:[], reason:'unavailable'};
}
function toolCancel() {
  if (toolActive) {
    toolActive.cancelled = true;
    if (toolActive.controller) toolActive.controller.abort();
  }
  toolActive = null;
}
function toolStepState(task, index, state) {
  if (!toolCurrent(task)) return;
  task.steps[index].dataset.state = state;
  document.getElementById('tool-status').textContent = task.steps[index].textContent + (state === 'done' ? ' — done' : state === 'failed' ? ' — unavailable' : '');
  var done = task.steps.filter(step => step.dataset.state === 'done').length;
  var progress = document.getElementById('tool-progress');
  progress.setAttribute('aria-valuenow', String(Math.round(done / task.steps.length * 100)));
  progress.firstElementChild.style.width = done / task.steps.length * 100 + '%';
}
async function toolStep(task, index, work) {
  if (!toolCurrent(task)) throw Error('cancelled');
  task.index = index;
  toolStepState(task, index, 'working');
  await work();
  if (!toolCurrent(task)) throw Error('cancelled');
  if (!task.reduced) {
    // Milestones are a minimum reading time; completed work gates every tick.
    var target = task.started + (index + 1) * 9000 / task.steps.length;
    await toolPause(Math.max(120, target - performance.now()));
  }
  if (!toolCurrent(task)) throw Error('cancelled');
  toolStepState(task, index, 'done');
}
function toolPaintDeals(rows) {
  var section = document.getElementById('verified-deals');
  section.querySelector('.product-grid').innerHTML = rows.map(toolDealCard).join('');
  section.hidden = !section.querySelector('.product-card');
}
function toolFailure(task, reason) {
  var result = document.getElementById('tool-result');
  var query = task.input.asin || task.words || '';
  var url = 'https://www.amazon.com/' + (query && !analyzeAdult(query) ? 's?k=' + encodeURIComponent(query) + '&' : '?') +
    'tag=' + encodeURIComponent(toolSettings.tag);
  var finder = document.body.dataset.tool === 'finder';
  result.innerHTML = '<div><h2>' + esc(analyzeReason(reason)) + '</h2>' +
    toolButton(finder ? task.discountUrl : url, finder ? 'See all Amazon discounts →' : 'Search Amazon',
      finder ? 'search_discounts' : 'search_amazon') + '</div>';
  result.hidden = false;
}
async function toolSubmit(event) {
  event.preventDefault();
  var raw = searchText();
  if (!raw || toolActive && !toolActive.finished && toolActive.raw === raw) return;
  toolCancel();
  var finder = document.body.dataset.tool === 'finder', input = analyzeInput(raw);
  var task = {raw:raw, input:input, started:performance.now(),
    steps:Array.from(document.querySelectorAll('.steps li')), index:0,
    reduced:matchMedia('(prefers-reduced-motion: reduce)').matches};
  toolActive = task;
  for (var step of task.steps) step.dataset.state = 'pending';
  for (var id of ['tool-result', 'top-sellers', 'verified-deals']) document.getElementById(id).hidden = true;
  var analyzer = document.getElementById('analyzer');
  analyzer.hidden = false; analyzer.setAttribute('aria-busy', 'true');
  document.getElementById('tool-progress').setAttribute('aria-valuenow', '0');
  document.getElementById('tool-progress').firstElementChild.style.width = '0%';
  document.getElementById('analyzer-title').textContent = finder ? 'Finding discounts for your product' : 'Analyzing your product';
  track(finder ? 'search_discounts' : 'analyze');
  if (!finder) track('search_analyze', {link:input.kind !== 'query'});
  var inventory = toolInventory(), matches = [];
  try {
    if (finder) {
      var discount;
      await toolStep(task, 0, () => {
        discount = toolDiscount(raw);
        task.discountUrl = discount.url;
        if (!discount.words && !discount.asin) throw Error(analyzeAdult(raw) ? 'excluded' :
          input.kind === 'query' ? 'invalid_query' : input.kind);
      });
      await toolStep(task, 1, () => { task.discountUrl = discount.url; });
      await toolStep(task, 2, async () => {
        var words = searchWords(discount.words);
        matches = (await inventory).filter(d => matchesSearch(d, words)).slice(0, 6);
      });
      await toolStep(task, 3, () => {
        task.result = '<div><h2>' + esc(discount.words || (discount.asin ? 'Amazon product ' + discount.asin : 'Amazon discounts')) + '</h2>' +
          toolButton(task.discountUrl, 'See all Amazon discounts →', 'search_discounts') + '</div>';
      });
    } else {
      var items, lead, sellers;
      task.words = input.kind === 'query' ? toolQuery(raw) : '';
      await toolStep(task, 0, async () => {
        if (!['amazon', 'query'].includes(input.kind)) throw Error(input.kind);
        if (analyzeAdult(raw)) throw Error('excluded');
        if (!toolSettings.live) throw Error('disabled');
        if (!input.asin && !task.words) throw Error('invalid_query');
        task.key = input.asin ? 'asin=' + input.asin : 'q=' + encodeURIComponent(task.words.toLowerCase().replace(/\s+/g, ' '));
        var data = await toolLookup(task);
        if (data.reason) throw Error(data.reason);
        items = analyzeItems(data.items, '');
        lead = input.asin ? items.find(d => d.asin === input.asin) : items[0];
        if (!lead) throw Error('no_price');
      });
      await toolStep(task, 1, () => { if (!analyzeItems([lead], input.asin).length) throw Error('no_price'); });
      await toolStep(task, 2, async () => {
        var rows = await inventory;
        matches = input.asin ? await analyzeCompare(rows, lead.title, input.asin, null) :
          rows.filter(d => matchesSearch(d, searchWords(task.words))).slice(0, 6);
      });
      await toolStep(task, 3, () => {
        // ASIN requests currently return one item. Never buy another request
        // to fill this grid; text requests already return up to five sellers.
        sellers = (input.asin ? items.filter(d => d.asin !== input.asin) : items).slice(0, 5);
      });
      await toolStep(task, 4, () => { task.result = toolLiveCard(lead, true); });
      // Build the lead after the minimum reading time, so its clock is exact.
      task.result = toolLiveCard(lead, true);
      var top = document.getElementById('top-sellers');
      top.querySelector('.product-grid').innerHTML = sellers.map(d => toolLiveCard(d, false)).join('');
      top.hidden = !sellers.length;
      track('analyze_result', {count:Math.min(16, 1 + sellers.length + matches.length), live:true, matched:!!matches.length});
    }
    if (!toolCurrent(task)) return;
    var result = document.getElementById('tool-result');
    result.innerHTML = task.result; result.hidden = false;
    toolPaintDeals(matches);
  } catch (error) {
    if (!toolCurrent(task)) return;
    toolStepState(task, task.index, 'failed');
    toolFailure(task, error.message);
    if (!finder && input.kind === 'query' && task.words && !analyzeAdult(raw)) {
      matches = (await inventory).filter(d => matchesSearch(d, searchWords(task.words))).slice(0, 6);
      if (toolCurrent(task)) toolPaintDeals(matches);
    }
  } finally {
    if (toolCurrent(task)) { task.finished = true; analyzer.setAttribute('aria-busy', 'false'); }
  }
}
// TOOL_PAGE_MOUNT
document.getElementById('tool-form').addEventListener('submit', toolSubmit);
document.getElementById('q').addEventListener('input', () => {
  toolCancel();
  document.getElementById('analyzer').hidden = true;
  for (var id of ['tool-result', 'top-sellers', 'verified-deals']) document.getElementById(id).hidden = true;
});
for (var event of ['click', 'auxclick']) document.addEventListener(event, e => {
  if ((e.type === 'auxclick' ? e.button !== 1 : e.button !== 0)) return;
  var link = e.target.closest('[data-tool-event]');
  if (link) track(link.dataset.toolEvent);
});
setInterval(() => document.querySelectorAll('[data-price-time]').forEach(el => {
  el.textContent = 'price checked ' + Math.max(0, Math.floor((Date.now() - Date.parse(el.dataset.priceTime)) / 1000)) + ' sec ago on Amazon';
}), 1000);
track('page_view', {source:'tool_page'});
