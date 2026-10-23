// tools_merge (operator 2026-10-23): typed words (not a link) never spend a lookup (no
// /api/search call). One box: Amazon's discounts for the words (the Discount Finder's own
// link), Amazon's highly rated list, and our matching verified deals from /deals.json.
function wordsCard(d) {
  var steps = /^\/deal\/[a-z0-9-]+\/$/.test(d.deal_path || '');
  var url = d.needs_steps ? (steps ? d.deal_path : '') : toolLink(d.url);
  if (!url || typeof d.final_price !== 'number' || !Number.isFinite(d.final_price) || d.final_price <= 0) return '';
  var outside = /^https:/.test(url);
  var photo = typeof d.image_url === 'string' && /^https:\/\//.test(d.image_url) ? '<img src="' + esc(d.image_url) +
    '" alt="" width="96" height="96" loading="lazy" decoding="async" referrerpolicy="no-referrer">' : '<span class="words-photo" aria-hidden="true"></span>';
  return '<a class="words-deal" href="' + esc(url) + '"' + (outside ? ' target="_blank" rel="sponsored noopener"' : '') + '>' + photo +
    '<span class="words-deal-title">' + esc(d.item) + '</span><strong>$' + d.final_price.toFixed(2) + '</strong><span class="words-deal-cta">' +
    (outside ? 'Get deal <span aria-hidden="true">↗</span>' : 'How to get this price') + '</span></a>';
}
function wordsDeals(panel, holder, words) {
  toolInventory().then(rows => {
    if (panel.dataset.words !== words || panel.hidden) return;
    var terms = searchWords(words), cards = rows.filter(d => matchesSearch(d, terms)).map(wordsCard).filter(Boolean).slice(0, 4);
    if (!cards.length) return;
    var heading = document.createElement('h3'), grid = document.createElement('div');
    heading.textContent = 'Matching verified deals on wowpriced'; grid.className = 'words-cards'; grid.innerHTML = cards.join('');
    holder.replaceChildren(heading, grid); holder.hidden = false;
  });
}
// Fills the panel and returns its first link, or null (no box) for adult or unusable words.
function namePanel(panel, raw) {
  var holder = document.createElement('div'); holder.id = panel.id;
  var rated = namePanelRated(holder, raw), hint = [...holder.children].slice(1);  // its paste-a-link hint
  panel.replaceChildren(); panel.hidden = !rated; delete panel.dataset.words;
  if (!rated) return null;
  var words = toolQuery(raw), discount = toolDiscount(raw), first = rated;
  var box = document.createElement('div'), title = document.createElement('p'), strong = document.createElement('strong');
  var buttons = document.createElement('div'), deals = document.createElement('div');
  box.className = 'words-box'; title.className = 'words-title'; buttons.className = 'words-buttons';
  deals.className = 'words-deals'; deals.hidden = true;
  strong.textContent = words; title.append('Shopping for ', strong, '?');
  if (discount.words) {
    var link = document.createElement('a'), arrow = document.createElement('span');
    link.className = 'words-discounts'; link.href = discount.url; link.target = '_blank'; link.rel = 'sponsored noopener';
    arrow.textContent = ' →'; arrow.setAttribute('aria-hidden', 'true');
    link.append('See ' + discount.words + ' discounts on Amazon', arrow);
    link.setAttribute('aria-describedby', panel.id + '-hint');
    buttons.append(link); first = link;
  }
  buttons.append(rated);
  box.append(title, buttons, ...hint, deals);
  panel.append(box); panel.dataset.words = words;
  wordsDeals(panel, deals, words);
  return first;
}
