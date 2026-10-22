const assert = require("node:assert/strict");
const {amazonTag, checkAmazonTag, checkToolAmazonUrl, checkToolProductUrl, checkSovrnLinks} = require("../scripts/site_check_logic.cjs");
const tag = "wowpriced-20";
const doc = (...urls) => ({deals: urls.map(url => ({url})),
  meta: {disclosure: "As an Amazon Associate, wowpriced earns from qualifying purchases."}});
assert.deepEqual(checkAmazonTag(doc("https://amazon.com/dp/B012345678"), ""),
  {ok: true, note: "n/a (no Amazon tag yet)"});
assert.equal(checkAmazonTag(doc(...["amazon.com", "www.amazon.com", "smile.amazon.com"]
  .map(host => `https://${host}/dp/B012345678?ref=other&tag=${tag}`)), tag).ok, true);
for (const query of ["", "?tag=wrong", `?tag=${tag}&tag=${tag}`, `?tag=wrong&tag=${tag}`]) {
  const result = checkAmazonTag(doc(`https://amazon.com/dp/B012345678${query}`), tag);
  assert.equal(result.ok, false); assert.equal(result.badLinks, 1);
  assert.ok(result.reason.includes("1 bad Amazon links"));
}
assert.equal(checkAmazonTag(doc("https://target.com/x", "http://amazon.com/x",
  "https://amazon.com.evil/x", "https://amazon.co.uk/x", "garbage"), tag).ok, true);
let result = checkAmazonTag(doc("https://amazon.com/dp/B012345678?private=secret&tag=wrong",
  "https://smile.amazon.com/dp/B987654321"), tag);
assert.equal(result.badLinks, 2);
assert.equal(result.example, "/dp/B012345678?tag=wrong");
assert.ok(!result.reason.includes("private") && !result.reason.includes("secret"));
result = checkAmazonTag({deals: [], meta: {disclosure: "No affiliate sentence"}}, tag);
assert.equal(result.ok, false); assert.equal(result.badLinks, 0);
assert.ok(result.reason.includes("missing Amazon disclosure"));
for (const value of ['""', "''", "", '"wowpriced-20"', "'wowpriced-20'", "wowpriced-20"]) {
  assert.equal(amazonTag(`other:\n  amazon_tag: wrong\naffiliate:\n  # comment\n  amazon_tag: ${value} # comment\nseo:\n  enabled: true\n`),
    value.includes(tag) ? tag : "");
}
assert.throws(() => amazonTag("other:\n  amazon_tag: wrong\n"), /missing/);

// Tool exits are inspected only: neither the check nor these tests request Amazon.
{
  const finder = `https://www.amazon.com/s?k=cupping%20set&rh=p_n_deal_type%3A210906366011&tag=${tag}`;
  const checker = `https://amazon.com/s?k=Robot+fixture+cupping+set&rh=p_n_deal_type%3A23566065011&tag=${tag}`;
  assert.deepEqual(checkToolAmazonUrl(finder, tag, true), {ok: true});
  assert.deepEqual(checkToolAmazonUrl(checker, tag), {ok: true});
  assert.equal(checkToolAmazonUrl(finder.replace("p_n_deal_type", "other%3Avalue%2Cp_n_deal_type"), tag, true).ok, true);
  for (const href of [finder.replace("www.amazon.com", "amazon.com.evil"),
    finder + `&tag=${tag}`, finder.replace(tag, "wrong-20"), finder.replace("https:", "http:"),
    finder.replace("/s?", "/dp/item?"), finder.replace("k=cupping%20set", "k="),
    finder.replace("k=cupping%20set&", ""), finder.replace("k=cupping%20set", "k=%20"),
    finder.replace(`&tag=${tag}`, ""), "garbage", "/s?k=cupping"]) {
    assert.equal(checkToolAmazonUrl(href, tag, true).ok, false, href);
  }
  assert.equal(checkToolAmazonUrl(finder, "", true).ok, false);
  assert.equal(checkToolAmazonUrl(checker, tag, true).ok, false);
  assert.equal(checkToolAmazonUrl(finder.replace(/&rh=[^&]+/, ""), tag, true).ok, false);
  assert.equal(checkToolAmazonUrl(finder.replace("210906366011", "2109063660110"), tag, true).ok, false);
  const product = `https://www.amazon.com/dp/B0CUPPING1?tag=${tag}`;
  assert.deepEqual(checkToolProductUrl(product, tag), {ok: true});
  for (const href of [product.replace("https:", "http:"), product.replace("www.amazon.com", "amazon.com.evil"),
    product.replace("/dp/B0CUPPING1", "/s"), product.replace("B0CUPPING1", "B0CUP"), product + `&tag=${tag}`,
    product.replace(tag, "wrong-20"), product.replace(`?tag=${tag}`, ""), "garbage"]) {
    assert.equal(checkToolProductUrl(href, tag).ok, false, href);
  }
  assert.equal(checkToolAmazonUrl(product, tag).ok, false);
}

