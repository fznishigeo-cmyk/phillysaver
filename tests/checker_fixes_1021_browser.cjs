// Real Chromium on a local build with the shipped switches: typed words reach "See similar deals",
// and the lead price age reads in minutes.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');

const BUILD = fs.readFileSync(path.join(__dirname, 'tool_pages_browser.cjs'), 'utf8')
  .match(/spawnSync\('\.venv\/bin\/python',\['-c',`([\s\S]*?)`,scratch\]/)[1]
  .replace(/^cfg\.amazon_price_cap=False.*$/m, '')
  .replace(/^cfg\.checker_similar_heading=False.*$/m, '');

(async () => {
  const scratch = fs.mkdtempSync(path.join(process.argv[2], 'checker-'));
  const built = spawnSync('.venv/bin/python', ['-c', BUILD, scratch], {encoding:'utf8'});
  assert.equal(built.status, 0, built.stderr);
  const root = path.join(scratch, 'site');
  let browser;
  try {
    browser = await chromium.launch({headless:true, executablePath:'/opt/pw-browsers/chromium'});
  } catch (e) {
    console.log('Chromium cannot launch: ' + e.message.split('\n')[0]);
    process.exitCode = 77; return;
  }
  try {
    const context = await browser.newContext({viewport:{width:1280, height:900}});
    const checked = new Date(Date.now() - 2638 * 1000).toISOString();
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'https://wowpriced.test') return route.fulfill({status:204, body:''});
      if (url.pathname === '/api/search') return route.fulfill({contentType:'application/json', body:JSON.stringify({items:[
        {asin:'B0D8KZHN1Q', title:'Apple EarPods Headphones with USB-C Plug, Wired Ear Buds with Built-in Remote',
         price_cents:1900, list_price_cents:1900, as_of:checked, monthlySold:10000},
        {asin:'B000000101', title:'Wireless earbuds with charging case', price_cents:1500, list_price_cents:3000, as_of:checked}]})});
      let file = path.join(root, url.pathname);
      if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, 'index.html');
      if (!fs.existsSync(file)) return route.fulfill({status:404, body:''});
      const type = {'.html':'text/html','.js':'application/javascript','.css':'text/css','.json':'application/json'}[path.extname(file)];
      return route.fulfill({contentType:type || 'application/octet-stream', body:fs.readFileSync(file)});
    });
    const page = await context.newPage();
    await page.goto('https://wowpriced.test/amazon-deal-checker/?q=headphones');
    await page.waitForSelector('[data-price-time]', {timeout:60000});
    const similar = await page.locator('a', {hasText:'See similar deals on Amazon'}).first().getAttribute('href');
    const query = new URL(similar).searchParams.get('k');
    assert.equal(query, 'headphones', similar);
    const age = await page.locator('[data-price-time]').first().textContent();
    assert.match(age, /^price checked 4[34] min ago on Amazon$/, age);
    await page.waitForTimeout(1200);
    assert.match(await page.locator('[data-price-time]').first().textContent(), /^price checked 4[34] min ago on Amazon$/);
    console.log(`similar k=${query}; ${age}`);
  } finally {
    await browser.close();
    fs.rmSync(scratch, {recursive:true, force:true});
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
