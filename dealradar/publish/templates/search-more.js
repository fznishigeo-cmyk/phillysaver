  var searchMoreSettings = {{SEARCH_MORE}}, searchMoreSeen = new Map();
  var searchMoreExclude = {{SEARCH_MORE_EXCLUDE}};
  searchMoreExclude = searchMoreExclude ? new RegExp(searchMoreExclude, "iu") : null;
  var searchMoreObserver, searchMoreTimer, searchMoreRequest, searchMoreActive = "";
  var searchSpellingState = null, searchSpellingTried = new Set(), searchSpellingEmpty = new Set();
  var searchSpellingValue = document.getElementById("q").value;
  var searchShoppingWords = {{SHOPPING_WORDS}};
  function searchMoreQuery() { return searchAmazonQuery().trim().toLowerCase().replace(/\s+/g, " "); }
  function searchMoreAdult(text) { return !!searchMoreExclude && searchMoreExclude.test(text); }
  function searchMoreAllowed(q) {
    return amazonResultsEnabled() && q.length >= searchMoreSettings.min_query_chars &&
      /^[\p{L}\p{N} -]{2,60}$/u.test(q) && !searchMoreAdult(q);
  }
  function searchMoreAsins(exclude) {
    var seen = new Set();
    document.querySelectorAll("#content .card a[href]").forEach(function (link) {
      if (link.closest(exclude)) return;
      try {
        var url = new URL(link.href);
        if (!/^(?:(?:www|smile|m)\.)?amazon\.com$/.test(url.hostname)) return;
        var match = url.pathname.match(/\/(?:dp|gp\/product|gp\/aw\/d|exec\/obidos(?:\/ASIN)?)\/([a-z0-9]{10})(?:\/|$)/i);
        var asin = match ? match[1] : url.searchParams.get("asin");
        if (/^[a-z0-9]{10}$/i.test(asin || "")) seen.add(asin.toUpperCase());
      } catch (_) {}
    });
    return seen;
  }
  function searchMoreDetach() {
    if (searchMoreObserver) searchMoreObserver.disconnect();
    clearTimeout(searchMoreTimer);
    searchMoreTimer = null;
  }
  function searchMoreCancel() {
    searchMoreDetach();
    if (searchMoreRequest) searchMoreRequest.abort();
    searchMoreRequest = null;
    searchMoreActive = "";
  }
  function searchMoreMount(rows) {
    var q = searchMoreQuery();
    if (!searchMoreAllowed(q) || !rows.some(function (d) { return d.status !== "dead"; })) return;
    if (searchMoreActive !== q) { searchMoreCancel(); searchMoreActive = q; }
    if (!searchMoreSeen.has(q)) {
      if (searchMoreSeen.size >= 50) searchMoreSeen.delete(searchMoreSeen.keys().next().value);
      searchMoreSeen.set(q, {pages:[], next:0, done:false, pending:false,
        ready:searchSpellingState && searchSpellingState.corrected === q ? searchSpellingState.ready : 0});
    }
    var content = document.getElementById("content"), existing = document.getElementById("search-more");
    if (existing) existing.remove();
    content.insertAdjacentHTML("beforeend", '<section id="search-more" class="search-more" data-clarity-mask="true" aria-labelledby="search-more-title">' +
      '<h2 id="search-more-title">More from Amazon for &quot;' + esc(q) + '&quot;</h2><div class="grid"></div>' +
      '<div class="search-more-load"><button type="button" class="pwbtn alt">Load more Amazon products</button></div>' +
      '<div class="search-amazon-top"><a class="getdeal" data-search-amazon href="https://www.amazon.com/?tag=' +
      encodeURIComponent(amazonResultsSettings.tag) + '" target="_blank" rel="sponsored nofollow noopener">See all &quot;' +
      esc(q) + '&quot; on Amazon →</a><small>Amazon links may earn us a commission.</small></div></section>');
    searchMorePaint(q);
  }
  function searchMorePaint(q) {
    var box = document.getElementById("search-more"), entry = searchMoreSeen.get(q);
    if (!box || !entry || searchMoreActive !== q || searchMoreQuery() !== q) return;
    searchMoreDetach();
    var seen = searchMoreAsins("#search-more");
    box.querySelector(".grid").innerHTML = entry.pages.map(function (items) {
      return amazonResultsCards(items, seen).join("");
    }).join("");
    var sentinel = box.querySelector(".search-more-load"), button = sentinel.querySelector("button");
    sentinel.hidden = entry.done || entry.next >= searchMoreSettings.pages;
    box.setAttribute("aria-busy", String(entry.pending));
    button.hidden = "IntersectionObserver" in window;
    button.onclick = function () { searchMoreLoad(q); };
    if (sentinel.hidden || entry.pending || !("IntersectionObserver" in window)) return;
    searchMoreObserver = new IntersectionObserver(function (entries) {
      if (entries.some(function (e) { return e.isIntersecting && document.body.contains(e.target); })) searchMoreLoad(q);
    }, {rootMargin:"300px"});
    searchMoreObserver.observe(sentinel);
  }
  function searchMoreLoad(q) {
    var entry = searchMoreSeen.get(q);
    if (!entry || entry.pending || entry.done || entry.next >= searchMoreSettings.pages ||
        searchMoreActive !== q || searchMoreQuery() !== q || !document.getElementById("search-more")) return;
    searchMoreDetach();
    // Space consecutive pages past the server's completion cooldown.
    searchMoreTimer = setTimeout(function () {
      searchMoreTimer = null;
      if (searchMoreQuery() !== q || !document.getElementById("search-more")) return;
      var page = entry.next++, controller = new AbortController();
      entry.pending = true; searchMoreRequest = controller;
      searchMorePaint(q);
      track("search_more_page", {page:page});
      fetch("/api/search?q=" + encodeURIComponent(q) + "&page=" + page,
        {signal:controller.signal, cache:"no-store", credentials:"omit", referrerPolicy:"no-referrer"})
        .then(function (r) { return r.ok ? r.json() : {items:[], reason:"unavailable"}; })
        .then(function (data) {
          var items = data && Array.isArray(data.items) ? data.items.slice(0, 10) : [];
          if (controller.signal.aborted || !data || data.reason || !items.length) { entry.done = true; return; }
          entry.pages.push(items);
        }).catch(function () { entry.done = true; }).finally(function () {
          entry.pending = false; entry.ready = Date.now() + 1100;
          if (searchMoreRequest === controller) searchMoreRequest = null;
          if (!controller.signal.aborted) searchMorePaint(q);
        });
    }, Math.max(0, entry.ready - Date.now()));
  }
  function searchSpellingSuggest(q) {
    var vocabulary = new Map(), bigrams = new Set();
    searchShoppingWords.split(" ").forEach(function (word) { vocabulary.set(word, 0); });
    searchInventory().forEach(function (d) {
      if (searchMoreAdult(d.item)) return;
      var words = searchWords(d.item);
      words.forEach(function (word, i) {
        if (/^[a-z]+$/.test(word)) vocabulary.set(word, (vocabulary.get(word) || 0) + 1);
        if (i) bigrams.add(words[i - 1] + " " + word);
      });
    });
    var words = q.split(" ");
    return words.map(function (word, i) {
      if (!/^[a-z]{4,}$/.test(word)) return word;
      function score(candidate) {
        return Number(i > 0 && bigrams.has(words[i - 1] + " " + candidate)) +
          Number(i + 1 < words.length && bigrams.has(candidate + " " + words[i + 1]));
      }
      var best = word, rank = score(word), frequency = vocabulary.has(word) ? vocabulary.get(word) : -1;
      vocabulary.forEach(function (count, candidate) {
        if (candidate === word || !oneSearchEdit(word, candidate)) return;
        var candidateRank = score(candidate);
        if (candidateRank > rank || (candidateRank === rank && !vocabulary.has(word) && count > frequency)) {
          best = candidate; rank = candidateRank; frequency = count;
        }
      });
      return best;
    }).join(" ");
  }
  function searchSpellingRemember(q, data) {
    if (data && !data.reason && Array.isArray(data.items) && !data.items.length) searchSpellingEmpty.add(q);
    else searchSpellingEmpty.delete(q);
    if (searchSpellingState && searchSpellingState.corrected === q) searchSpellingState.data = data;
  }
  function searchSpellingCached(q, items) {
    if (searchSpellingEmpty.has(q) && searchSpellingTry(q, {items:[]})) return;
    amazonResultsPaint(q, items);
  }
  function searchSpellingTry(q, data) {
    searchSpellingRemember(q, data);
    if (!data || data.reason || !Array.isArray(data.items) || data.items.length || searchSpellingState ||
        searchSpellingTried.has(q) || searchMoreQuery() !== q || !searchMoreAllowed(q) || filtered(searchInventory()).length) return false;
    searchSpellingTried.add(q);
    var corrected = searchSpellingSuggest(q);
    if (corrected === q || !searchMoreAllowed(corrected)) { track("search_spelling", {corrected:false}); return false; }
    searchSpellingState = {original:document.getElementById("q").value, corrected:corrected, ready:Date.now() + 1100, data:null};
    searchSpellingTried.add(corrected);
    track("search_spelling", {corrected:true});
    if (typeof analyzeCancel === "function") analyzeCancel();
    amazonResultsCancel(); searchMoreCancel();
    document.getElementById("q").value = searchSpellingValue = corrected;
    updateSearch();
    return true;
  }
  function searchSpellingPaint() {
    var box = document.getElementById("search-spelling");
    if (searchSpellingState && searchMoreQuery() !== searchSpellingState.corrected) searchSpellingState = null;
    box.hidden = !searchSpellingState;
    box.innerHTML = searchSpellingState ? 'Showing results for <b>' + esc(searchSpellingState.corrected) +
      '</b> · <a href="#" data-search-original>Search instead for &quot;' + esc(searchSpellingState.original) + '&quot;</a>' : '';
  }
  document.addEventListener("click", function (event) {
    if (!searchSpellingState || !event.target.closest || !event.target.closest("[data-search-original]")) return;
    event.preventDefault();
    var original = searchSpellingState.original;
    searchSpellingState = null;
    if (typeof analyzeCancel === "function") analyzeCancel();
    amazonResultsCancel(); searchMoreCancel();
    document.getElementById("q").value = searchSpellingValue = original;
    track("search_spelling", {corrected:false});
    updateSearch();
  });
  document.getElementById("q").addEventListener("input", function () {
    // Undo keeps this query literal until the visitor actually changes the text.
    if (this.value !== searchSpellingValue) {
      searchSpellingValue = this.value;
      searchSpellingState = null;
      searchSpellingTried.clear();
    }
    if (searchMoreQuery() !== searchMoreActive) searchMoreCancel();
    searchSpellingPaint();
  });