// First-screens coverage reads the same public photo field as the hourly walk.
{
  const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
  const source = fs.readFileSync(path.join(__dirname, "../scripts/live_first_screens.js"), "utf8");
  const expression = source.match(/noPhoto: count\(([^\n]+?)\), openBox:/)[1];
  const rows = [{image_url: "https://wowpriced.test/photo.jpg"}, {image_url: ""}, {}, {image: "legacy"}];
  assert.equal(rows.filter(vm.runInNewContext(expression)).length, 3);
}

// Manual Sovrn links decode once, keep product parameters, and never wrap Amazon.
{
  const key = "0123456789abcdef0123456789abcdef";
  const wrap = (url, query = `key=${key}`) => `https://redirect.viglink.com?${query}&u=${encodeURIComponent(url)}&cuid=0123456789abcdef`;
  const result = checkSovrnLinks(doc(wrap("https://target.com/product?size=XL&color=red%26blue"),
    "https://sovrn.co/onboarding", "https://target.com/product"));
  assert.deepEqual(result, {ok: true, links: 2, badLinks: 0, reason: "0 malformed Sovrn links"});
  for (const url of [
    wrap("https://target.com/product", ""), wrap("https://target.com/product", "key="),
    wrap("https://target.com/product", "key=invalid"),
    wrap("https://target.com/product", `key=${key}&key=${key}`),
    `https://redirect.viglink.com?key=${key}`, wrap("http://target.com/product"),
    wrap("garbage"), wrap("/relative"), wrap("https%3A%2F%2Ftarget.com%2Fproduct"),
    wrap("https://target.com/product").replace("https://redirect", "http://redirect"),
    ...["amazon.com", "www.amazon.com", "amazon.co.uk", "www.amazon.com.au", "amzn.to", "a.co", "link.amazon"]
      .map(host => wrap(`https://${host}/product`)),
  ]) {
    const bad = checkSovrnLinks(doc(url));
    assert.equal(bad.ok, false, url);
    assert.equal(bad.badLinks, 1);
    assert.ok(!bad.reason.includes(key) && !bad.reason.includes("product"));
  }
  assert.equal(checkSovrnLinks(doc(wrap("https://amazon.com.evil/product"))).ok, true);
  assert.equal(checkSovrnLinks(doc()).links, 0);
}

// Network links (Sovrn onboarding) are reported, never a failure.
{
  const {overrideLinks, overrideNote} = require("../scripts/site_check_logic.cjs");
  const cfg = 'affiliate:\n  amazon_tag: "x-20"\n  link_overrides:\n    "walmart.com/1": "https://sovrn.co/abc"\n\nseo:\n';
  assert.deepEqual(overrideLinks(cfg), ["https://sovrn.co/abc"]);
  assert.equal(overrideNote({deals: [{status: "alive", url: "https://sovrn.co/abc"}]}, ["https://sovrn.co/abc"]), "sovrn.co/abc live");
  assert.equal(overrideNote({deals: [{status: "dead", url: "https://sovrn.co/abc"}]}, ["https://sovrn.co/abc"]), "sovrn.co/abc not on a live deal");
  assert.equal(overrideNote({deals: []}, []), "");
}

