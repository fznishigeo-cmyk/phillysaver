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
