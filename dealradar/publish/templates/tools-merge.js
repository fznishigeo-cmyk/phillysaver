// tools_merge (operator 2026-10-23): Fakespot's report rows, filled only from the grade the
// page already received and the published prices file. No extra lookup, no star ratings,
// and no past-price wording (operator 2026-10-17: history stays a hidden signal).
function mergeAge(asOf) {
  var seconds = Math.max(0, Math.floor((Date.now() - Date.parse(asOf)) / 1000)), minutes = Math.floor(seconds / 60);
  var hours = Math.floor(minutes / 60), days = Math.floor(hours / 24);
  return (seconds < 60 ? seconds + ' sec' : minutes < 60 ? minutes + ' min' : hours < 24 ? hours + (hours === 1 ? ' hr' : ' hrs') :
    days + (days === 1 ? ' day' : ' days')) + ' ago';
}
var MERGE_ICONS = {
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
  people: '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c.6-3.6 3-5.5 6-5.5s5.4 1.9 6 5.5M16 5.5a3 3 0 0 1 0 5.6M18 14.8c1.7.8 2.7 2.5 3 5.2"/>',
  box: '<path d="M3 7l9-4 9 4v10l-9 4-9-4V7z"/><path d="M3 7l9 4 9-4M12 11v10"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>'
};
function mergeFact(icon, text) {
  return '<li class="fact-' + icon + '"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + MERGE_ICONS[icon] + '</svg><span>' + esc(text) + '</span></li>';
}
function mergeAsin(url) {
  try {
    var parsed = new URL(url);
    if (parsed.protocol !== 'https:' || !/^(?:(?:www|smile|m)\.)?amazon\.com$/.test(parsed.hostname)) return '';
    var match = parsed.pathname.match(/\/(?:dp|gp\/product)\/([a-z0-9]{10})(?:\/|$)/i);
    return match ? match[1].toUpperCase() : '';
  } catch (_) { return ''; }
}
var mergePrices;
// "On wowpriced": our published verified deals for the same product (static file, no lookup).
function mergeOurs(item) {
  var box = document.getElementById('report-ours');
  box.dataset.asin = item.asin;
  box.innerHTML = '<p class="muted">Looking for our verified deals…</p>';
  mergePrices = mergePrices || fetch('/deal-spotter-prices.json', {credentials:'omit'})
    .then(response => { if (!response.ok) throw Error('unavailable'); return response.json(); })
    .catch(() => { mergePrices = null; return {failed:true}; });
  mergePrices.then(doc => {
    if (box.dataset.asin !== item.asin) return;
    var rows = (Array.isArray(doc.deals) ? doc.deals : []).concat((Array.isArray(doc.spotlight) ? doc.spotlight : [])
      .filter(d => d && d.spotlight && Date.parse(d.spotlight.until) > Date.now()));
    var mine = rows.filter(d => d && d.status === 'alive' && typeof d.final_price === 'number' && Number.isFinite(d.final_price) &&
      d.final_price > 0 && mergeAsin(d.url) === item.asin).sort((a, b) => a.final_price - b.final_price);
    if (!mine.length) {
      box.innerHTML = '<p>' + (doc.failed ? 'Our verified deals could not be loaded just now.' :
        'No verified wowpriced deal for this product right now.') + '</p>' +
        '<a class="text-link" href="/best-deals-today/">See today’s best deals <span aria-hidden="true">→</span></a>';
      return;
    }
    var d = mine[0], page = typeof d.deal_path === 'string' && /^\/deal\/[a-z0-9-]+\/$/.test(d.deal_path);
    box.innerHTML = '<p class="ours-line"><span class="ours-chip">Verified deal</span> <strong>$' + d.final_price.toFixed(2) +
      '</strong> on wowpriced, price checked against its source</p>' + (page ?
      '<a class="secondary" href="' + esc(d.deal_path) + '">See this deal on wowpriced <span aria-hidden="true">→</span></a>' :
      '<a class="secondary" href="' + esc(toolAmazon(item.asin)) + '" target="_blank" rel="sponsored nofollow noopener">See it on Amazon <span aria-hidden="true">↗</span></a>');
  });
}
function mergeReport(item, grade) {
  var $ = id => document.getElementById(id);
  $('report-checked').textContent = mergeAge(item.as_of);
  var category = typeof item.category === 'string' ? item.category.trim() : '';
  $('report-category').textContent = category; $('report-category-item').hidden = !category;
  // Amazon's list price is plain information here, never a verdict.
  $('result-list').textContent = Number.isSafeInteger(item.list_price_cents) && item.list_price_cents > item.price_cents ?
    'Amazon list price: $' + (item.list_price_cents / 100).toFixed(2) : '';
  var facts = Array.isArray(grade.facts) ? grade.facts : [], lines = [];
  if (facts.includes('Verified deal on wowpriced')) lines.push(mergeFact('shield', 'A verified deal on wowpriced'));
  var monthly = monthlyBoughtLabel(item.monthlySold);
  if (monthly) lines.push(mergeFact('people', 'Popular: ' + monthly));
  if (Number.isSafeInteger(item.shipping_cents) && item.shipping_cents > 0)
    lines.push(mergeFact('box', 'The grade counts the $' + (item.shipping_cents / 100).toFixed(2) + ' shipping'));
  if (lines.length < 2) lines.push(mergeFact('info', 'The grade describes the price, not the product'));
  $('result-facts').innerHTML = lines.slice(0, 4).join('');
  mergeOurs(item);
  document.querySelectorAll('.helpful [data-helpful]').forEach(button => { button.disabled = false; button.removeAttribute('aria-pressed'); });
  $('helpful-status').textContent = '';
  var help = document.querySelector('.grade-help'); if (help) help.open = false;
}
// Fakespot's guide tabs: Price grade · Verified deals · Price details · Find discounts.
function mergeGuide() {
  var tabs = [...document.querySelectorAll('.guide-tabs [role="tab"]')];
  function show(index, focus) {
    tabs.forEach((tab, i) => {
      tab.setAttribute('aria-selected', String(i === index)); tab.tabIndex = i === index ? 0 : -1;
      document.getElementById(tab.getAttribute('aria-controls')).hidden = i !== index;
    });
    if (focus) tabs[index].focus();
  }
  tabs.forEach((tab, i) => {
    tab.addEventListener('click', () => show(i));
    tab.addEventListener('keydown', event => {
      var next = {ArrowRight:i + 1, ArrowLeft:i - 1, Home:0, End:tabs.length - 1}[event.key];
      if (next === undefined) return;
      event.preventDefault(); show((next + tabs.length) % tabs.length, true);
    });
  });
}
// The old tools' ?q= links (bookmarks, the homepage buttons) arrive as typed words or a link.
function mergeQuery(params, submit) {
  var typed = params.get('q');
  if (params.getAll('q').length !== 1 || !typed || !typed.trim() || typed.length > 300) return;
  document.getElementById('q').value = typed;
  submit(typed.trim(), document.getElementById('q'));
}
(function () {
  var box = document.querySelector('.helpful');
  if (!box) return;
  // GA4 only (tracking's tool_feedback): which answer, nothing about the product or visitor.
  box.addEventListener('click', event => {
    var button = event.target.closest && event.target.closest('[data-helpful]');
    if (!button || button.disabled) return;
    box.querySelectorAll('[data-helpful]').forEach(other => { other.disabled = true; other.setAttribute('aria-pressed', String(other === button)); });
    document.getElementById('helpful-status').textContent = 'Thanks for telling us.';
    try { if (typeof window.wpTrack === 'function') window.wpTrack('tool_feedback', {method:button.dataset.helpful}); } catch (_) { /* tags only */ }
  });
})();