// Photo floors from config.yaml turn the hourly check red (operator, 2026-10-15).
{
  const {photoRules, checkPhotos} = require("../scripts/site_check_logic.cjs");
  const fs = require("node:fs"), path = require("node:path");
  const shipped = photoRules(fs.readFileSync(path.join(__dirname, "../config.yaml"), "utf8"));
  assert.deepEqual(shipped, {minPhotoPct: 98, maxBroken: 10, firstScreen: 24});
  const rules = photoRules("other:\n  max_broken: 99\nsite_check:\n  # note\n  min_photo_pct: 92.5  # c\n" +
    "  max_broken: 3\n  first_screen_photos: 20\nseo:\n  min_photo_pct: 1\n");
  assert.deepEqual(rules, {minPhotoPct: 92.5, maxBroken: 3, firstScreen: 20});
  assert.throws(() => photoRules("site_check:\n  min_photo_pct: 90\n"), /site_check\.max_broken missing/);
  const ok = {live: 1000, showing: 925, broken: 3, firstScreen: 20};
  assert.deepEqual(checkPhotos(ok, rules), {ok: true, reason: ""});
  assert.deepEqual(checkPhotos({...ok, live: 0, showing: 0}, rules), {ok: true, reason: ""});
  for (const [change, reason] of [[{showing: 924}, "photos showing 92.40% < 92.5%"],
                                  [{broken: 4}, "4 broken photos > 3"],
                                  [{firstScreen: 19}, "19 of the top 24 cards show a photo < 20"]]) {
    assert.deepEqual(checkPhotos({...ok, ...change}, rules), {ok: false, reason});
  }
  assert.equal(checkPhotos({live: 10, showing: 0, broken: 9, firstScreen: 0}, rules).reason.split("; ").length, 3);
  assert.deepEqual(checkPhotos({live: 10000, showing: 9799, broken: 0, firstScreen: 24}, shipped),
    {ok: false, reason: "photos showing 97.99% < 98%"});
  assert.deepEqual(checkPhotos({live: 10000, showing: 9800, broken: 0, firstScreen: 24}, shipped),
    {ok: true, reason: ""});
  const script = fs.readFileSync(path.join(__dirname, "../scripts/site_check.js"), "utf8");
  assert.ok(script.includes("if (!photos.ok || !front.ok || [desktop, mobile].some("));
  assert.ok(script.includes('say(`photo floors: ${photos.ok ? "ok" : "FAIL " + photos.reason}`);'));
}

// The not-earning list is a report from public fields only.
{
  const fs = require("node:fs"), path = require("node:path");
  const script = fs.readFileSync(path.join(__dirname, "../scripts/site_check.js"), "utf8");
  const block = script.slice(script.indexOf("const unpaid = "), script.indexOf("// Category audit"));
  assert.ok(block.includes("alive.filter((d) => !d.earns)") && block.includes("top(unpaid, (d) => d.store, 25)"));
  assert.ok(!block.includes("exitCode") && !block.includes("check("));
}

// Front page: niche categories and car parts are flagged; only the first 24 cards turn it red.
{
  const fs = require("node:fs"), path = require("node:path");
  const {frontRules, frontPageFlags} = require("../scripts/site_check_logic.cjs");
  const rules = frontRules(fs.readFileSync(path.join(__dirname, "../config.yaml"), "utf8"));
  assert.ok(rules.niche.includes("auto"));
  const card = (category, item) => ({category, item, store: "Amazon"});
  const clean = Array.from({length: 48}, (_, i) => card("home", `Desk Lamp ${i}`));
  assert.deepEqual(frontPageFlags(clean, rules), {ok: true, flags: []});
  const late = clean.slice(); late[30] = card("auto", "Wiper fluid");
  let result = frontPageFlags(late, rules);
  assert.equal(result.ok, true); assert.equal(result.flags[0].pos, 31);
  const early = clean.slice();
  early[5] = card("home", "PHILTOP Front Left CV Axle for Hyundai Sonata 2011-2014");
  result = frontPageFlags(early, rules);
  assert.equal(result.ok, false); assert.equal(result.flags[0].why, "car part");
  early[5] = card("auto", "KAX Front Brake Kit, Ceramic Brake Pads and Rotors");
  assert.equal(frontPageFlags(early, rules).flags[0].why, "niche category auto, car part");
  for (const ok of ["Shark FlexStyle Air Styler", "Car Seat Cover for Babies", "Hot Wheels 20-Car Pack",
                    "Skechers Girl's Glimmer Kicks - School Struts Sneaker"])
    assert.equal(frontPageFlags([card("home", ok)], rules).ok, true, ok);
}

