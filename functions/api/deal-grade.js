// Task 366. Pure price grading; no route handler and no upstream requests.
// All amounts are cents. Historical inputs must stay inside the server/cache.
const cents = value => Number.isSafeInteger(value) && value > 0;

export function validDealSettings(cfg) {
  return !!cfg && cfg.enabled === true &&
    [cfg.a_max_avg_ratio, cfg.b_max_avg_ratio, cfg.b_min_list_pct, cfg.verified_max_ratio]
      .every(n => typeof n === 'number' && Number.isFinite(n)) &&
    cfg.a_max_avg_ratio > 0 && cfg.a_max_avg_ratio <= cfg.b_max_avg_ratio && cfg.b_max_avg_ratio <= 1 &&
    cfg.b_min_list_pct >= 0 && cfg.b_min_list_pct <= 100 &&
    cfg.verified_max_ratio >= 1 && cfg.verified_max_ratio <= 1.1;
}

export function gradeDeal(input = {}, cfg = {}) {
  const {price_cents:price, list_price_cents:list, avg90, verified_price_cents:verified} = input;
  const facts = [];
  let grade = 'C';
  if (cents(price) && validDealSettings(cfg)) {
    const discount = cents(list) && list > price ? (list - price) / list * 100 : null;
    if (discount !== null) facts.push(Math.floor(discount) + '% below the list price');
    if (cents(verified)) facts.push('Verified deal on wowpriced');
    if ((cents(avg90) && price <= cfg.a_max_avg_ratio * avg90) ||
        (cents(verified) && price <= verified * cfg.verified_max_ratio)) grade = 'A';
    else if ((cents(avg90) && price <= cfg.b_max_avg_ratio * avg90) ||
        (!cents(avg90) && discount !== null && discount >= cfg.b_min_list_pct)) grade = 'B';
  }
  return {grade, label:{A:'Great price', B:'Good price', C:'Fair price'}[grade], facts};
}

export function publishedDealPrice(doc, asin, now = Date.now()) {
  const rows = (Array.isArray(doc?.deals) ? doc.deals : []).concat(
    (Array.isArray(doc?.spotlight) ? doc.spotlight : []).filter(d => Date.parse(d?.spotlight?.until) > now));
  let price;
  for (const d of rows) {
    // Published rows passed the price checks; badge only counts agreeing sources.
    if (!d || d.locked || d.status !== 'alive' ||
        d.price_unconfirmed || d.price_aged || d.click_price_unknown || /not yet confirmed/.test(d.headline || '') ||
        typeof d.final_price !== 'number' || !Number.isFinite(d.final_price) || d.final_price <= 0) continue;
    try {
      const url = new URL(d.url);
      if (url.protocol !== 'https:' || url.username || url.password || url.port ||
          !/^(?:(?:www|smile|m)\.)?amazon\.com$/.test(url.hostname)) continue;
      const match = url.pathname.match(/\/(?:dp|gp\/product|gp\/aw\/d|exec\/obidos(?:\/ASIN)?)\/([a-z0-9]{10})(?:\/|$)/i);
      if ((match?.[1] || url.searchParams.get('asin') || '').toUpperCase() !== asin) continue;
      const amount = Math.round(d.final_price * 100);
      if (cents(amount)) price = price === undefined ? amount : Math.min(price, amount);
    } catch (_) { /* Only the publisher's valid Amazon product links qualify. */ }
  }
  return price;
}
