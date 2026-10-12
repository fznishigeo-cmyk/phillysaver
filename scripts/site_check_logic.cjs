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

module.exports = {amazonTag, checkAmazonTag, overrideLinks, overrideNote};
