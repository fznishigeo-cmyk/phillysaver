// Offline phone audit. node tests/dark_mode_browser.cjs <off> <on> <shots>
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const ORIGIN = 'https://wowpriced.test';
const FIXED = new Date('2026-10-12T00:00:00Z');

// Chromium screenshots are non-interlaced, 8-bit RGB/RGBA PNGs. Decode with
// Node alone so the audit needs no image library or downloaded dependencies.
function pixels(png) {
  assert.equal(png.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  let width, height, channels;
  const chunks = [];
  for (let at = 8; at < png.length;) {
    const length = png.readUInt32BE(at), type = png.toString('ascii', at + 4, at + 8);
    const data = png.subarray(at + 8, at + 8 + length);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0); height = data.readUInt32BE(4);
      assert.equal(data[8], 8); assert.equal(data[12], 0);
      channels = {2: 3, 6: 4}[data[9]]; assert(channels, 'unsupported PNG color type');
    }
    if (type === 'IDAT') chunks.push(data);
    at += length + 12;
  }
  const raw = zlib.inflateSync(Buffer.concat(chunks)), stride = width * channels;
  const decoded = Buffer.alloc(height * stride);
  const paeth = (a, b, c) => {
    const p = a + b - c, da = Math.abs(p - a), db = Math.abs(p - b), dc = Math.abs(p - c);
    return da <= db && da <= dc ? a : db <= dc ? b : c;
  };
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)]; assert(filter <= 4);
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? decoded[y * stride + x - channels] : 0;
      const b = y ? decoded[(y - 1) * stride + x] : 0;
      const c = y && x >= channels ? decoded[(y - 1) * stride + x - channels] : 0;
      decoded[y * stride + x] = raw[y * (stride + 1) + x + 1] +
        [0, a, b, Math.floor((a + b) / 2), paeth(a, b, c)][filter];
    }
  }
  const data = Buffer.alloc(width * height * 3);
  for (let i = 0; i < width * height; i++) decoded.copy(data, i * 3, i * channels, i * channels + 3);
  return {width, height, data};
}

function equalPixels(a, b, label) {
  assert.equal(a.width, b.width, label); assert.equal(a.height, b.height, label);
  assert(a.data.equals(b.data), label + ': pixels differ');
}

function glare(image) {
  let white = 0;
  for (let i = 0; i < image.data.length; i += 3)
    if (Math.min(...image.data.subarray(i, i + 3)) >= 245) white++;
  return white / (image.width * image.height);
}

function contrast(fg, bg) {
  const luminance = rgb => rgb.map(v => v / 255).reduce((sum, v, i) => sum +
    [0.2126, 0.7152, 0.0722][i] * (v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4), 0);
  const values = [luminance(fg), luminance(bg)].sort((a, b) => b - a);
  return (values[0] + .05) / (values[1] + .05);
}

function medianBackground(image, box) {
  const values = [[], [], []];
  const left = Math.max(0, Math.ceil(box.x)), top = Math.max(0, Math.ceil(box.y));
  const right = Math.min(image.width, Math.floor(box.x + box.width));
  const bottom = Math.min(image.height, Math.floor(box.y + box.height));
  assert(left < right && top < bottom, 'empty contrast box');
  for (let y = top; y < bottom; y++) for (let x = left; x < right; x++) {
    const at = (y * image.width + x) * 3;
    for (let c = 0; c < 3; c++) values[c].push(image.data[at + c]);
  }
  return values.map(channel => channel.sort((a, b) => a - b)[Math.floor(channel.length / 2)]);
}

