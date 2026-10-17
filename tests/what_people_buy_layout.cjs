// Local markup only; every browser request is aborted.
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  const {chromium} = require(require.resolve('playwright', {paths: ['/opt/node-tools', __dirname]}));
  let browser;
  try {
    browser = await chromium.launch({executablePath: '/opt/pw-browsers/chromium',
      args: ['--no-sandbox', '--disable-dev-shm-usage']});
  } catch (error) {
    console.log('Offline 390px layout unverified: Chromium cannot launch: ' + error.message);
    process.exitCode = 77;
    return;
  }
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}});
    await page.route('**/*', route => route.abort());
    for (const colorScheme of ['light', 'dark']) {
      await page.emulateMedia({colorScheme});
      await page.setContent(fs.readFileSync(process.argv[2], 'utf8'));
      const cards = await page.locator('.card').evaluateAll(cards => cards.map(card => {
        const media = card.querySelector('.media').getBoundingClientRect();
        const tag = card.querySelector('.condtag').getBoundingClientRect();
        const price = card.querySelector('.pricerow').getBoundingClientRect();
        const style = getComputedStyle(card.querySelector('.condtag'));
        return {left: tag.left - media.left, right: media.right - tag.right,
          tagBottom: tag.bottom, priceTop: price.top, priceHeight: price.height,
          fontSize: parseFloat(style.fontSize)};
      }));
      for (const card of cards) {
        assert(card.left >= 0 && card.right >= 0, 'tag overflows 390px card');
        assert(card.tagBottom <= card.priceTop, 'tag reaches price row');
        assert(card.fontSize >= 12, 'phone text below existing 12px floor');
        assert.equal(card.priceHeight, cards[0].priceHeight, 'price row gained wrapping');
      }
    }
    console.log('390px light/dark labels fit; price-row heights unchanged.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
