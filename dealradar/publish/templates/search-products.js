// Shared by the page and Pages Function; category labels are supplied by the page.
  var CATGROUPS = [];
  function normalizeSearch(value) {
    var parts = String(value || "").toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
      .replace(/[\u2010-\u2015]/g, "-").replace(/&/g, " and ").match(/[a-z0-9]+(?:[./-][a-z0-9]+)*\+?/g) || [];
    // Preserve the whole digit-bearing identifier, including model
    // separators, decimals and fractions. Ordinary punctuation separates words.
    return parts.map(function (part) { return /[0-9]/.test(part) ? part : part.replace(/[./+-]/g, " "); }).join(" ");
  }
  export function searchWords(value) { return normalizeSearch(value).split(" ").filter(Boolean); }
  var searchIndex = new WeakMap();
  function indexedWords(d) {
    var cached = searchIndex.get(d);
    if (cached) return cached;
    var labels = CATGROUPS.filter(function (g) {
      return Array.isArray(g[1]) && g[1].indexOf(d.category || "other") !== -1;
    }).map(function (g) { return g[0]; });
    var text = [d.item, d.brand, d.store, d.category, labels.join(" "), (d.conditions || []).join(" ")].join(" ");
    var parts = searchWords(text);
    // Retain BestOffice and its written camel-case words; never join
    // unrelated fields or split an alphanumeric model into partial numbers.
    (text.match(/[a-zA-Z]+/g) || []).forEach(function (word) {
      if (/[a-z][A-Z]/.test(word) && parts.indexOf(word.toLowerCase()) !== -1) {
        parts = parts.concat(searchWords(word.replace(/([a-z])([A-Z])/g, "$1 $2")));
      }
    });
    // A model's own segments match too ("1000xm5" in "wh-1000xm5"), never a
    // partial number ("65" stays out of "6500").
    parts.slice().forEach(function (part) {
      part.split("-").forEach(function (seg) {
        if (/[a-z]/.test(seg) && /[0-9]/.test(seg) && seg !== part) parts.push(seg);
      });
    });
    var unique = Array.from(new Set(parts));
    cached = {exact: new Set(unique),
              words: unique.filter(function (w) { return !/[0-9]/.test(w); }),
              ordinary: unique.filter(function (w) { return /^[a-z]{4,}$/.test(w); })};
    searchIndex.set(d, cached);
    return cached;
  }
  function oneSearchEdit(a, b) {
    if (Math.abs(a.length - b.length) > 1) return false;
    var i = 0, j = 0, edits = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { i++; j++; continue; }
      if (++edits > 1) return false;
      if (a.length >= b.length) i++;
      if (b.length >= a.length) j++;
    }
    return edits + Number(i < a.length || j < b.length) <= 1;
  }
  export function matchesSearch(d, words) {
    var index = indexedWords(d);
    return words.length > 0 && words.every(function (word) {
      if (index.exact.has(word)) return true;
      if (/[0-9]/.test(word)) return false;  // numbers and models match exactly
      // Ordinary words match as typed so far ("vac" -> vacuum, "shoe" ->
      // shoes), and 5+ letter words forgive one typo.
      return index.words.some(function (part) { return part.indexOf(word) === 0; }) ||
        (/^[a-z]{5,}$/.test(word) && index.ordinary.some(function (part) { return oneSearchEdit(word, part); }));
    });
  }

export function monthlyBoughtLabel(value) {
  if (!Number.isSafeInteger(value) || value < 50) return "";
  var floor = 0;
  for (var scale = 10; scale <= value; scale *= 10) {
    for (var multiple of [1, 2, 5]) {
      var bucket = scale * multiple;
      if (bucket >= 50 && bucket <= value) floor = bucket;
    }
  }
  var unit = floor >= 1e9 ? 1e9 : floor >= 1e6 ? 1e6 : floor >= 1e3 ? 1e3 : 1;
  return floor / unit + (unit === 1e9 ? "B" : unit === 1e6 ? "M" : unit === 1e3 ? "K" : "") + "+ bought in past month";
}
