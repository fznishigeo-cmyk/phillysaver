// Mobile audit of the live site (read-only): sideways overflow, tap targets
// under 44px, inputs under 16px (iOS zooms on focus), text under 12px, header
// height, deals visible before scrolling, console errors. Writes screenshots
// and prints JSON. Our own site only; never pointed at a partner's.
// Usage: node mobile_audit.cjs <base_url> <shot_prefix> [chrome_path]
// This factory also runs in the page; its geometry can be tested without a
// browser or Playwright. CSS geometry is only a candidate: probes verify it.
function hitAreaMath() {
  function length(value, size, fallback = 0) {
    if (typeof value === 'number') return value;
    if (/^-?[\d.]+(?:px)?$/.test(value)) return parseFloat(value);
    if (/^-?[\d.]+%$/.test(value)) return parseFloat(value) * size / 100;
    const calc = /^calc\((-?[\d.]+)%\s*([+-])\s*([\d.]+)px\)$/.exec(value);
    return calc ? Number(calc[1]) * size / 100 + (calc[2] === '+' ? 1 : -1) * Number(calc[3]) : fallback;
  }
  function rect(left, top, width, height) {
    return {left, top, width, height, right: left + width, bottom: top + height};
  }
  function pseudoRect(cb, style) {
    if (!['""', "''"].includes(style.content) || style.position !== 'absolute' ||
        style.pointerEvents === 'none' || style.display === 'none' || style.visibility === 'hidden') return null;
    // The hit areas use translations. Don't claim unsupported transforms.
    const matrix = /^matrix\(([^)]+)\)$/.exec(style.transform || '');
    const transform = matrix ? matrix[1].split(',').map(Number) : [1, 0, 0, 1, 0, 0];
    if ((style.transform && style.transform !== 'none' && !matrix) ||
        transform.length !== 6 || transform.some(n => !Number.isFinite(n)) ||
        transform.slice(0, 4).some((n, i) => n !== [1, 0, 0, 1][i])) return null;
    const left = length(style.left, cb.width, null), right = length(style.right, cb.width, null);
    const top = length(style.top, cb.height, null), bottom = length(style.bottom, cb.height, null);
    // Auto/auto needs the inline static position, which a computed style
    // doesn't expose. All our extended targets set an explicit position.
    if ((left === null && right === null) || (top === null && bottom === null)) return null;
    const width = Math.max(length(style.minWidth, cb.width), Math.min(
      length(style.width, cb.width, cb.width - (left || 0) - (right || 0)),
      length(style.maxWidth, cb.width, Infinity)));
    const height = Math.max(length(style.minHeight, cb.height), Math.min(
      length(style.height, cb.height, cb.height - (top || 0) - (bottom || 0)),
      length(style.maxHeight, cb.height, Infinity)));
    return rect(cb.left + (left === null ? cb.width - right - width : left) + transform[4],
      cb.top + (top === null ? cb.height - bottom - height : top) + transform[5], width, height);
  }
  function union(boxes) {
    const left = Math.min(...boxes.map(b => b.left)), top = Math.min(...boxes.map(b => b.top));
    return rect(left, top, Math.max(...boxes.map(b => b.right)) - left,
      Math.max(...boxes.map(b => b.bottom)) - top);
  }
  function effectiveRect(box, extensions, ownsPoint) {
    let area = union([box, ...extensions.filter(Boolean)]);
    const cx = box.left + box.width / 2, cy = box.top + box.height / 2;
    function span(horizontal, fixed, low, high, center) {
      const probe = v => horizontal ? ownsPoint(v, fixed) : ownsPoint(fixed, v);
      if (!probe(center)) return [center, center];
      function edge(end) {
        const direction = Math.sign(end - center);
        let good = center;
        while (Math.abs(end - good) > .01) {
          const next = good + direction * Math.min(1, Math.abs(end - good));
          // Probe just inside the edge, not the adjacent element at the edge.
          if (probe(next - direction * .01)) { good = next; continue; }
          let bad = next;
          for (let i = 0; i < 10; i++) {
            const mid = (good + bad) / 2;
            if (probe(mid)) good = mid; else bad = mid;
          }
          return good;
        }
        return end;
      }
      return [edge(low), edge(high)];
    }
    let [left, right] = span(true, cy, area.left, area.right, cx);
    let [top, bottom] = span(false, cx, area.top, area.bottom, cy);
    // Also probe the 44px square's edges: a neighbor at a corner must not
    // turn a nominal extension into a passing target. Keep any shortfall.
    if (right - left >= 43.99 && bottom - top >= 43.99) {
      const x = Math.max(left, Math.min(cx - 22, right - 44));
      const y = Math.max(top, Math.min(cy - 22, bottom - 44));
      for (const row of [y + .01, y + 22, y + 44 - .01]) {
        const limits = span(true, row, left, right, cx);
        left = Math.max(left, limits[0]); right = Math.min(right, limits[1]);
      }
      for (const column of [x + .01, x + 22, x + 44 - .01]) {
        const limits = span(false, column, top, bottom, cy);
        top = Math.max(top, limits[0]); bottom = Math.min(bottom, limits[1]);
      }
    }
    return rect(left, top, Math.max(0, right - left), Math.max(0, bottom - top));
  }
  return {length, rect, pseudoRect, union, effectiveRect};
}

