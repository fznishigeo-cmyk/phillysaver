// Shared native assertions; callers provide their existing offline routes.
const assert = require('node:assert/strict');
const path = require('node:path');

async function yellow(page) {
  const button = page.locator('a.search-discounts:visible');
  await button.waitFor();
  assert.equal(await button.count(), 1, 'one yellow results exit');
  assert.equal(await button.innerText(), 'See Amazon discounts →');
  assert.equal(await button.getAttribute('href'), 'https://www.amazon.com/');
  assert.equal(await button.getAttribute('target'), '_blank');
  assert.equal(await button.getAttribute('rel'), 'sponsored noopener');
  const state = () => button.evaluate(el => {
    const c = getComputedStyle(el), r = el.getBoundingClientRect();
    const parent = el.parentElement.getBoundingClientRect();
    return {background:c.backgroundColor, image:c.backgroundImage, color:c.color, radius:c.borderRadius,
      weight:c.fontWeight, padding:c.padding, decoration:c.textDecorationLine, animation:c.animationName, transition:c.transitionDuration,
      star:getComputedStyle(el,'::before').content, sheen:getComputedStyle(el,'::after').content,
      x:r.x, y:r.y, width:r.width, height:r.height, parentWidth:parent.width};
  });
  const before = await state();
  assert.equal(before.background, 'rgb(255, 200, 61)');
  assert.equal(before.color, 'rgb(17, 17, 17)');
  assert.equal(before.image, 'none'); assert.equal(before.decoration, 'none');
  assert.equal(before.radius, '999px'); assert.equal(before.weight, '800');
  assert.equal(before.padding, '14px 24px');
  assert(before.height >= 44);
  assert.equal(before.animation, 'none'); assert.equal(before.transition, '0s');
  assert.equal(before.star, 'none'); assert.equal(before.sheen, 'none');
  await button.hover(); assert.deepEqual(await state(), before, 'hover keeps paint and bounds');
  await page.mouse.move(0, 0);
  if (page.viewportSize().width < 900) assert(Math.abs(before.width - before.parentWidth) < 1, 'phone full width');
  else assert(before.width < before.parentWidth, 'desktop auto width');
  return button;
}

async function compareYellowURL(page) {
  await page.evaluate(() => {
    window.__task342Open = window.open; window.__task342Calls = [];
    window.open = (...args) => {window.__task342Calls.push(args); return null;};
  });
  try {
    await (await yellow(page)).click();
    // The phone All Discounts button only shows after typing (a ?q= page load has not typed),
    // so fire its click directly: the same handler builds the URL either way.
    await page.locator(page.viewportSize().width < 900 ? '#search-discounts-mobile' : '#search-discounts').dispatchEvent('click');
    const calls = await page.evaluate(() => window.__task342Calls);
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[0], calls[1], 'results uses exactly All Discounts URL and window options');
    assert.deepEqual(calls[0].slice(1), ['_blank', 'noopener']);
    return calls[0][0];
  } finally {await page.evaluate(() => {window.open = window.__task342Open;});}
}

async function cards(page, shots, name) {
  const card = page.locator('#content .card:not(.gone):has(.getdeal)').first();
  await card.waitFor();
  const href = await card.locator('.getdeal').getAttribute('href');
  assert.equal(await card.locator('.getdeal').count(), 1);
  assert.equal(await card.locator('.getdeal').getAttribute('target'), '_blank');
  assert.equal(await card.locator('.getdeal').getAttribute('rel'), 'sponsored noopener');
  const openAt = async point => {
    const popupPromise = page.context().waitForEvent('page');
    const requestPromise = page.context().waitForEvent('request', req => req.url() === href && req.isNavigationRequest());
    await page.mouse.click(point.x, point.y);
    const popup = await popupPromise;
    assert.equal((await requestPromise).url(), href);
    await popup.close();
  };
  const tracksBefore = await page.evaluate(() => window.discountTracks.filter(e => e[0] === 'deal_click').length);
  await card.scrollIntoViewIfNeeded();
  let media = await card.locator('.media').boundingBox();
  await openAt({x:media.x + media.width / 2, y:media.y + media.height / 2});
  // Find actual padding whose hit target is the existing Get deal anchor.
  const empty = await card.evaluate(el => {
    const r = el.getBoundingClientRect(), body = el.querySelector('.body').getBoundingClientRect();
    for (let y = body.y + 4; y < Math.min(body.bottom, innerHeight) - 4; y += 6) {
      const x = r.x + 4, target = document.elementFromPoint(x, y);
      if (target?.closest('.getdeal')) return {x,y};
    }
    return null;
  });
  assert(empty, 'empty body padding belongs to Get deal'); await openAt(empty);
  const clicks = await page.evaluate(() => window.discountTracks.filter(e => e[0] === 'deal_click'));
  assert.equal(clicks.length, tracksBefore + 2, 'photo and padding preserve Get deal tracking');
  assert.deepEqual(clicks.at(-1), clicks.at(-2), 'same existing tracking payload');
  const heart = card.locator('.save-deal');
  if (await heart.count()) {
    const old = await heart.getAttribute('aria-pressed');
    await heart.click(); assert.notEqual(await heart.getAttribute('aria-pressed'), old, 'heart still toggles');
    await heart.click(); assert.equal(await heart.getAttribute('aria-pressed'), old);
  }
  await page.screenshot({path:path.join(shots, name + '-card.png')});
  const title = card.locator('.title a'), dealPage = await title.getAttribute('href');
  assert(dealPage.startsWith('/deal/'));
  await title.click(); await page.waitForURL(url => url.pathname === dealPage);
  await page.goBack(); await card.waitFor();
}
module.exports = {yellow, compareYellowURL, cards};