// Identify the actual DOM cards, including gone/spotlight rows and deploy races.
{
  const {identifyFrontCards, frontPageFlags, frontPageFlagLine, frontPageSnapshot} =
    require("../scripts/site_check_logic.cjs");
  const rules = {niche: ["auto", "other"]};
  const visible = {key: 'lamp&"', item: 'Lamp & shade "large"', store: "Target", category: ""};
  const deal = {...visible, category: "home", status: "alive"};
  const check = (cards, docs) => frontPageFlags(identifyFrontCards(cards, docs), rules);
  for (const status of ["alive", "dead"]) {
    assert.deepEqual(check([visible], [{deals: [{...deal, status}]}]), {ok: true, flags: []});
  }
  assert.equal(check([visible], [{deals: [], spotlight: [deal]}]).ok, true);
  assert.equal(check([visible], [null, {deals: [{...deal, key: "", dedupe_key: visible.key}]}]).ok, true);
  assert.equal(check([{...visible, category: "home"}], []).ok, true); // DOM alone is sufficient.
  assert.equal(check([{...visible, category: "auto"}], [{deals: [deal]}]).ok, false); // DOM wins.
  for (const docs of [[], [null], [{deals: [{...deal, item: "Old publication"}]}],
      [{deals: [{...deal, store: "Other store"}]}], [{deals: [{...deal, locked: true}]}],
      [{deals: [deal]}, {deals: [{...deal, category: "auto"}]}]]) {
    const result = check([visible], docs);
    assert.equal(result.ok, false);
    assert.deepEqual(result.flags, [{pos: 1, unidentified: true, key: visible.key}]);
    assert.equal(frontPageFlagLine(result.flags[0]), 'unidentified card #1 key=lamp&"');
    assert(!frontPageFlagLine(result.flags[0]).includes("niche category other"));
  }
  const good = {...deal}, unknown = {...visible, key: "missing"};
  for (const pos of [19, 24, 25, 30, 48]) {
    const cards = Array.from({length: 48}, () => good);
    cards[pos - 1] = unknown;
    const result = check(cards, [{deals: [deal]}]);
    assert.equal(result.ok, pos > 24);
    assert.equal(frontPageFlagLine(result.flags[0]), `unidentified card #${pos} key=missing`);
  }
  for (const d of [{...deal, category: "other"}, {...deal, category: undefined},
      {...deal, category: "auto"}, {...deal, item: "Front Brake Pads and Rotors"}]) {
    const result = check([{...d, category: ""}], [{deals: [d]}]);
    assert.equal(result.ok, false);
    assert.equal(result.flags[0].unidentified, undefined);
    assert(frontPageFlagLine(result.flags[0]).includes(d.item));
  }
  assert.equal(frontPageFlags([{}], rules).flags[0].unidentified, true);
  // Snapshot the first 48 positions, with browser-decoded text and attributes.
  global.document = {
    getElementById: id => id === "countdown" ? {textContent:"10:00"} :
      {innerText:"1,000+ VERIFIED DEALS", textContent:"verified deals · 1,000+ live deals1,000+ verified deals"},
    querySelectorAll: selector => {
      assert.equal(selector, "#content .card[data-key]");
      return Array.from({length: 60}, (_, i) => ({
        matches: () => false, getAttribute: () => `key${i}`,
        querySelector: s => s === ".title" ? {textContent: ` ${visible.item} `} :
          s === ".storechip" ? {textContent: " Target "} :
          s === ".getdeal[href]" ? {getAttribute: () => "https://target.com/product"} :
          {getAttribute: () => "home"},
      }));
    },
  };
  try {
    const snapshot = frontPageSnapshot();
    assert.equal(snapshot.cards, 60);
    assert.equal(snapshot.tagline, "1,000+ VERIFIED DEALS");
    assert.equal(snapshot.frontCards.length, 48);
    assert.deepEqual(snapshot.frontCards[47],
      {key: "key47", item: visible.item, store: "Target", category: "home", url: "https://target.com/product"});
  } finally { delete global.document; }
}