function inspectPage(math) {
  const vw = document.documentElement.clientWidth;
  const out = { vw, scrollW: document.documentElement.scrollWidth, overflow: [], smallTaps: [], smallInputs: [], tinyText: 0 };
  const taps = [], startX = scrollX, startY = scrollY;
  const actionable = 'a,button,select,input,[role=button],[role=tab]';
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const b = el.getBoundingClientRect();
    if (b.width === 0 || b.height === 0) continue;
    // ignore content inside horizontal scrollers and the ticker
    let p = el.parentElement, scroller = false;
    while (p) { const ps = getComputedStyle(p); if (/(auto|scroll|hidden)/.test(ps.overflowX)) { scroller = true; break; } p = p.parentElement; }
    if (!scroller && (b.right > vw + 1 || b.left < -1)) out.overflow.push(`${el.tagName.toLowerCase()}.${(el.className||'').toString().slice(0,30)} [${Math.round(b.left)},${Math.round(b.right)}]`);
    if (el.matches(actionable) && b.top < 3000) taps.push(el);
    if (el.matches('input,select,textarea') && parseFloat(cs.fontSize) < 16) out.smallInputs.push(`${el.tagName.toLowerCase()}#${el.id} ${cs.fontSize}`);
    if (el.childElementCount === 0 && el.innerText && el.innerText.trim() && parseFloat(cs.fontSize) < 12) out.tinyText++;
  }
  const grid = document.querySelector('.grid .card, .grid article');
  out.firstCardTop = grid ? Math.round(grid.getBoundingClientRect().top + scrollY) : null;
  const hdr = document.querySelector('header');
  out.headerH = hdr ? Math.round(hdr.getBoundingClientRect().height) : null;
  out.cardsAboveFold = [...document.querySelectorAll('.grid article')].filter(a => a.getBoundingClientRect().top < innerHeight).length;
  const properties = ['content', 'position', 'display', 'visibility', 'pointerEvents', 'transform',
    'left', 'right', 'top', 'bottom', 'width', 'height', 'minWidth', 'maxWidth', 'minHeight', 'maxHeight'];
  for (const el of taps) {
    let box = el.getBoundingClientRect();
    if (box.width >= 44 && box.height >= 44) continue;
    // Probes need viewport coordinates, including cards below the fold.
    // Scroll only the document, leaving category/filter scrollers in place.
    window.scrollTo({left: startX, top: Math.max(0, box.top + scrollY - innerHeight / 2), behavior: 'instant'});
    box = el.getBoundingClientRect();
    let cb = el;
    while (cb.parentElement && getComputedStyle(cb).position === 'static') cb = cb.parentElement;
    const cbStyle = getComputedStyle(cb), cbBox = cb.getBoundingClientRect();
    const bl = parseFloat(cbStyle.borderLeftWidth) || 0, bt = parseFloat(cbStyle.borderTopWidth) || 0;
    const containing = math.rect(cbBox.left + bl, cbBox.top + bt,
      cbBox.width - bl - (parseFloat(cbStyle.borderRightWidth) || 0),
      cbBox.height - bt - (parseFloat(cbStyle.borderBottomWidth) || 0));
    const extensions = ['::before', '::after'].map(pseudo => {
      const cs = getComputedStyle(el, pseudo);
      return math.pseudoRect(containing, Object.fromEntries(properties.map(key => [key, cs[key]])));
    });
    const effective = math.effectiveRect(box, extensions, (x, y) => {
      const hit = document.elementFromPoint(x, y);
      return !!hit && (hit === el || el.contains(hit) ||
        (el.matches('input,select') && hit.closest('label')?.control === el));
    });
    if (effective.height < 43.99 || effective.width < 43.99) out.smallTaps.push(`${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}.${(el.className||'').toString().slice(0,24)} "${(el.innerText||el.value||el.getAttribute('aria-label')||'').trim().slice(0,22)}" ${Math.round(effective.width)}x${Math.round(effective.height)}`);
  }
  window.scrollTo({left: startX, top: startY, behavior: 'instant'});
  return out;
}

async function runAudit() {
  const { chromium, devices } = require('playwright');
  const [base, prefix] = process.argv.slice(2, 4);
  const exe = process.argv[4];
  const browser = await chromium.launch(exe ? { executablePath: exe } : {});
  const report = {};
  for (const [name, dev] of [['iphone14', devices['iPhone 14']], ['pixel7', devices['Pixel 7']], ['small360', {viewport:{width:360,height:740}, deviceScaleFactor:2, isMobile:true, hasTouch:true, userAgent:devices['Pixel 7'].userAgent}], ['iphone14-dark', {...devices['iPhone 14'], colorScheme:'dark'}]]) {
    const ctx = await browser.newContext({ ...dev });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
    await page.goto(base + '/', { waitUntil: 'networkidle' });
    await page.waitForTimeout(1200);
    const r = await page.evaluate(`(${inspectPage.toString()})((${hitAreaMath.toString()})())`);
    r.errors = errors;
    r.smallTaps = [...new Set(r.smallTaps)].slice(0, 25);
    r.overflow = [...new Set(r.overflow)].slice(0, 15);
    report[name] = r;
    await page.screenshot({ path: `${prefix}-${name}.png` });
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(report, null, 1));
}

module.exports = hitAreaMath();
if (require.main === module) runAudit().catch(e => { console.error(e); process.exit(1); });
