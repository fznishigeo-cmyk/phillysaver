// Browser health probes; outbound deal links are inspected only as strings.
const {pixels, blankBands} = require('./browser_pixels.cjs');
const {textFindings, dealLinkProblem, BLOCK} = require('./visitor_watch_logic.cjs');

async function boundedScreenshot(page, deadline) {
  const remaining = deadline - Date.now();
  if (remaining <= 0) throw new Error('screenshot deadline expired');
  return page.screenshot({animations: 'disabled', caret: 'hide', timeout: Math.min(3000, remaining)});
}

async function photoState(card, timeout = 1500, selector = 'img') {
  return card.evaluate(async (el, {timeout, selector}) => {
    const images = [...el.querySelectorAll(selector)];
    let timer;
    try {
      await Promise.race([Promise.all(images.map(img => img.decode().catch(() => {}))),
        new Promise(resolve => {timer = setTimeout(resolve, timeout);})]);
    } finally {clearTimeout(timer);}
    const loaded = images.some(img => {
      const box = img.getBoundingClientRect();
      return img.complete && img.naturalWidth > 0 && img.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) &&
        box.width > 1 && box.height > 1 && box.bottom > 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth;
    });
    return loaded ? 'loaded' : images.length ? 'broken' : 'placeholder';
  }, {timeout, selector});
}

async function scanScreens(page, selector, deadline) {
  const cards = new Map(), boxes = new Map(), bands = [];
  let overflow = 0;
  for (let screen = 0; screen < 3; screen++) {
    await page.evaluate(screen => window.scrollTo(0, screen * innerHeight), screen);
    await page.waitForTimeout(100);
    const visible = await page.locator(selector).evaluateAll(elements => elements.flatMap((el, index) => {
      const box = el.getBoundingClientRect();
      return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) && box.width > 1 && box.height > 1 &&
        box.bottom > 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth ?
        [{index, key: el.dataset.key || String(index)}] : [];
    }));
    for (const card of visible) {
      const photo = await photoState(page.locator(selector).nth(card.index), Math.max(1, Math.min(1500, deadline - Date.now())));
      // A partially visible image can be outside this viewport. Keep a loaded
      // observation if any of the three screens actually displays the photo.
      if (cards.get(card.key)?.photo !== 'loaded') cards.set(card.key, {key: card.key, photo});
    }
    const geometry = await page.evaluate(selector => {
      const rect = el => {const r = el.getBoundingClientRect(); return {left: r.left, right: r.right,
        top: r.top, bottom: r.bottom, height: r.height};};
      const content = document.querySelector('#content') || document.querySelector('main');
      return {scrollY, content: content ? rect(content) : null,
        overflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth) - innerWidth,
        boxes: [...document.querySelectorAll(selector)].flatMap((el, index) => {
          const r = rect(el);
          return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) && r.height > 1 && r.bottom > 0 && r.top < innerHeight ?
            [{...r, top: r.top + scrollY, bottom: r.bottom + scrollY, key: el.dataset.key || String(index)}] : [];
        })};
    }, selector);
    overflow = Math.max(overflow, geometry.overflow);
    for (const box of geometry.boxes) boxes.set(box.key, box);
    if (!geometry.content) throw new Error('content area missing');
    const image = pixels(await boundedScreenshot(page, deadline));
    bands.push(...blankBands(image, geometry.content, geometry.scrollY));
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  return {photos: [...cards.values()], optical: {overflow, boxes: [...boxes.values()], bands}};
}

function internalLinks(hrefs, origin) {
  const urls = new Set();
  for (const href of hrefs) try {
    const url = new URL(href, origin);
    if (url.origin !== origin || !['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
        BLOCK.test(url.href) || url.pathname === '/' && !url.search) continue;
    url.hash = '';
    urls.add(url.href);
  } catch { /* malformed URL is never requested */ }
  return [...urls];
}

async function headLinks(hrefs, origin, cache, request) {
  const results = [];
  for (const url of internalLinks(hrefs, origin)) {
    if (!cache.has(url)) {
      if (cache.size >= 20) continue;
      try {cache.set(url, {path: new URL(url).pathname, status: await request(url)});}
      catch {cache.set(url, {path: new URL(url).pathname, status: 0, error: 'HEAD failed'});}
    }
    results.push(cache.get(url));
  }
  return results;
}

async function firstScreenLinks(page) {
  return page.locator('a[href]').evaluateAll(links => links.filter(link => {
    const r = link.getBoundingClientRect();
    return !link.matches('.getdeal, [rel~=sponsored]') && link.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) &&
      r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
  }).map(link => link.href));
}

