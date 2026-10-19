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
  if (pct < rules.minPhotoPct) problems.push(`photos showing ${pct.toFixed(2)}% < ${rules.minPhotoPct}%`);
  if (broken > rules.maxBroken) problems.push(`${broken} broken photos > ${rules.maxBroken}`);
  if (firstScreen < rules.firstScreen) {
    problems.push(`${firstScreen} of the top 24 cards show a photo < ${rules.firstScreen}`);
  }
  return {ok: !problems.length, reason: problems.join("; ")};
}

// Front page (operator 2026-10-16: "stop putting random car parts on the front page"):
// niche categories and model-specific car parts never belong on the first screens.
// Every flag prints each hour; one in the first 24 cards turns the check red.
const VEHICLE_PART = /\b(?:cv axles?|radiators?|ac compressors?|brake (?:kits?|pads|rotors|calipers?)|catalytic converters?|alternators?|control arms?|struts?|tie rods?|wheel (?:hubs?|bearings?)|spark plugs?|ignition coils?|fuel pumps?|o2 sensors?|oxygen sensors?|(?:head|tail)[ -]?light assembl(?:y|ies)|floor mats? for)\b/i;

function frontRules(config) {
  const match = config.match(/^[ \t]+niche_categories:[ \t]*\[([^\]]*)\]/m);
  if (!match) throw new Error("front_page.niche_categories missing from config.yaml");
  return {niche: match[1].split(",").map((s) => s.trim()).filter(Boolean)};
}

// seo.event_pages (Black Friday, Cyber Monday): when enabled, every footer
// ends with one link per event, in config order.
function eventLinks(config) {
  const lines = config.split("\n"), start = lines.findIndex((l) => /^[ \t]+event_pages:[ \t]*$/.test(l));
  if (start < 0) return [];
  const indent = lines[start].search(/\S/), links = [];
  let enabled = false, slug = "";
  for (const line of lines.slice(start + 1)) {
    if (line.trim() && line.search(/\S/) <= indent) break;
    const m = line.match(/^[ \t]+(?:- )?(enabled|slug|name):[ \t]*(.*?)[ \t]*(?:#.*)?$/);
    if (!m) continue;
    if (m[1] === "enabled" && line.search(/\S/) === indent + 2) enabled = m[2] === "true";
    else if (m[1] === "slug") slug = m[2];
    else if (m[1] === "name" && slug) { links.push([`/${slug}/`, m[2]]); slug = ""; }
  }
  return enabled ? links : [];
}

// Runs inside Chromium: take identity and ordering together, before the walk
// changes filters. textContent/getAttribute already decode HTML entities.
function frontPageSnapshot() {
  const cards = Array.from(document.querySelectorAll("#content .card[data-key]"));
  return {
    cards: cards.length, top: 0,
    clock: (document.getElementById("countdown") || {}).textContent || "",
    tagline: ((el) => el ? (el.innerText ?? el.textContent ?? "") : "")(document.getElementById("tagline")),
    frontCards: cards.slice(0, 48).map((c) => {
      const labels = c.matches("[data-category]") ? c : c.querySelector("[data-category]");
      return {key: c.getAttribute("data-key"),
        item: (c.querySelector(".title")?.textContent || "").trim(),
        store: (c.querySelector(".storechip")?.textContent || "").trim(),
        category: labels?.getAttribute("data-category") || ""};
    }),
  };
}

function identifyFrontCards(cards, publishes) {
  const byKey = new Map();
  for (const doc of publishes) {
    for (const d of [...(doc?.deals || []), ...(doc?.spotlight || [])]) {
      const key = String(d.key || d.dedupe_key || "");
      if (!byKey.has(key)) byKey.set(key, []);
      byKey.get(key).push(d);
    }
  }
  return cards.map((c) => {
    // Never replace the visible title with a separately fetched publication.
    // If multiple fetched versions disagree, fail closed unless DOM labels
    // supply the category. Include gone cards: the wall deliberately shows them.
    const matches = (byKey.get(c.key) || []).filter((d) => !d.locked &&
      String(d.item || "").trim() === c.item && String(d.store || "").trim() === c.store);
    const categories = new Set(matches.map((d) => d.category || "other"));
    const category = c.category || (categories.size === 1 ? [...categories][0] : "");
    return {...c, category, unidentified: !c.item || !c.store || !category};
  });
}

function frontPageFlagLine(f) {
  return f.unidentified ? `unidentified card #${f.pos} key=${f.key}`
    : `#${f.pos} ${f.why}: ${f.store} | ${f.item}`;
}

function frontPageFlags(cards, rules) {
  const flags = [];
  cards.forEach((d, i) => {
    if (d.unidentified || !d.item || !d.store) {
      flags.push({pos: i + 1, unidentified: true, key: String(d.key || "")});
      return;
    }
    const why = [];
    if (rules.niche.includes(d.category || "other")) why.push(`niche category ${d.category || "other"}`);
    if (VEHICLE_PART.test(String(d.item || ""))) why.push("car part");
    if (why.length) flags.push({pos: i + 1, why: why.join(", "), store: String(d.store || ""),
                                item: String(d.item || "").slice(0, 70)});
  });
  return {ok: !flags.some((f) => f.pos <= 24), flags};
}

module.exports = {amazonTag, checkAmazonTag, checkSovrnLinks, overrideLinks, overrideNote,
                  photoRules, checkPhotos, frontRules, frontPageSnapshot, identifyFrontCards,
                  frontPageFlagLine, frontPageFlags, eventLinks};
