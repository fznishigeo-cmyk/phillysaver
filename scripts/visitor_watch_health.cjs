// Browser health probes; outbound deal links are inspected only as strings.
const {pixels, blankBands} = require('./browser_pixels.cjs');
const {textFindings, dealLinkProblem, BLOCK} = require('./visitor_watch_logic.cjs');

// page.screenshot waits for document.fonts.ready, which Chromium holds until
// the page finishes loading; with photos paced at 1 request/second/host a
// category or deal page stays loading for minutes, so every capture there
// timed out at 3 s (live 2026-10-17..21). The site has no web fonts: capture
// the viewport from Chromium directly, with animations stilled as before.
const sessions = new WeakMap();
async function boundedScreenshot(page, deadline) {
  const remaining = deadline - Date.now();
  if (remaining <= 0) throw new Error('screenshot deadline expired');
  const timeout = Math.min(3000, remaining);
  let timer;
  const expired = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`screenshot: Timeout ${timeout}ms exceeded.`)), timeout);
  });
  try {
    return await Promise.race([expired, (async () => {
      if (!sessions.has(page)) sessions.set(page, await page.context().newCDPSession(page));
      await page.evaluate(() => {
        // Same stilling as Playwright's animations: 'disabled' and caret: 'hide'.
        const resume = [];
        for (const animation of document.getAnimations()) {
          if (!animation.effect || animation.playbackRate === 0) continue;
          try {
            if (Number.isFinite(animation.effect.getComputedTiming().endTime)) animation.finish();
            else {animation.cancel(); resume.push(animation);}
          } catch { /* animation already gone */ }
        }
        const style = document.createElement('style');
        style.textContent = '*,*::before,*::after{transition:none!important;caret-color:transparent!important}';
        document.documentElement.append(style);
        window.__visitorWatchRestore = () => {
          style.remove();
          for (const animation of resume) try {animation.play();} catch { /* animation already gone */ }
        };
      });
      try {
        const shot = await sessions.get(page).send('Page.captureScreenshot', {format: 'png'});
        return Buffer.from(shot.data, 'base64');
      } finally {
        await page.evaluate(() => {window.__visitorWatchRestore?.(); delete window.__visitorWatchRestore;}).catch(() => {});
      }
    })()]);
  } finally {clearTimeout(timer);}
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
    // Still downloading is not broken: our own 1 request/second/host pacing
    // queues photos, so the caller gives these a second, longer look.
    return loaded ? 'loaded' : images.some(img => !img.complete) ? 'pending' : images.length ? 'broken' : 'placeholder';
  }, {timeout, selector});
}

// Photos still "pending" get one more look until `until`, at most `look` ms
// each. Cards are found by the key scanScreens gave them (data-key or index).
async function relookPending(page, selector, photos, until, look = Infinity) {
  for (const card of photos) {
    if (card.photo !== 'pending' || until - Date.now() <= 0) continue;
    const at = await page.locator(selector).evaluateAll((elements, key) =>
      elements.findIndex((el, index) => (el.dataset.key || String(index)) === key), card.key);
    if (at < 0) continue;
    const element = page.locator(selector).nth(at);
    await element.scrollIntoViewIfNeeded({timeout: Math.min(3000, until - Date.now())}).catch(() => {});
    card.photo = await photoState(element, Math.max(1, Math.min(look, until - Date.now())));
  }
}

// Placeholder cards for deals the site still shows without a photo by design
// (newPhotoless), matched by their deal-page link or exact title.
async function markNewPhotoless(page, selector, photos, allowed) {
  const keys = photos.filter(card => card.photo === 'placeholder').map(card => card.key);
  if (!keys.length || !allowed.paths.size && !allowed.titles.size) return;
  const found = await page.locator(selector).evaluateAll((elements, keys) => elements.flatMap((el, index) => {
    const key = el.dataset.key || String(index);
    if (!keys.includes(key)) return [];
    let path = '';
    try {path = new URL(el.querySelector('.title a')?.getAttribute('href') || '', location.href).pathname;}
    catch { /* no usable link */ }
    return [{key, path, title: el.querySelector('.title')?.textContent.trim() || ''}];
  }), keys);
  for (const card of photos) {
    const hit = found.find(row => row.key === card.key);
    if (hit && (allowed.paths.has(hit.path) || allowed.titles.has(hit.title))) card.newPhotoless = true;
  }
}