async function textContrast(page, shot) {
  const selected = await page.evaluate(() => {
    const selectors = ['h1', 'h2', 'h3', '.now', '.save', '.detail-price', '.title a', '.chip', '.badge', '.flag',
      '.condtag', '.gonetag', '.getdeal', '.pwbtn', '.joinchoice', '.ph span', 'footer a',
      'footer p', '.joincard label', '.joincard p', '.joincard a', '.joincard button',
      '.price-history figcaption', '.amazon-inner p', '.amazon-inner a', '#filtertoggle',
      '#filterapply', '#filterclose', '.footeremail button', '#check-form button'];
    const nodes = [...document.querySelectorAll(selectors.join(','))];
    return nodes.flatMap((el, i) => {
      const style = getComputedStyle(el), r = el.getBoundingClientRect();
      // Audit visible text only; screen-reader labels have their own hidden box.
      if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}) ||
          r.width < 2 || r.height < 2 || r.bottom <= 0 || r.top >= innerHeight ||
          r.right <= 0 || r.left >= innerWidth || style.clipPath !== 'none' ||
          el.closest('.sr-only,[hidden]') || !el.textContent.trim()) return [];
      // Nested elements with their own text color are measured separately.
      if (nodes.some(other => other !== el && el.contains(other))) return [];
      // Text under a modal scrim is dimmed with its background; audit the top layer only.
      const top = document.elementFromPoint(Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1),
        Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1));
      if (!top || !(el.contains(top) || top.contains(el))) return [];
      el.setAttribute('data-task254-contrast', String(i));
      let opacity = 1;
      for (let node = el; node; node = node.parentElement) opacity *= Number(getComputedStyle(node).opacity);
      return [{id: i, text: el.textContent.trim().slice(0, 100), color: style.color,
        opacity, size: Number.parseFloat(style.fontSize), weight: Number.parseInt(style.fontWeight) || 400,
        box: {x: r.x, y: r.y, width: r.width, height: r.height}}];
    });
  });
  const hiddenText = await page.addStyleTag({content:
    '[data-task254-contrast],[data-task254-contrast] *{color:transparent!important;-webkit-text-fill-color:transparent!important;text-shadow:none!important}'});
  let background;
  try { background = pixels(await page.screenshot({animations: 'disabled', caret: 'hide'})); }
  finally { await hiddenText.evaluate(el => el.remove()); }
  return selected.map(row => {
    const bg = medianBackground(background, row.box);
    const rgba = row.color.match(/[\d.]+/g).map(Number), alpha = (rgba[3] ?? 1) * row.opacity;
    const fg = rgba.slice(0, 3).map((v, i) => v * alpha + bg[i] * (1 - alpha));
    const minimum = row.size >= 24 || row.size >= 18.66 && row.weight >= 700 ? 3 : 4.5;
    return {...row, shot, background: bg, ratio: contrast(fg, bg), minimum};
  });
}

function firstPage(root, prefix) {
  const folder = path.join(root, prefix);
  const child = fs.readdirSync(folder).sort().find(name => fs.existsSync(path.join(folder, name, 'index.html')));
  assert(child, 'missing ' + prefix + ' fixture');
  return '/' + prefix + '/' + child + '/';
}

async function prepare(page, scene) {
  await page.goto(ORIGIN + scene.url, {waitUntil: 'networkidle'});
  await page.evaluate(() => document.fonts.ready);
  if (scene.url === '/') {
    await page.waitForFunction(() => document.querySelectorAll('#content .grid .card').length >= 24);
    await page.waitForTimeout(250); // let the scheduled Saved repaint settle
  }
  if (scene.state === 'grid') await page.locator('.grid .card').first().scrollIntoViewIfNeeded();
  // Off-screen cards settle their height while scrolling (content-visibility);
  // pin the sign-up box to the top edge so every mode captures the same frame.
  if (scene.state === 'footer') for (let i = 0; i < 3; i++) {
    await page.locator('.footersignup').evaluate(el => el.scrollIntoView({block: 'start'}));
    await page.waitForTimeout(150);
  }
  if (scene.state === 'filters') await page.locator('#filtertoggle').click();
  if (scene.state === 'join') {
    // Same popup presentation as support.js openPrompt(false), with no signup
    // submission or timed prompt needed on a phone.
    await page.evaluate(() => {
      const prompt = document.getElementById('join-prompt');
      prompt.hidden = false; prompt.className = 'joinoverlay joinpopup';
      const dialog = prompt.querySelector('[role="dialog"]');
      dialog.setAttribute('aria-modal', 'true'); dialog.setAttribute('aria-describedby', 'join-description');
      for (const [id, hidden] of Object.entries({'join-success': true, 'join-legal': false,
        'join-google-message': true, 'join-email-choice': true, 'join-back': true,
        'join-step-one': false, 'join-email-fields': false})) document.getElementById(id).hidden = hidden;
      const fields = document.getElementById('join-email-fields');
      fields.querySelector('[data-join-message]').hidden = true;
      fields.querySelector('label').textContent = 'Email address';
      const input = document.getElementById('join-email'); input.placeholder = 'Email address'; input.value = '';
      const button = fields.querySelector('button[type="submit"]'); button.textContent = 'Get the deals'; button.disabled = true;
      const title = document.getElementById('join-title'); title.innerHTML = 'Save up to <span>90% OFF</span>'; title.className = '';
      const description = document.getElementById('join-description'); description.hidden = false;
      description.textContent = "The day's best verified deals in one free email. Unsubscribe anytime.";
      document.getElementById('join-legal').innerHTML = 'We use your email only to send deals. Unsubscribe anytime. See our <a href="/privacy.html">privacy policy</a>.';
      document.getElementById('join-google-or').hidden = document.getElementById('join-google').hidden;
      for (const id of ['paywall', 'subbar', 'subbarclose']) {
        const el = document.getElementById(id); if (el) el.hidden = true;
      }
    });
  }
  // Make lazy photos deterministic for the currently visible viewport.
  await page.evaluate(async () => {
    await Promise.all([...document.images].filter(img => {
      const r = img.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight;
    }).map(img => img.decode().catch(() => {})));
  });
}

