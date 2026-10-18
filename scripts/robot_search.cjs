// Synthetic browser-check data only; never published or sent to the search API.
const FIXTURE_TITLE = 'Robot fixture cupping set';
const FIXTURE_IMAGE = 'https://m.media-amazon.com/images/I/robot-search-fixture.png';
function searchResponse(url, products = false) {
  const params = new URL(url).searchParams;
  const items = products && params.get('q') === 'cupping set' && Number(params.get('page') || 0) === 0
    ? [{asin: 'B000000298', title: FIXTURE_TITLE, image: FIXTURE_IMAGE,
        price_cents: 1299, as_of: new Date().toISOString()}] : [];
  return {status: 200, contentType: 'application/json', body: JSON.stringify(items.length ? {items} : {items, reason: 'robot'})};
}
async function routeRobotSearch(target, products = false) {
  await target.route('**/api/search*', route => route.fulfill(searchResponse(route.request().url(), products)));
  if (products) await target.route(FIXTURE_IMAGE, route => route.fulfill({contentType: 'image/svg+xml',
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><rect width="200" height="200" fill="#728b9b"/></svg>'}));
}
module.exports = {routeRobotSearch, searchResponse, FIXTURE_TITLE};