// Footer: the enabled event pages' links follow the fixed footer links, in config order.
{
  const fs = require("node:fs"), path = require("node:path");
  const {eventLinks} = require("../scripts/site_check_logic.cjs");
  const config = fs.readFileSync(path.join(__dirname, "../config.yaml"), "utf8");
  assert.deepEqual(eventLinks(config), [["/black-friday/", "Black Friday"], ["/cyber-monday/", "Cyber Monday"]]);
  assert.deepEqual(eventLinks(config.replace(/(\n[ \t]+event_pages:\n[ \t]+enabled:) true/, "$1 false")), []);
  assert.deepEqual(eventLinks("seo:\n  hub_min_items: 10\n"), []);
}

// Public tool pages append their two links after the event pages; preview adds none.
// Deal Spotter (with buttons_to_tools) and Review Checker follow, in publish order.
{
  const fs = require("node:fs"), path = require("node:path");
  const {toolLinks} = require("../scripts/site_check_logic.cjs");
  const config = fs.readFileSync(path.join(__dirname, "../config.yaml"), "utf8");
  const checker = [["/amazon-deal-checker/", "Amazon Deal Checker"], ["/amazon-discount-finder/", "Amazon Discount Finder"]];
  const spotter = [["/deal-spotter/", "Deal Spotter"], ["/fakespot-alternative/", "Fakespot alternative"]];
  const reviews = [["/review-checker/", "Review Checker"]];
  assert.deepEqual(toolLinks(config), [...checker, ...spotter, ...reviews]);
  assert.deepEqual(toolLinks(config.replace(/^tool_pages: \{enabled: true, public: true\}/m,
    "tool_pages: {enabled: true, public: false}")), [...spotter, ...reviews]);
  assert.deepEqual(toolLinks(config.replace(/^(deal_spotter:\n  enabled:) true/m, "$1 false")), [...checker, ...reviews]);
  assert.deepEqual(toolLinks(config.replace(/^buttons_to_tools: true/m, "buttons_to_tools: false")), [...checker, ...reviews]);
  assert.deepEqual(toolLinks(config.replace(/^(review_checker:\n  enabled:) true/m, "$1 false")), [...checker, ...spotter]);
  assert.deepEqual(toolLinks("tool_pages: {enabled: false, public: true}\n"), []);
  assert.deepEqual(toolLinks("seo:\n  hub_min_items: 10\n"), []);
  // Only the block's own enabled counts, not a nested one.
  assert.deepEqual(toolLinks("review_checker:\n  min_reviews: 100\n  sub:\n    enabled: true\n"), []);
}

