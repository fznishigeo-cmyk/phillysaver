const assert = require("node:assert/strict");
const {amazonTag, checkAmazonTag, checkSovrnLinks} = require("../scripts/site_check_logic.cjs");
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
          s === ".storechip" ? {textContent: " Target "} : {getAttribute: () => "home"},
      }));
    },
  };
  try {
    const snapshot = frontPageSnapshot();
    assert.equal(snapshot.cards, 60);
    assert.equal(snapshot.tagline, "1,000+ VERIFIED DEALS");
    assert.equal(snapshot.frontCards.length, 48);
    assert.deepEqual(snapshot.frontCards[47],
      {key: "key47", item: visible.item, store: "Target", category: "home"});
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
{
  const fs = require("node:fs"), path = require("node:path");
  const {toolLinks} = require("../scripts/site_check_logic.cjs");
  const config = fs.readFileSync(path.join(__dirname, "../config.yaml"), "utf8");
  assert.deepEqual(toolLinks(config), [["/amazon-deal-checker/", "Amazon Deal Checker"],
    ["/amazon-discount-finder/", "Amazon Discount Finder"]]);
  assert.deepEqual(toolLinks(config.replace(/^tool_pages: \{enabled: true, public: true\}/m,
    "tool_pages: {enabled: true, public: false}")), []);
  assert.deepEqual(toolLinks("tool_pages: {enabled: false, public: true}\n"), []);
  assert.deepEqual(toolLinks("seo:\n  hub_min_items: 10\n"), []);
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
