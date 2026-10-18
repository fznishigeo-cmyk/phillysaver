  var analyzeTask = null, analyzeSeen = new Map(), analyzeQueryPromise;
  var analyzeExclude = {{ANALYZE_EXCLUDE}};
  analyzeExclude = analyzeExclude ? new RegExp(analyzeExclude, "iu") : null;
  function analyzeAdult(text) { return !!analyzeExclude && analyzeExclude.test(text); }
  function analyzeInput(value) {
    var text = String(value || "").trim();
    if (!/^(?:[a-z][a-z0-9+.-]*:|\/\/|[^\s/]+\.[a-z]{2,}(?:[/:?#]|$))/i.test(text)) {
      return {kind:/(?:https?:\/\/|www\.|\b(?:amazon\.com|amzn\.to|a\.co)\/)/i.test(text) ? "invalid" : "query"};
    }
    var url;
    try { url = new URL(/^[a-z][a-z0-9+.-]*:/i.test(text) ? text : "https:" + (text.startsWith("//") ? "" : "//") + text); }
    catch (_) { return {kind:"invalid"}; }
    if (!/^https?:$/.test(url.protocol) || url.username || url.password || url.port || /[\\\s]/.test(text)) return {kind:"invalid"};
    if (/^(?:www\.)?(?:amzn\.to|a\.co)$/.test(url.hostname)) return {kind:"short"};
    if (!/^(?:(?:www|smile|m)\.)?amazon\.com$/.test(url.hostname)) return {kind:"other"};
    var path = url.pathname.match(/\/(?:dp|gp\/product|gp\/aw\/d|exec\/obidos(?:\/ASIN)?)\/([a-z0-9]{10})(?:\/|$)/i);
    var params = url.searchParams.getAll("asin"), asin = path ? path[1].toUpperCase() : "";
    if (params.length > 1 || (params.length && (!/^[a-z0-9]{10}$/i.test(params[0]) ||
        (asin && asin !== params[0].toUpperCase())))) return {kind:"invalid"};
    asin = asin || (params[0] || "").toUpperCase();
    return asin ? {kind:"amazon", asin:asin} : {kind:"invalid"};
  }
  function analyzeSearchText() {
    var value = document.getElementById("q").value.trim();
    return analyzeInput(value).kind === "query" ? value : "";
  }
  function analyzeKey(input) {
    return input.kind === "amazon" ? "asin=" + input.asin : input.kind === "query"
      ? "q=" + encodeURIComponent(searchAmazonQuery().trim().toLowerCase().replace(/\s+/g, " ")) : input.kind;
  }
  function analyzeCurrent(task) {
    return analyzeTask === task && task.key === analyzeKey(analyzeInput(document.getElementById("q").value));
  }
  function analyzeCancel() {
    if (analyzeTask && analyzeTask.controller) analyzeTask.controller.abort();
    analyzeTask = null;
  }
  function analyzeReason(reason) {
    var message = ({short:"Paste the full Amazon link (open it, then copy from the address bar)",
      other:"We check Amazon links for now", invalid:"Paste a full Amazon product link",
      disabled:"Live price checks are unavailable right now.", unavailable:"Live price checks are unavailable right now.",
      budget_unavailable:"Live price checks are unavailable right now.", budget_exhausted:"Today's live price checks have reached their limit.",
      rate_limited:"Live price checks are busy. Please try again shortly.", limit_reached:"Live price checks are busy. Please try again shortly.",
      timeout:"The price check took too long. Please try again.", keepa_error:"Amazon's current price could not be confirmed.",
      invalid_query:"Try a product name or a full Amazon product link.", no_price:"No current Amazon price could be confirmed.",
      excluded:"This product is not shown in our results."})[reason];
    return typeof message === "string" ? message : "Live price checks are unavailable right now.";
  }
  function analyzeFallback(task) {
    return '<p class="search-amazon"><a class="getdeal" data-analyze-fallback href="https://www.amazon.com/?tag=' +
      encodeURIComponent(amazonResultsSettings.tag) + '" target="_blank" rel="sponsored nofollow noopener">Search Amazon</a><small>Amazon links may earn us a commission.</small></p>';
  }
  function analyzeRender() {
    var task = analyzeTask;
    if (!task) return false;
    if (!analyzeCurrent(task)) { analyzeCancel(); return false; }
    if (task.finished && !task.reason) {
      if (task.local) {
        task.local = searchInventory().find(function (d) { return dealKey(d) === dealKey(task.local) && !analyzeAdult(d.item); });
        if (!task.local) task.reason = "no_price";
      } else {
        task.items = analyzeItems(task.items, task.asin);
        if (!task.items.length) task.reason = "no_price";
      }
      // A later publish may end or replace a related deal, too.
      var live = searchInventory();
      task.related = task.related.map(function (d) { return live.find(function (current) {
        return dealKey(current) === dealKey(d) && !analyzeAdult(current.item);
      }); }).filter(Boolean);
    }
    var content = document.getElementById("content"), html;
    document.getElementById("clearsearch").hidden = false;
    document.getElementById("searchcount").textContent = "";
    document.getElementById("search-amazon-result").hidden = true;
    if (!task.finished) {
      var labels = [task.asin || task.kind !== "query" ? "Reading the link" : "Reading your search",
        task.local ? "Using our verified deal" : "Checking today's Amazon price",
        task.total == null ? "Comparing verified deals" : "Comparing with " + task.total + " verified deals"];
      html = '<div class="analyze-progress" role="status" aria-live="polite"><h2>Checking your product</h2><ol class="analyze-steps">' +
        labels.map(function (label, i) { return '<li data-state="' + task.steps[i] + '">' + label + '</li>'; }).join("") + '</ol></div>';
    } else if (task.reason) {
      html = '<div class="analyze-error" role="status"><h2>' + esc(analyzeReason(task.reason)) + '</h2>' +
        (task.kind === "other" ? '<p>Type a product name to search our verified deals.</p>' : '') + analyzeFallback(task) + '</div>';
    } else {
      html = '<h2>' + (task.local ? "Your verified deal" : task.asin ? "Live Amazon price" : "Live Amazon prices") + '</h2>' +
        (task.local ? '<div class="analyze-lead">' + cardHtml(task.local) + '</div>' :
          '<div' + (task.asin ? ' class="analyze-lead"' : '') + '><div id="amazon-results" data-clarity-mask="true"></div></div>') +
        (task.related.length ? '<div class="analyze-related"><h2>Related verified deals</h2><div class="grid">' +
          task.related.map(cardHtml).join("") + '</div></div>' : '');
    }
    content.innerHTML = '<section class="search-analysis" data-clarity-mask="true" aria-busy="' + !task.finished + '">' + html + '</section>';
    if (task.finished && !task.reason && !task.local) {
      amazonResultsQuery = task.key;
      amazonResultsPaint(task.key, task.items);
    }
    return true;
  }
  function analyzePause(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }
  async function analyzeInventory(task) {
    while (!loadedOnce && analyzeCurrent(task) && Date.now() - task.started < 6000) await analyzePause(50);
    if (!loadedOnce) throw new Error("unavailable");
    return searchInventory();
  }
  function analyzeDealAsin(d) {
    return analyzeInput(d.url).asin || (/^url\|/.test(d.dedupe_key || "") && analyzeInput(d.dedupe_key.slice(4)).asin) || "";
  }
  function analyzeCompare(inventory, title, asin, local) {
    return Promise.resolve().then(function () {
      var words = Array.from(new Set(searchWords(title).filter(function (w) {
        return w.length >= 3 && !/^(?:the|and|for|with|from|pack|new|set)$/.test(w);
      })));
      return inventory.filter(function (d) {
        return d !== local && !analyzeAdult(d.item) && (!asin || analyzeDealAsin(d) !== asin);
      }).map(function (d) {
        var parts = searchWords(d.item);
        return {deal:d, score:words.filter(function (w) { return parts.indexOf(w) !== -1; }).length};
      }).filter(function (d) { return d.score; }).sort(function (a, b) { return b.score - a.score; })
        .slice(0, 6).map(function (d) { return d.deal; });
    });
  }
  function analyzeItems(items, asin) {
    return (Array.isArray(items) ? items : []).slice(0, 10).filter(function (d) {
      return d && /^[A-Z0-9]{10}$/.test(d.asin) && (!asin || d.asin === asin) &&
        typeof d.title === "string" && d.title.trim() && !analyzeAdult(d.title) &&
        Number.isSafeInteger(d.price_cents) && d.price_cents > 0 &&
        Number.isFinite(Date.parse(d.as_of)) && Date.parse(d.as_of) <= Date.now() && Date.now() - Date.parse(d.as_of) <= 86400000;
    }).slice(0, asin ? 1 : 10);
  }
  async function analyzeLookup(task) {
    if (analyzeSeen.has(task.key)) return analyzeSeen.get(task.key);
    var query = task.asin ? "" : decodeURIComponent(task.key.slice(2)), cached = amazonResultsSeen.get(query);
    if (cached && cached.length) return {items:cached};
    for (var retry = 0; retry < 3; retry++) {
      if (!analyzeCurrent(task)) return {items:[]};
      var pending = retry === 0 && task.pending;
      var controller = pending ? task.controller : new AbortController(), timer, data;
      task.controller = controller;
      try {
        data = await Promise.race([
          pending || fetch("/api/search?" + task.key, {signal:controller.signal, cache:"no-store", credentials:"omit", referrerPolicy:"no-referrer"})
            .then(function (response) { return response.json(); }),
          new Promise(function (_, reject) { timer = setTimeout(function () { controller.abort(); reject(new Error("timeout")); }, 6000); })
        ]);
        if (!data || typeof data !== "object") data = {items:[], reason:"unavailable"};
      } catch (error) { data = {items:[], reason:error.message === "timeout" ? "timeout" : "unavailable"}; }
      finally { clearTimeout(timer); }
      if (!analyzeCurrent(task)) return {items:[]};
      if ((data.reason === "rate_limited" || data.reason === "timeout") && retry < 2) { await analyzePause(1500); continue; }
      if (analyzeSeen.size >= 50) analyzeSeen.delete(analyzeSeen.keys().next().value);
      analyzeSeen.set(task.key, data);
      return data;
    }
  }
  async function analyzeSubmit() {
    clearTimeout(searchTimer);
    var input = analyzeInput(document.getElementById("q").value), key = analyzeKey(input);
    if (analyzeTask && analyzeCurrent(analyzeTask)) return;
    analyzeCancel();
    if (input.kind === "query" && !searchAmazonQuery()) { updateSearch(); return; }
    // Adopt an in-flight text lookup instead of aborting its paid reservation.
    var pending = input.kind === "query" && amazonResultsRequest &&
      key === "q=" + encodeURIComponent(amazonResultsQuery) ? analyzeQueryPromise : null;
    var controller = pending && amazonResultsRequest;
    if (pending) clearTimeout(amazonResultsTimer); else amazonResultsCancel();
    var task = {key:key, kind:input.kind, asin:input.asin || "", started:Date.now(),
      steps:["working", "pending", "pending"], items:[], related:[], finished:false, pending:pending, controller:controller};
    analyzeTask = task;
    track("search_analyze", {link:input.kind !== "query"});
    if (task.asin) track("paste_amazon_link", {link:true});
    analyzeRender();
    try {
      await Promise.resolve(); // Parsing has finished; no URL is retained in the task.
      if (!["amazon", "query"].includes(input.kind)) throw new Error(input.kind);
      task.steps[0] = "done"; analyzeRender();
      var inventory = await analyzeInventory(task);
      if (!analyzeCurrent(task)) return;
      task.total = inventory.length;
      var candidates = inventory.filter(function (d) { return !analyzeAdult(d.item); });
      task.local = task.asin ? candidates.find(function (d) { return analyzeDealAsin(d) === task.asin; }) :
        filtered(candidates)[0];
      task.steps[1] = "working"; analyzeRender();
      if (analyzeAdult(analyzeSearchText()) || (task.asin && inventory.some(function (d) {
        return analyzeDealAsin(d) === task.asin && analyzeAdult(d.item);
      }))) throw new Error("excluded");
      if (!task.local) {
        if (!amazonResultsEnabled() || (typeof wpAndroidLimited === "function" && wpAndroidLimited())) throw new Error("disabled");
        var data = await analyzeLookup(task);
        if (!analyzeCurrent(task)) return;
        if (data.reason) throw new Error(data.reason);
        task.items = analyzeItems(data.items, task.asin);
        if (!task.items.length) throw new Error((Array.isArray(data.items) ? data.items : []).some(function (d) {
          return d && typeof d.title === "string" && analyzeAdult(d.title);
        }) ? "excluded" : "no_price");
      }
      inventory = searchInventory(); task.total = inventory.length;
      task.steps[1] = "done"; task.steps[2] = "working"; analyzeRender();
      task.related = await analyzeCompare(inventory, task.local ? task.local.item : task.items[0].title, task.asin, task.local);
      if (!analyzeCurrent(task)) return;
      task.steps[2] = "done"; analyzeRender();
    } catch (error) {
      task.reason = error.message;
      task.steps = task.steps.map(function (step) { return step === "working" ? "failed" : step; });
      analyzeRender();
    }
    await analyzePause(Math.max(0, 900 - (Date.now() - task.started)));
    if (!analyzeCurrent(task)) return;
    task.finished = true; analyzeRender();
    track("analyze_result", {count:task.reason ? 0 : (task.local ? 1 : task.items.length) + task.related.length,
      live:!task.reason && !task.local, matched:!!task.local && !task.reason});
  }
  var analyzeButton = document.getElementById("analyze-search"), analyzeBox = document.getElementById("q");
  if (analyzeButton) analyzeButton.addEventListener("click", analyzeSubmit);
  if (analyzeBox) analyzeBox.addEventListener("input", function () {
    if (analyzeTask && !analyzeCurrent(analyzeTask)) analyzeCancel();
  });
  function analyzeFallbackClick(event) {
    if ((event.type === "auxclick" ? event.button !== 1 : event.button !== 0) ||
        !event.target.closest || !event.target.closest("[data-analyze-fallback]")) return;
    event.preventDefault();
    var task = analyzeTask;
    if (task) window.open("https://www.amazon.com/s?k=" + encodeURIComponent(task.asin || analyzeSearchText()) +
      "&tag=" + encodeURIComponent(amazonResultsSettings.tag), "_blank", "noopener");
    track("search_amazon");
  }
  document.addEventListener("click", analyzeFallbackClick);
  document.addEventListener("auxclick", analyzeFallbackClick);
  var analyzeHint = document.getElementById("search-hint"), analyzeVisited = false;
  try { analyzeVisited = localStorage.getItem("wp.analyzeHint") === "1"; localStorage.setItem("wp.analyzeHint", "1"); } catch (_) {}
  if (analyzeHint && analyzeBox) {
    analyzeHint.hidden = analyzeVisited;
    analyzeBox.addEventListener("focus", function () { analyzeHint.hidden = false; });
    analyzeBox.addEventListener("blur", function () { if (analyzeVisited) analyzeHint.hidden = true; });
  }