// Execute the real Home step: use the browser's cache-busted GET, never the
// earlier report fetch, HEAD requests, failed responses or another origin.
(async () => {
  const fs = require("node:fs"), vm = require("node:vm");
  const logic = require("../scripts/site_check_logic.cjs");
  const source = fs.readFileSync(require("node:path").join(__dirname, "../scripts/site_check.js"), "utf8");
  const start = source.indexOf("async function walk(");
  const end = source.indexOf("  // Preserve the photo report's", start);
  const listeners = new Map(), SITE = "https://wowpriced.com/";
  const card = {key: "gone", item: "Robot vacuum", store: "Woot", category: ""};
  const current = {deals: [{...card, category: "home", status: "dead"}]};
  const stale = {deals: [{...card, category: "auto", status: "alive"}]};
  const page = {
    on: (event, fn) => listeners.set(event, fn),
    off: (event, fn) => { assert.equal(listeners.get(event), fn); listeners.delete(event); },
    goto: async () => {
      for (const [url, method, ok, doc] of [
        [SITE + "deals.json?t=123", "GET", true, current],
        [SITE + "deals.json", "HEAD", true, stale],
        [SITE + "deals.json?t=124", "GET", false, stale],
        ["https://another.example/deals.json", "GET", true, stale],
      ]) listeners.get("response")({url: () => url, request: () => ({method: () => method}),
        ok: () => ok, json: async () => doc});
      return {status: () => 200, text: async () => "<html>Home</html>"};
    },
    waitForFunction: async () => {}, waitForTimeout: async () => {},
    evaluate: async fn => {
      assert.equal(fn, logic.frontPageSnapshot);
      return {cards: 48, top: 0, clock: "10:00", tagline: "100 live deals", frontCards: [card]};
    },
  };
  const context = {page, doc: stale, ...logic, URL, SITE, SITE_URL: new URL(SITE),
    STEP_TIMEOUT_MS: 10000, checkFooter: () => [], eventLinks: () => [],
    fs: {readFileSync: () => ""}, path: {join: () => ""}, __dirname: "",
    check: (ok, reason) => assert(ok, reason)};
  const shown = await vm.runInNewContext(source.slice(start, end) +
    '\nreturn shown;\n}\nwalk(page, doc, "desktop");', context);
  assert.equal(shown.frontCards[0].category, "home");
  assert.equal(shown.frontCards[0].unidentified, false);
  assert.equal(listeners.has("response"), false);
})().catch(error => { console.error(error); process.exitCode = 1; });

// Get deal source hosts fail at any of the first 48 positions, using the
// visible anchor even when a separately fetched publish has a different URL.
{
  const fs = require('node:fs'), path = require('node:path');
  const {frontRules, frontPageFlags, frontPageFlagLine, identifyFrontCards, dealSourceHosts} =
    require('../scripts/site_check_logic.cjs');
  const config = fs.readFileSync(path.join(__dirname, '../config.yaml'), 'utf8');
  const sources = fs.readFileSync(path.join(__dirname, '../sources.yaml'), 'utf8');
  const rules = frontRules(config, sources);
  const card = {key: 'fixture', item: 'Kitchen fixture', store: 'Amazon', category: 'home',
    url: 'https://www.amazon.com/dp/B012345678?tag=wowpriced-20'};
  for (const host of ['cc.dealsofamerica.com', 'slickdeals.net', 'links.southernsavers.com',
      'moneysavingmom.com', 'go.dealmoon.com', 'hip2save.com', 'thekrazycouponlady.com',
      'freestufffinder.com', 'sub.hip2save.com', 'www.slickdeals.net']) {
    for (const pos of [1, 24, 25, 48]) {
      const cards = Array.from({length: 49}, () => ({...card}));
      cards[pos - 1].url = `https://${host}/post`;
      const checked = frontPageFlags(identifyFrontCards(cards, [{deals: [card]}]), rules);
      assert.equal(checked.ok, false);
      assert.equal(checked.flags.length, 1);
      assert.equal(checked.flags[0].pos, pos);
      assert.ok(frontPageFlagLine(checked.flags[0]).includes('Get deal opens deal site'));
      assert.ok(frontPageFlagLine(checked.flags[0]).includes(host.replace(/^www\./, '')));
    }
  }
  for (const url of ['https://hip2save.com.evil.example/post', 'https://www.walmart.com/ip/123',
      'https://morningsave.com/product', 'https://woot.com/product', '', 'garbage']) {
    assert.deepEqual(frontPageFlags([{...card, url}], rules), {ok: true, flags: []});
  }
  const cards = Array.from({length: 49}, () => ({...card}));
  cards[48].url = 'https://hip2save.com/post';
  assert.deepEqual(frontPageFlags(cards, rules), {ok: true, flags: []});
  assert.deepEqual(dealSourceHosts('  competitor_tracker_hosts: [track.example,\n    second.example]',
    'sources:\n  - name: fixture\n    site: https://blog.example/\n    url: https://feed.example/rss\n' +
    '  - name: store\n    site: https://www.morningsave.com/\n'),
    ['track.example', 'second.example', 'blog.example']);
}
