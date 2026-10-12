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

module.exports = {amazonTag, checkAmazonTag};
