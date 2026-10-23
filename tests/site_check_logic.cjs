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
  // Fake Deal Spotter's typed-words box links Today's Deals (A52); All Discounts is not a finder filter.
  assert.deepEqual(checkToolAmazonUrl(finder.replace("210906366011", "23566064011"), tag, true), {ok: true});
  assert.equal(checkToolAmazonUrl(finder.replace("210906366011", "23566065011"), tag, true).ok, false);
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
  const spotter = [["/deal-spotter/", "Fake Deal Spotter"], ["/fakespot-alternative/", "Fakespot alternative"]];
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
  const context = {page, doc: stale, ...logic, URL, SITE, SITE_URL: new URL(SITE), siteRequests: 0,
    STEP_TIMEOUT_MS: 10000, checkFooter: () => [], eventLinks: () => [],
    fs: {readFileSync: () => ""}, path: {join: () => ""}, __dirname: "",
    check: (ok, reason) => assert(ok, reason)};
  const shown = await vm.runInNewContext(source.slice(start, end) +
    '\nreturn shown;\n}\nwalk(page, doc, "desktop");', context);
  assert.equal(shown.frontCards[0].category, "home");
  assert.equal(shown.frontCards[0].unidentified, false);
  assert.equal(listeners.has("response"), false);
  // A retried Home identifies its cards from its own load, not the failed try's publish.
  const load = page.goto;
  let failed = false;
  page.goto = async () => {
    if (failed) return load();
    failed = true;
    listeners.get("response")({url: () => SITE + "deals.json?t=122", request: () => ({method: () => "GET"}),
      ok: () => true, json: async () => stale});
    throw new Error("page.goto: Timeout 60000ms exceeded.");
  };
  Object.assign(context, {retriesLeft: 3, RETRY_WAIT_MS: 60000, lastAbort: "", setTimeout: resolve => resolve()});
  const [again, results] = await vm.runInNewContext(source.slice(start, end) +
    '\nreturn [shown, results];\n}\nwalk(page, doc, "desktop");', context);
  assert.equal(again.frontCards[0].category, "home");
  assert.equal(again.frontCards[0].unidentified, false);
  assert.equal(results[0].note, "retried after 60 s: passed (first try: page.goto: Timeout 60000ms exceeded.)");
})().catch(error => { console.error(error); process.exitCode = 1; });

// Operator 2026-10-23 ("check again a minute later before calling it a problem"): network
// errors, timeouts and HTTP 5xx/429 are transient; content failures and our own request limit are not.
{
  const {transientFailure} = require("../scripts/site_check_logic.cjs");
  for (const reason of ["about: fetch failed",  // #341, phone step 8
      "page.goto: net::ERR_CONNECTION_RESET at https://wowpriced.com/", "page.goto: Timeout 60000ms exceeded.",
      "page.waitForFunction: Timeout 10000ms exceeded.", "locator.waitFor: Timeout 40000ms exceeded.",
      "robots.txt: The operation was aborted due to timeout", "about: terminated", "HTTP 503", "Join HTTP 502",
      "deal-spotter HTTP 429", "sitemap.xml: fetch failed; fewer than 3 category hubs in sitemap",
      "page.goto: net::ERR_FAILED at https://wowpriced.com/ (guard: Route is already handled!)"]) {
    assert.equal(transientFailure(reason), true, reason);
  }
  for (const reason of ["HTTP 404", "HTTP 5030", "about: missing title", "footer link line changed", "page error",
      "amazon-deal-checker: tool Amazon URL has wrong or duplicate tag", "1 bad Amazon links; example /dp/B012345678",
      "source wall or filter still present", "publish missing, future, or >=45 minutes old", "about: site request limit (120)",
      "page.goto: net::ERR_FAILED at https://wowpriced.com/deal/x/ (guard: site request limit (120))"]) {
    assert.equal(transientFailure(reason), false, reason);
  }
}

