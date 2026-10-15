// Pure checks shared by the hourly walk and its offline tests.
function amazonTag(config) {
  const block = config.match(/^affiliate:\s*(?:#.*)?\n((?:[ \t]+[^\n]*\n|\n)*)/m);
  const match = block && block[1].match(/^[ \t]+amazon_tag:[ \t]*(?:"([^"]*)"|'([^']*)'|([^#\n]*))/m);
  if (!match) throw new Error("affiliate.amazon_tag missing from config.yaml");
  return (match[1] ?? match[2] ?? match[3]).trim();
}

function checkAmazonTag(doc, tag) {
  if (!tag) return {ok: true, note: "n/a (no Amazon tag yet)"};
  let badLinks = 0, example = "";
  for (const deal of doc.deals || []) {
    let url;
    try { url = new URL(deal.url); } catch { continue; }
    if (url.protocol !== "https:" ||
        !["amazon.com", "www.amazon.com", "smile.amazon.com"].includes(url.hostname)) continue;
    const tags = url.searchParams.getAll("tag");
    if (tags.length !== 1 || tags[0] !== tag) {
      badLinks++;
      if (!example) {
        const query = new URLSearchParams();
        for (const value of tags) query.append("tag", value);
        example = url.pathname + (query.size ? "?" + query.toString() : "");
      }
    }
  }
  const disclosure = String((doc.meta || {}).disclosure || "").includes("As an Amazon Associate");
  if (badLinks || !disclosure) return {ok: false, badLinks, example,
    reason: `${badLinks} bad Amazon links${example ? `; example ${example}` : ""}` +
      (!disclosure ? "; missing Amazon disclosure" : "")};
  return {ok: true, badLinks: 0};
}

function checkSovrnLinks(doc) {
  let links = 0, badLinks = 0;
  for (const deal of doc.deals || []) {
    let url;
    try { url = new URL(deal.url); } catch { continue; }
    if (!["redirect.viglink.com", "sovrn.co"].includes(url.hostname)) continue;
    links++;
    // Sovrn short-link overrides carry no embedded destination.
    if (url.hostname === "sovrn.co") continue;
    let merchant;
    try { merchant = new URL(url.searchParams.get("u")); } catch {}
    const keys = url.searchParams.getAll("key"), destinations = url.searchParams.getAll("u");
    if (url.protocol !== "https:" || keys.length !== 1 || !/^[0-9a-f]{32}$/.test(keys[0]) ||
        destinations.length !== 1 || !merchant || merchant.protocol !== "https:" ||
        /(^|\.)amazon\.[a-z]{2,3}(\.[a-z]{2})?$/.test(merchant.hostname) ||
        ["amzn.to", "a.co", "link.amazon"].some(h => merchant.hostname === h || merchant.hostname.endsWith("." + h))) {
      badLinks++;
    }
  }
  return {ok: badLinks === 0, links, badLinks, reason: `${badLinks} malformed Sovrn links`};
}

// affiliate.link_overrides values in config.yaml (network onboarding links,
// e.g. Sovrn's first-click check): which of them a live deal carries now.
function overrideLinks(config) {
  const block = config.match(/^  link_overrides:\s*\n((?:    [^\n]*\n)*)/m);
  return block ? [...block[1].matchAll(/:\s*"(https:[^"]+)"/g)].map((m) => m[1]) : [];
}

function overrideNote(doc, links) {
  if (!links.length) return "";
  const live = new Set((doc.deals || []).filter((d) => d.status === "alive").map((d) => d.url));
  return links.map((l) => `${l.replace(/^https:\/\//, "")} ${live.has(l) ? "live" : "not on a live deal"}`).join(", ");
}

// Photo floors from config.yaml site_check (operator, 2026-10-15: "we need
// 99.99% of images showing up!"): a shortfall turns the hourly check red.
function photoRules(config) {
  const block = config.match(/^site_check:\s*(?:#.*)?\n((?:[ \t]+[^\n]*\n|\n)*)/m);
  const value = (key) => {
    const match = block && block[1].match(new RegExp(`^[ \\t]+${key}:[ \\t]*(\\d+(?:\\.\\d+)?)`, "m"));
    if (!match) throw new Error(`site_check.${key} missing from config.yaml`);
    return Number(match[1]);
  };
  return {minPhotoPct: value("min_photo_pct"), maxBroken: value("max_broken"),
          firstScreen: value("first_screen_photos")};
}

function checkPhotos({live, showing, broken, firstScreen}, rules) {
  const problems = [], pct = live ? (100 * showing) / live : 100;
  if (pct < rules.minPhotoPct) problems.push(`photos showing ${pct.toFixed(1)}% < ${rules.minPhotoPct}%`);
  if (broken > rules.maxBroken) problems.push(`${broken} broken photos > ${rules.maxBroken}`);
  if (firstScreen < rules.firstScreen) {
    problems.push(`${firstScreen} of the top 24 cards show a photo < ${rules.firstScreen}`);
  }
  return {ok: !problems.length, reason: problems.join("; ")};
}

module.exports = {amazonTag, checkAmazonTag, checkSovrnLinks, overrideLinks, overrideNote,
                  photoRules, checkPhotos};