async function geometry(page) {
  return page.evaluate(() => ({height: document.documentElement.scrollHeight,
    boxes: [...document.querySelectorAll('.grid .card')].slice(0, 12).flatMap(card =>
      [card, card.querySelector('.media')].filter(Boolean).map(el => {
        const r = el.getBoundingClientRect(); return [r.x, r.y + scrollY, r.width, r.height];
      }))}));
}

(async () => {
  const args = process.argv.slice(2); assert.equal(args.length, 3, 'Provide off, on and shots directories');
  const [off, on, shots] = args.map(p => path.resolve(p)); fs.mkdirSync(shots, {recursive: true});
  let chromium;
  try { ({chromium} = require(require.resolve('playwright', {paths: ['/opt/node-tools', __dirname]}))); }
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    console.log('Chromium unavailable: Playwright missing (exit 77).'); process.exitCode = 77; return;
  }
  let browser;
  try {
    browser = await chromium.launch({headless: true, executablePath: '/opt/pw-browsers/chromium',
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--enable-features=WebContentsForceDark']});
  } catch (error) {
    console.log('Chromium cannot launch (exit 77): ' + error.message.split('\n')[0]);
    process.exitCode = 77; return;
  }
  const scenes = [{name: 'home-top', url: '/'}, {name: 'home-grid', url: '/', state: 'grid'},
    {name: 'home-footer', url: '/', state: 'footer'}, {name: 'home-join', url: '/', state: 'join'},
    {name: 'home-filters', url: '/', state: 'filters'},
    {name: 'best-today', url: '/best-deals-today/'}, {name: 'hub', url: firstPage(on, 'deals')},
    {name: 'store', url: firstPage(on, 'stores')}, {name: 'deal', url: firstPage(on, 'deal')},
    {name: 'product', url: firstPage(on, 'p')}, {name: 'checker', url: '/check/'},
    {name: 'join', url: '/join'}, {name: 'subscribe', url: '/subscribe.html'},
    {name: '404', url: '/404.html'}, {name: 'about', url: '/about.html'}];
  const modes = [{name: 'light', scheme: 'light', forced: false}, {name: 'dark', scheme: 'dark', forced: false},
    {name: 'forced-dark', scheme: 'dark', forced: true}, {name: 'forced-light', scheme: 'light', forced: true}];
  const captures = {}, layouts = {}, errors = [], outside = [], contrasts = [], glareReport = [];
  try {
    for (const mode of modes) for (const [switchName, root] of [['off', off], ['on', on]]) {
      const context = await browser.newContext({viewport: {width: 390, height: 844}, deviceScaleFactor: 1,
        isMobile: true, hasTouch: true, colorScheme: mode.scheme, serviceWorkers: 'block'});
      try {
        await context.route('**/*', async route => {
          const req = route.request(), url = new URL(req.url());
          if (url.origin !== ORIGIN) outside.push(req.url());
          if (req.resourceType() === 'image') {
            // All image bodies are synthetic, including any external image URL.
            const transparent = url.pathname.includes('transparent'), pale = url.pathname.includes('pale');
            return route.fulfill({contentType: 'image/svg+xml', body:
              '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400">' +
              (transparent ? '' : '<rect width="400" height="400" fill="white"/>') +
              '<rect x="95" y="65" width="210" height="270" rx="28" fill="' + (pale ? '#d4d7db' : '#728b9b') + '"/></svg>'});
          }
          if (url.origin !== ORIGIN || req.method() !== 'GET') return route.abort();
          if (url.pathname === '/probe') return route.fulfill({contentType: 'text/html',
            body: '<!doctype html><style>body{margin:0;background:#fff;color:#111}</style><p>Force dark probe</p>'});
          let file = path.resolve(root, '.' + url.pathname);
          if (file !== root && !file.startsWith(root + path.sep)) {outside.push(req.url()); return route.abort();}
          if (url.pathname === '/join') file = path.join(root, 'join.html');
          if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
          assert(fs.existsSync(file), 'missing local fixture ' + url.pathname);
          const type = {'.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css',
            '.json': 'application/json', '.webmanifest': 'application/manifest+json'}[path.extname(file)];
          return route.fulfill({contentType: type || 'application/octet-stream', body: fs.readFileSync(file)});
        });
        const page = await context.newPage();
        page.on('pageerror', error => errors.push(switchName + ': ' + error.message));
        page.on('console', message => {if (message.type() === 'error') errors.push(switchName + ': ' + message.text());});
        await page.clock.setFixedTime(FIXED);
        const cdp = await context.newCDPSession(page);
        await cdp.send('Emulation.setAutoDarkModeOverride', {enabled: mode.forced});
        for (const scene of scenes) {
          await prepare(page, scene);
          const key = [mode.name, switchName, scene.name].join('/');
          const folder = path.join(shots, mode.name, switchName); fs.mkdirSync(folder, {recursive: true});
          const png = await page.screenshot({path: path.join(folder, scene.name + '.png'),
            animations: 'disabled', caret: 'hide', mask: [page.locator('.tk'), page.locator('.drop')]});
          captures[key] = pixels(png); layouts[key] = await geometry(page);
          if (mode.name === 'dark' && switchName === 'on') {
            contrasts.push(...await textContrast(page, scene.name));
            if (scene.name === 'join') assert.equal(await page.locator('footer a').first().evaluate(el =>
              getComputedStyle(el).color), 'rgb(47, 179, 164)', '/join footer link uses the dark accent token');
          }
        }
        if (switchName === 'on' && ['dark', 'forced-dark'].includes(mode.name)) {
          await page.goto(ORIGIN + '/probe');
          captures[mode.name + '/probe'] = pixels(await page.screenshot({path: path.join(shots, mode.name, 'on', 'probe.png'),
            animations: 'disabled', caret: 'hide'}));
        }
      } finally {await context.close();}
    }
    fs.writeFileSync(path.join(shots, 'contrast.json'), JSON.stringify(contrasts, null, 2) + '\n');
    for (const scene of scenes) {
      equalPixels(captures['light/on/' + scene.name], captures['light/off/' + scene.name], scene.name + ' light ON/OFF');
      equalPixels(captures['forced-dark/on/' + scene.name], captures['dark/on/' + scene.name], scene.name + ' forced/dark');
      assert.deepEqual(layouts['dark/on/' + scene.name], layouts['dark/off/' + scene.name], scene.name + ' layout ON/OFF');
      const offShare = glare(captures['dark/off/' + scene.name]), onShare = glare(captures['dark/on/' + scene.name]);
      glareReport.push({page: scene.name, off: offShare, on: onShare});
    }
    fs.writeFileSync(path.join(shots, 'glare.json'), JSON.stringify(glareReport, null, 2) + '\n');
    console.log('Near-white pixel shares OFF/ON: ' + JSON.stringify(glareReport));
    assert(!captures['dark/probe'].data.equals(captures['forced-dark/probe'].data), 'forced dark probe was not darkened');
    assert(glare(captures['forced-dark/probe']) < glare(captures['dark/probe']), 'probe must darken under forced mode');
    assert(contrasts.length > 100, 'contrast audit did not run');
    for (const row of contrasts) assert(row.ratio >= row.minimum,
      row.shot + ': ' + row.text + ' contrast ' + row.ratio.toFixed(2) + ' < ' + row.minimum);
    for (const row of glareReport) assert(row.on <= .01, row.page + ' glare ' + row.on);
    // The dark block may add no console/page error: ON errors must also occur with it OFF
    // (the /check/ page's own CSP blocks traffic.js and the touch icon in both).
    const offErrors = new Set(errors.filter(e => e.startsWith('off: ')).map(e => e.slice(5)));
    const added = [...new Set(errors.filter(e => e.startsWith('on: ')).map(e => e.slice(4)))].filter(e => !offErrors.has(e));
    assert.deepEqual(added, [], 'console/page errors added by the dark block'); assert.deepEqual(outside, [], 'requests outside fake origin');
    console.log('PASS: 15 phone scenes, light equality, forced dark probe/equality, geometry, contrast, glare and no external requests.');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