// The real step(): a transient failure runs the same action once more after the wait, at most
// RETRIES_PER_RUN times per run (both devices). step() itself touches no page (this fake has no
// goto or click): a retry re-runs only the step's own action, which never opens an Amazon link.
(async () => {
  const fs = require("node:fs"), vm = require("node:vm");
  const source = fs.readFileSync(require("node:path").join(__dirname, "../scripts/site_check.js"), "utf8");
  const [, wait, cap] = source.match(/\nconst RETRY_WAIT_MS = (\d+), RETRIES_PER_RUN = (\d+);\n/).map(Number);
  assert.deepEqual([wait, cap], [60000, 3]);
  const head = source.slice(source.indexOf("async function walk("), source.indexOf('  await step(1, "Home"'));
  const listeners = new Map(), waits = [];
  const context = {...require("../scripts/site_check_logic.cjs"), RETRY_WAIT_MS: wait,
    setTimeout: (resolve, ms) => { waits.push(ms); resolve(); }};
  const walk = vm.runInNewContext(head + "\nfor (const [n, name, action] of steps) await step(n, name, action);\n" +
    "return JSON.stringify({results, errors});\n}\nwalk", context);
  const page = {on: (event, fn) => listeners.set(event, fn)};
  const run = async (steps, {retriesLeft = cap, lastAbort = ""} = {}) => {
    waits.length = 0;
    Object.assign(context, {steps, retriesLeft, lastAbort, siteRequests: 50});
    return JSON.parse(await walk(page, {}, "desktop"));
  };
  const tries = (...outcomes) => {
    const action = async () => {
      const outcome = outcomes[action.calls++];
      if (outcome instanceof Error) throw outcome;
      return outcome;
    };
    action.calls = 0;
    return action;
  };

  // A transient failure, then a pass: green, with a note that keeps the flake visible.
  const home = tries(Error("page.goto: net::ERR_CONNECTION_RESET at https://wowpriced.com/\nCall log: ..."), undefined);
  const tools = tries(Error("deal-spotter HTTP 503"), "tool pages ok");
  let out = await run([[1, "Home", home], [15, "Tools", tools]]);
  assert.deepEqual(out.results, [
    {n: 1, name: "Home", ok: true,
     note: "retried after 60 s: passed (first try: page.goto: net::ERR_CONNECTION_RESET at https://wowpriced.com/)"},
    {n: 15, name: "Tools", ok: true, note: "tool pages ok; retried after 60 s: passed (first try: deal-spotter HTTP 503)"}]);
  assert.deepEqual([home.calls, tools.calls, waits, context.retriesLeft], [2, 2, [60000, 60000], 1]);

  // A transient failure twice: red, after one retry.
  const about = tries(Error("about: fetch failed"), Error("about: fetch failed"));
  const deal = tries(Error("page.goto: Timeout 60000ms exceeded."), Error("missing Get deal link"));
  out = await run([[8, "Public pages / hubs", about], [13, "deal page / screenshot", deal]]);
  assert.deepEqual(out.results, [
    {n: 8, name: "Public pages / hubs", ok: false, reason: "about: fetch failed (failed again 60 s later)"},
    {n: 13, name: "deal page / screenshot", ok: false,
     reason: "missing Get deal link (failed again 60 s later; first try: page.goto: Timeout 60000ms exceeded.)"}]);
  assert.deepEqual([about.calls, deal.calls, waits], [2, 2, [60000, 60000]]);

  // Content failures, and our own request limit, fail at once: no wait, no retry.
  const footer = tries(Error("footer link line changed"));
  const tag = tries(Error("amazon-deal-checker: tool Amazon URL has wrong or duplicate tag"));
  out = await run([[1, "Home", footer], [15, "Tools", tag]]);
  assert.deepEqual(out.results.map(r => [r.ok, r.reason]),
    [[false, "footer link line changed"], [false, "amazon-deal-checker: tool Amazon URL has wrong or duplicate tag"]]);
  assert.deepEqual([footer.calls, tag.calls, waits, context.retriesLeft], [1, 1, [], cap]);
  const limited = tries(Error("page.goto: net::ERR_FAILED at https://wowpriced.com/best-deals-today/"), undefined);
  out = await run([[14, "best-today page / screenshot", limited]], {lastAbort: "site request limit (120)"});
  assert.deepEqual(out.results.map(r => [r.ok, r.reason]), [[false,
    "page.goto: net::ERR_FAILED at https://wowpriced.com/best-deals-today/ (guard: site request limit (120))"]]);
  assert.deepEqual([limited.calls, waits], [1, []]);

  // The cap holds across both devices: the fourth transient failure of the run fails at once.
  const flaky = () => tries(Error("HTTP 503"), undefined);
  const desktop = [flaky(), flaky()], phone = [flaky(), flaky()];
  out = await run(desktop.map((action, i) => [i + 1, "desktop", action]));
  assert(out.results.every(r => r.ok) && context.retriesLeft === cap - 2);
  out = await run(phone.map((action, i) => [i + 1, "phone", action]), {retriesLeft: context.retriesLeft});
  assert.deepEqual(out.results.map(r => [r.ok, r.note || r.reason]),
    [[true, "retried after 60 s: passed (first try: HTTP 503)"], [false, "HTTP 503"]]);
  assert.deepEqual([...desktop, ...phone].map(a => a.calls), [2, 2, 2, 1]);
  assert.equal(context.retriesLeft, 0);

  // The failed try's requests and page errors do not count; the retry's do.
  const reload = async () => {
    context.siteRequests += 12;
    listeners.get("pageerror")(`TypeError: Failed to fetch (try ${++reload.calls})`);
    if (reload.calls === 1) throw Error("page.waitForFunction: Timeout 10000ms exceeded.");
  };
  reload.calls = 0;
  out = await run([[1, "Home", reload]]);
  assert.equal(out.results[0].ok, true);
  assert.deepEqual(out.errors, ["TypeError: Failed to fetch (try 2)"]);
  assert.equal(context.siteRequests, 62);
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