// Screenshots keep `deadline`; looks at paced photos stop at `photoDeadline`.
async function scanScreens(page, selector, deadline, photoDeadline = deadline) {
  const cards = new Map(), where = new Map(), boxes = new Map(), bands = [];
  let overflow = 0;
  for (let screen = 0; screen < 3; screen++) {
    await page.evaluate(screen => window.scrollTo(0, screen * innerHeight), screen);
    await page.waitForTimeout(100);
    const visible = await page.locator(selector).evaluateAll(elements => elements.flatMap((el, index) => {
      const box = el.getBoundingClientRect();
      return el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) && box.width > 1 && box.height > 1 &&
        box.bottom > 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth ?
        [{index, key: el.dataset.key || String(index), keyed: Boolean(el.dataset.key)}] : [];
    }));
    for (const card of visible) {
      where.set(card.key, card);
      // A partially visible image can be outside this viewport. Keep a loaded
      // observation if any of the three screens actually displays the photo;
      // one still downloading gets its single longer look after the scan.
      if (['loaded', 'pending'].includes(cards.get(card.key)?.photo)) continue;
      // A short first look: pending photos share one bounded wait after the scan
      // (live 2026-10-21: 1.5 s per paced card ran desktop past its 120 s).
      const photo = await photoState(page.locator(selector).nth(card.index), Math.max(1, Math.min(250, photoDeadline - Date.now())));
      cards.set(card.key, {key: card.key, photo});
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
  // Paced photos keep arriving while we wait on any one of them: give those
  // still downloading one more look, 8 s in all. Any still queued behind our
  // own 1 request/second pacing stay "pending" (reported, not a site fault);
  // a photo that failed to load is "broken".
  await relookPending(page, selector, cards.values(), Math.min(photoDeadline, Date.now() + 8000));
  // Name the photo host of each broken card, so a flag says where to look.
  for (const card of cards.values()) {
    if (card.photo !== 'broken') continue;
    const {index, keyed} = where.get(card.key);
    const at = keyed ? await page.locator(selector).evaluateAll((elements, key) =>
      elements.findIndex(el => el.dataset.key === key), card.key) : index;
    if (at >= 0) card.host = await page.locator(selector).nth(at).evaluate(el => {
      const img = el.querySelector('img');
      try {return new URL(img?.currentSrc || img?.src).hostname;} catch {return '';}
    }).catch(() => '') || undefined;
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

// Read the actual published taxonomy rather than maintaining a second list.
async function catGroups(page) {
  return page.locator('script').evaluateAll(scripts => {
    for (const script of scripts) {
      const match = script.textContent.match(/\bvar CATGROUPS = (\[[^\n]+\]);/);
      if (match) return JSON.parse(match[1]);
    }
    return [];
  });
}

async function departmentProbe(page, doc, rules = {}) {
  const before = await page.locator('#content .card[data-key]').evaluateAll(cards => cards.map(card => card.dataset.key));
  const groups = await catGroups(page);
  const rows = new Map((doc.deals || []).map(row => [row.key || row.dedupe_key, row]));
  // publish.front_page.used_tab_only: used/open-box rows show only in the Used tab.
  const used = row => (row.conditions || []).some(condition => ['used', 'open_box'].includes(condition));
  const eligible = [...rows.values()].filter(row => row.status !== 'dead' && !row.locked &&
    !(row.spotlight && row.pct_at_checkout == null) && !(rules.usedTabOnly && used(row)));
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
      // A slow runner can take longer than one frame to render the next page.
      for (let waited = 0; waited < 3000 && await page.locator('#content .card[data-key]').count() <= count; waited += 100)
        await page.waitForTimeout(100);
      after = await keys();
      if (await page.locator('#content .card[data-key]').count() <= count) break;
    }
    // Which rows differ, by category/condition only, so a flag says what to fix.
    const shown = new Set(after), wanted = new Set(department.expected);
    const describe = key => {const row = rows.get(key) || {};
      return [row.category || 'other', ...(row.conditions || []), row.status || ''].filter(Boolean).join('/');};
    return {before, after, expected: department.expected,
      active: await tab.evaluate(tab => tab.classList.contains('active')),
      allowed: department.group[1], categories: after.map(key => rows.get(key)?.category || 'other'),
      missing: department.expected.filter(key => !shown.has(key)).slice(0, 5).map(describe),
      extra: after.filter(key => !wanted.has(key)).slice(0, 5).map(describe)};
  } finally {
    await page.locator('#cattabs .cattab[data-i="0"]').click();
    await page.waitForTimeout(100);
  }
}

// The site's own related-deal stop words (search-analyze.js); "and" is also how
// the site reads "&", which every "Home & Kitchen"-style department label adds.
const STOP_WORDS = new Set(['the', 'and', 'for', 'with', 'from', 'pack', 'new', 'set']);

async function inventorySearchProbe(page, doc) {
  // Never assume a product type is in stock. Keep digit-bearing model/pack
  // tokens whole, as the site's normalizeSearch does.
  const tokens = text => (String(text || '').toLowerCase().normalize('NFKD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[\u2010-\u2015]/g, '-').replace(/&/g, ' and ')
    .match(/[a-z0-9]+(?:[./-][a-z0-9]+)*\+?/g) || [])
    .flatMap(word => /[0-9]/.test(word) ? [word] : word.split(/[./+-]/)).filter(Boolean);
  const live = (doc.deals || []).filter(row => row.status !== 'dead' && !row.locked);
  if (!live.some(row => tokens(row.item).length)) throw new Error('inventory search word unavailable');
  // Search like a visitor: a word read on a card of the wall. The site matches a
  // typed word as the start of any word of a deal's title, brand, store, category,
  // department label or condition, and shows the first 60. A broad word ("the" ->
  // thermal, "car" -> Personal Care, "shoe" -> Clothing, Shoes & Jewelry) fills
  // those 60 with deals whose titles lack it (live 2026-10-23 #246/#247: the first
  // deals.json item's first word), so search the seen word the fewest deals match.
  const groups = await catGroups(page);
  const searchable = live.map(row => tokens([row.item, row.brand, row.store, row.category,
    ...groups.filter(group => Array.isArray(group[1]) && group[1].includes(row.category || 'other')).map(group => group[0]),
    ...(row.conditions || [])].join(' ')));
  const seen = await page.locator('#content .card[data-key]:not(.locked):not(.gone) .title').evaluateAll(titles =>
    titles.filter(title => title.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})).map(title => title.textContent));
  const query = [...new Set(seen.flatMap(tokens))].map(word => ({word,
    plain: /^[a-z]{3,}$/.test(word) && !STOP_WORDS.has(word),
    reach: searchable.filter(words => words.some(part => part.startsWith(word))).length}))
    .filter(word => word.reach).sort((a, b) => b.plain - a.plain || a.reach - b.reach)[0]?.word;
  if (!query) throw new Error('inventory search word unavailable');
  // Wait for the site's count, not a fixed time: it names the query once the
  // 150 ms debounce has rendered the results (cards and count render together).
  const count = () => page.locator('#searchcount').innerText();
  const settled = async done => {
    for (let waited = 0; waited < 5000; waited += 100) {
      if (await done()) return true;
      await page.waitForTimeout(100);
    }
    return false;
  };
  try {
    await page.locator('#q').fill(query);
    let applied = '';
    if (!await settled(async () => await page.locator('#q').inputValue() === query &&
        (applied = await count()).match(/\bmatch(?:es)? "([^"]+)"/)?.[1] === query))
      throw new Error(`inventory search query was not applied ("${query}")`);
    // More live cards than the count names is not this search's result (a wall
    // left from before it), so a word seen there cannot pass by itself.
    const total = /^No deals/.test(applied) ? 0 : Number(applied.replace(/,/g, '').match(/^\d+/)?.[0]);
    const titles = await page.locator('#content .card[data-key]:not(.gone) .title').allTextContents();
    return {query, matches: titles.length <= total ? titles.filter(title => tokens(title).includes(query)).length : 0};
  } finally {
    await page.locator('#q').fill('');
    await settled(async () => !await count());
  }
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

module.exports = {photoState, relookPending, markNewPhotoless, scanScreens, internalLinks, headLinks, firstScreenLinks,
  departmentProbe, inventorySearchProbe, allUsedProbe, visibleText, collectText, dealPageProbe, boundedScreenshot};
