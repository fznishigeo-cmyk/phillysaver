  var amazonResultsSettings = {{SEARCH_RESULTS}}, amazonResultsSeen = new Map();
  var amazonResultsPreview = new URLSearchParams(location.search).get("preview") === "search";
  var amazonResultsTimer, amazonResultsRequest, amazonResultsQuery = "";
  function amazonResultsEnabled() {
    return amazonResultsSettings.mode === "on" || (amazonResultsSettings.mode === "preview" &&
      amazonResultsPreview);
  }
  function amazonResultsCancel() {
    clearTimeout(amazonResultsTimer);
    if (amazonResultsRequest) amazonResultsRequest.abort();
    amazonResultsRequest = null;
    amazonResultsQuery = "";
    var box = document.getElementById("amazon-results");
    if (box) box.innerHTML = "";
  }
  function amazonResultsPaint(q, items) {
    var box = document.getElementById("amazon-results");
    if (!box || amazonResultsQuery !== q) return;
    var cards = items.slice(0, 10).filter(function (d) {
      return d && /^[A-Z0-9]{10}$/.test(d.asin) && typeof d.title === "string" &&
        Number.isSafeInteger(d.price_cents) && d.price_cents > 0 &&
        Number.isFinite(Date.parse(d.as_of)) && Date.parse(d.as_of) <= Date.now() &&
        Date.now() - Date.parse(d.as_of) <= 86400000;
    }).map(function (d) {
      var image = typeof d.image === "string" && /^https:\/\/m\.media-amazon\.com\/images\/I\/[A-Za-z0-9+_.%-]+$/.test(d.image);
      var time = new Date(d.as_of).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit", hour12:false});
      return '<article class="card">' +
        (image ? '<div class="media"><img src="' + esc(d.image) + '" alt="' + esc(d.title) +
          '" width="160" height="160" loading="lazy" decoding="async" referrerpolicy="no-referrer"></div>' : '') +
        '<div class="body"><h3 class="title">' + esc(d.title) + '</h3><p class="pricerow"><span class="now">$' +
        (d.price_cents / 100).toFixed(2) + '</span></p><p class="headline">Amazon price as of ' + esc(time) +
        '</p><a class="getdeal" href="https://www.amazon.com/dp/' +
        d.asin + '?tag=' + encodeURIComponent(amazonResultsSettings.tag) +
        '" rel="sponsored nofollow noopener" target="_blank">View on Amazon</a></div></article>';
    });
    box.innerHTML = cards.length ? '<section aria-labelledby="amazon-results-title"><h2 id="amazon-results-title">From Amazon right now</h2><div class="grid">' + cards.join("") + '</div></section>' : "";
  }
  function queueAmazonResults(rows) {
    var q = searchAmazonQuery().trim().toLowerCase().replace(/\s+/g, " ");
    if (!amazonResultsEnabled() || rows.some(function (d) { return d.status !== "dead"; }) ||
        !/^[\p{L}\p{N} -]{2,60}$/u.test(q)) { amazonResultsCancel(); return; }
    if (q !== amazonResultsQuery) { amazonResultsCancel(); amazonResultsQuery = q; }
    clearTimeout(amazonResultsTimer);
    amazonResultsTimer = setTimeout(function () {
      if (amazonResultsSeen.has(q)) { amazonResultsPaint(q, amazonResultsSeen.get(q)); return; }
      if (amazonResultsRequest) return;
      // Remember even failures: rerenders and repeat queries cannot spend twice.
      amazonResultsSeen.set(q, []);
      var controller = new AbortController();
      amazonResultsRequest = controller;
      fetch("/api/search?q=" + encodeURIComponent(q), {signal:controller.signal, cache:"no-store", credentials:"omit"})
        .then(function (r) { return r.ok ? r.json() : {items:[]}; })
        .then(function (data) {
          if (controller.signal.aborted || amazonResultsQuery !== q) return;
          var items = Array.isArray(data.items) ? data.items.slice(0, 10) : [];
          amazonResultsSeen.set(q, items);
          amazonResultsPaint(q, items);
          if (typeof window.wpTrack === "function") window.wpTrack("search_amazon_results", {count:items.length});
        }).catch(function () {}).finally(function () {
          if (amazonResultsRequest === controller) amazonResultsRequest = null;
        });
    }, 200);
  }
  document.getElementById("q").addEventListener("input", function () {
    var q = searchAmazonQuery().trim().toLowerCase().replace(/\s+/g, " ");
    if (q !== amazonResultsQuery) amazonResultsCancel();
  });
