// Mobile audit of the live site (read-only): sideways overflow, tap targets
// under 40px, inputs under 16px (iOS zooms on focus), text under 12px, header
// height, deals visible before scrolling, console errors. Writes screenshots
// and prints JSON. Our own site only; never pointed at a partner's.
// Usage: node mobile_audit.cjs <base_url> <shot_prefix> [chrome_path]
const { chromium, devices } = require('playwright');
(async () => {
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
    const r = await page.evaluate(() => {
      const vw = document.documentElement.clientWidth;
      const out = { vw, scrollW: document.documentElement.scrollWidth, overflow: [], smallTaps: [], smallInputs: [], tinyText: 0 };
      for (const el of document.querySelectorAll('body *')) {
        const cs = getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden') continue;
        const b = el.getBoundingClientRect();
        if (b.width === 0 || b.height === 0) continue;
        // ignore content inside horizontal scrollers and the ticker
        let p = el.parentElement, scroller = false;
        while (p) { const ps = getComputedStyle(p); if (/(auto|scroll|hidden)/.test(ps.overflowX)) { scroller = true; break; } p = p.parentElement; }
        if (!scroller && (b.right > vw + 1 || b.left < -1)) out.overflow.push(`${el.tagName.toLowerCase()}.${(el.className||'').toString().slice(0,30)} [${Math.round(b.left)},${Math.round(b.right)}]`);
        if (el.matches('a,button,select,input,[role=button],[role=tab]') && b.top < 3000) {
          if (b.height < 40 || b.width < 40) out.smallTaps.push(`${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}.${(el.className||'').toString().slice(0,24)} "${(el.innerText||el.value||el.getAttribute('aria-label')||'').trim().slice(0,22)}" ${Math.round(b.width)}x${Math.round(b.height)}`);
        }
        if (el.matches('input,select,textarea') && parseFloat(cs.fontSize) < 16) out.smallInputs.push(`${el.tagName.toLowerCase()}#${el.id} ${cs.fontSize}`);
        if (el.childElementCount === 0 && el.innerText && el.innerText.trim() && parseFloat(cs.fontSize) < 12) out.tinyText++;
      }
      const grid = document.querySelector('.grid .card, .grid article');
      out.firstCardTop = grid ? Math.round(grid.getBoundingClientRect().top + scrollY) : null;
      const hdr = document.querySelector('header');
      out.headerH = hdr ? Math.round(hdr.getBoundingClientRect().height) : null;
      out.cardsAboveFold = [...document.querySelectorAll('.grid article')].filter(a => a.getBoundingClientRect().top < innerHeight).length;
      return out;
    });
    r.errors = errors;
    r.smallTaps = [...new Set(r.smallTaps)].slice(0, 25);
    r.overflow = [...new Set(r.overflow)].slice(0, 15);
    report[name] = r;
    await page.screenshot({ path: `${prefix}-${name}.png` });
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(report, null, 1));
})().catch(e => { console.error(e); process.exit(1); });