async function departmentProbe(page, doc) {
  const before = await page.locator('#content .card[data-key]').evaluateAll(cards => cards.map(card => card.dataset.key));
  // Read the actual published taxonomy rather than maintaining a second list.
  const groups = await page.locator('script').evaluateAll(scripts => {
    for (const script of scripts) {
      const match = script.textContent.match(/\bvar CATGROUPS = (\[[^\n]+\]);/);
      if (match) return JSON.parse(match[1]);
    }
    return [];
  });
  const rows = new Map((doc.deals || []).map(row => [row.key || row.dedupe_key, row]));
  const eligible = [...rows.values()].filter(row => row.status !== 'dead' && !row.locked &&
    !(row.spotlight && row.pct_at_checkout == null));
  const departments = groups.flatMap((group, index) => Array.isArray(group[1]) ?
    [{group, index, expected: eligible.filter(row => group[1].includes(row.category || 'other'))
      .map(row => row.key || row.dedupe_key)}] : [])
    .filter(department => department.expected.length).sort((a, b) => b.expected.length - a.expected.length);
  const department = departments[0];
  if (!department) throw new Error('populated department unavailable');
  try {
    const tab = page.locator(`#cattabs .cattab[data-i="${department.index}"]`);
    await tab.click();
    await page.waitForTimeout(700);
    const cards = page.locator('#content .card[data-key]:not(.locked):not(.gone)');
    const keys = () => cards.evaluateAll(cards => cards.map(card => card.dataset.key));
    let after = await keys();
    // Departments can exceed the initial page. Compare the complete eligible
    // set, without depending on its order or requiring a change from Trending.
    for (let loaded = 0; after.length < department.expected.length && loaded < department.expected.length; loaded++) {
      const more = page.locator('#moremore');
      if (!await more.count()) break;
      const count = await page.locator('#content .card[data-key]').count();
      await more.evaluate(button => button.click());
      await page.waitForTimeout(100);
      after = await keys();
      if (await page.locator('#content .card[data-key]').count() <= count) break;
    }
    return {before, after, expected: department.expected,
      active: await tab.evaluate(tab => tab.classList.contains('active')),
      allowed: department.group[1], categories: after.map(key => rows.get(key)?.category || 'other')};
  } finally {
    await page.locator('#cattabs .cattab[data-i="0"]').click();
    await page.waitForTimeout(100);
  }
}

async function inventorySearchProbe(page, doc) {
  // Pick a searchable word from a live published item, never assume a product
  // type is in stock. Keep digit-bearing model/pack tokens whole, as the site does.
  const tokens = text => (String(text || '').toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[\u2010-\u2015]/g, '-').replace(/&/g, ' and ')
    .match(/[a-z0-9]+(?:[./-][a-z0-9]+)*\+?/g) || [])
    .flatMap(word => /[0-9]/.test(word) ? [word] : word.split(/[./+-]/)).filter(Boolean);
  const words = (doc.deals || []).filter(row => row.status !== 'dead' && !row.locked).flatMap(row => tokens(row.item));
  const query = words.find(word => /^[a-z]{3,}$/.test(word)) || words[0];
  if (!query) throw new Error('inventory search word unavailable');
  try {
    await page.locator('#q').fill(query); await page.waitForTimeout(700);
    const input = await page.locator('#q').inputValue();
    const applied = await page.locator('#searchcount').innerText();
    if (input !== query || applied.match(/\bmatch(?:es)? "([^"]+)"/)?.[1] !== query)
      throw new Error('inventory search query was not applied');
    const titles = await page.locator('#content .card[data-key] .title').allTextContents();
    return {query, matches: titles.filter(title => tokens(title).includes(query)).length};
  } finally {await page.locator('#q').fill(''); await page.waitForTimeout(700);}
}

async function allUsedProbe(page, doc) {
  try {
    await page.locator('#cattabs .cattab[data-i="1"]').click(); await page.waitForTimeout(700);
    const keys = await page.locator('#content .card[data-key]').evaluateAll(cards => cards.map(card => card.dataset.key));
    const rows = new Map((doc.deals || []).map(row => [row.key || row.dedupe_key, row]));
    return keys.filter(key => (rows.get(key)?.conditions || []).some(condition => ['used', 'open_box'].includes(condition)));
  } finally {
    await page.locator('#cattabs .cattab[data-i="0"]').click(); await page.waitForTimeout(100);
  }
}

async function dealPageProbe(page, status, card, rules) {
  const link = page.locator('[data-deal-detail] > a.getdeal');
  const visible = await link.count() === 1 && await link.isVisible();
  const href = visible ? await link.getAttribute('href') : '';
  const problem = visible ? dealLinkProblem({...card, ended: false, href}, rules) ||
    (new URL(href).href !== new URL(card.href).href ? 'main Get deal destination differs from the home card' : '') :
    'main Get deal button missing/hidden';
  return {status, price: /\$[\d,.]+/.test(await page.locator('[data-deal-detail] > .detail-price').innerText().catch(() => '')),
    getDeal: visible && !problem, ...(problem ? {linkProblem: problem} : {})};
}

async function visibleText(content) {
  // Only findings are persisted, never a page's full text.
  return textFindings(await content.evaluateAll(elements => {
    const parts = [];
    for (const el of elements) {
      if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
      const walker = el.ownerDocument.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let node; (node = walker.nextNode());) {
        const parent = node.parentElement;
        if (!parent || parent.closest('script, style, template, noscript, [hidden]') ||
            !parent.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) continue;
        parts.push(node.textContent);
      }
    }
    return parts.join(' ');
  }));
}

async function collectText(content, findings, page = '') {
  const text = await visibleText(content);
  for (const field of ['invalid', 'history'])
    findings[field].push(...text[field].map(phrase => page ? page + ': ' + phrase : phrase));
}

module.exports = {photoState, scanScreens, internalLinks, headLinks, firstScreenLinks,
  departmentProbe, inventorySearchProbe, allUsedProbe, visibleText, collectText, dealPageProbe, boundedScreenshot};
