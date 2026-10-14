  // Only fixed, public selections go in the URL. Private input and anchors
  // stay in this tab; history contains an opaque lookup id, never input.
  var PRICE_PRESETS = {{PRICE_PRESETS}};
  var BROWSE_KEY = "wp.browse", browseRows = [], browseSerial = 0;
  var browseFilters = {preset: "", max: "", newOnly: false, plainOnly: false};
  var browseMemory = {version: 1, last: "", entries: {}}, browseId = "", browseSignature = "";
  var browsePending = null, browseReturning = false, browseScrollTimer;
  var BROWSE_SORTS = {top: "top:-1", fresh: "fresh:-1", pct: "pct:-1", abs: "abs:-1", price: "price:1", age: "age:1"};
  var filterForm = document.getElementById("filterform"), filterSheet = document.getElementById("filtersheet");
  function presetValue(v) { return PRICE_PRESETS.some(function (n) { return String(n) === v; }) ? v : ""; }
  function maxValue(v) {
    return typeof v === "string" && v.length <= 24 && /^(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(v.trim()) &&
      Number.isFinite(Number(v)) && Number(v) >= 0 ? v.trim() : "";
  }
  function eligibilityActive() { return !!(browseFilters.preset || browseFilters.max || browseFilters.newOnly || browseFilters.plainOnly); }
  function matchesEligibility(d, skip) {
    skip = skip || [];
    var ceiling = browseFilters.max || browseFilters.preset;
    if (ceiling && skip.indexOf("price") === -1 && !(typeof d.final_price === "number" && Number.isFinite(d.final_price) && d.final_price <= Number(ceiling))) return false;
    if (browseFilters.plainOnly && skip.indexOf("steps") === -1 && d.needs_steps !== false) return false;
    if (browseFilters.newOnly && skip.indexOf("new") === -1) {
      if (!Array.isArray(d.conditions)) return false;
      if (d.conditions.some(function (c) {
        if (typeof c !== "string") return true;
        var label = c.trim().toLowerCase().replace(/[\W_]+/g, "_").replace(/^_|_$/g, "")
          .replace(/^(?:item_)?condition_/, "");
        return /(?:^|_)(?:refurb(?:ished)?|reconditioned|renewed|unknown|unspecified)(?:_|$)/.test(label) ||
          /^(?:used|open_?box|pre_?owned|amazon_warehouse|amazon_resale|like_new|second_?hand|previously_owned|pre_?loved)/.test(label) ||
          label === "warehouse" || label === "resale";
      })) return false;
    }
    return true;
  }
  function paintEligibility() {
    document.getElementById("pricepreset").value = browseFilters.preset;
    document.getElementById("maxprice").value = browseFilters.max;
    document.getElementById("newonly").checked = browseFilters.newOnly;
    document.getElementById("plainonly").checked = browseFilters.plainOnly;
    var count = Number(!!(browseFilters.preset || browseFilters.max)) + Number(browseFilters.newOnly) + Number(browseFilters.plainOnly);
    document.getElementById("filtertoggle").textContent = "Filters" + (count ? " (" + count + ")" : "");
  }
  function clearEligibility() {
    browseFilters = {preset: "", max: "", newOnly: false, plainOnly: false};
    paintEligibility();
  }
  function categoryValue(i) { return i === 1 ? "all" : (CATGROUPS[i] || [])[2] || ""; }
  function categoryIndex(v) {
    if (v === "all") return 1;
    var i = CATGROUPS.findIndex(function (g) { return g[2] && g[2] === v; });
    return i < 0 ? 0 : i;
  }
  function knownStore(v) {
    return typeof v === "string" && Array.prototype.some.call(document.getElementById("storesel").options, function (o) { return o.value === v; }) ? v : "";
  }
  function browseURL() {
    var p = new URLSearchParams(), store = knownStore(document.getElementById("storesel").value);
    if (store) p.set("store", store);
    if (state.catTab) p.set("category", categoryValue(state.catTab));
    if (state.sortKey !== "top" || store === "amazon") p.set("sort", state.sortKey);
    if (browseFilters.preset) p.set("price", browseFilters.preset);
    if (browseFilters.newOnly) p.set("new", "1");
    if (browseFilters.plainOnly) p.set("steps", "none");
    return "/" + (p.toString() ? "?" + p.toString() : "");
  }
  function actionSignature() {
    return JSON.stringify([browseURL(), browseFilters.max, document.getElementById("minpct").value, document.getElementById("q").value]);
  }
  function readBrowseMemory() {
    try {
      var raw = sessionStorage.getItem(BROWSE_KEY);
      if (!raw || raw.length > 180000) return;
      var record = JSON.parse(raw);
      if (record && record.version === 1 && record.entries && typeof record.entries === "object" && !Array.isArray(record.entries)) {
        var entries = {};
        Object.keys(record.entries).slice(-20).forEach(function (key) {
          if (/^[a-z0-9-]{1,60}$/.test(key)) entries[key] = record.entries[key];
        });
        browseMemory = {version: 1, last: typeof record.last === "string" ? record.last : "", entries: entries};
      }
    } catch (e) { /* denied or corrupt storage: the current tab still works */ }
  }
  function historyBrowseId() {
    try { return history.state && history.state.wpBrowse || ""; } catch (e) { return ""; }
  }
  function rememberBrowse(push) {
    if (!loadedOnce) return;
    if (!browseId || push) browseId = Date.now().toString(36) + "-" + (++browseSerial);
    var cards = Array.prototype.slice.call(cardEls()), anchor = null;
    var visible = cards.findIndex(function (el) {
      var r = el.getBoundingClientRect();
      return r.bottom > 0 && r.top < window.innerHeight;
    });
    if (visible >= 0) anchor = {key: cards[visible].getAttribute("data-key"), index: visible,
                               offset: cards[visible].getBoundingClientRect().top};
    var snapshot = {url: browseURL(), query: document.getElementById("q").value.slice(0, 500),
      store: document.getElementById("storesel").value, category: categoryValue(state.catTab), sort: state.sortKey,
      filters: Object.assign({}, browseFilters), minimum: document.getElementById("minpct").value,
      shown: state.shown, seed: state.seed, anchor: anchor, y: window.scrollY || 0};
<!--saved-->    snapshot.savedView = savedOn() && savedDeals.view;
<!--/saved-->
    browseMemory.entries[browseId] = snapshot;
    browseMemory.last = browseId;
    Object.keys(browseMemory.entries).slice(0, -20).forEach(function (id) { delete browseMemory.entries[id]; });
    try { sessionStorage.setItem(BROWSE_KEY, JSON.stringify(browseMemory)); } catch (e) { /* tab-only fallback */ }
    try {
      if ("scrollRestoration" in history) history.scrollRestoration = "manual";
      // An OAuth return (?code=...&state=...) keeps its address: the
      // operator copies the code from the address bar (Pinterest connect).
      if (/[?&]code=[^&]/.test(location.search || "")) return;
      if (push || historyBrowseId() !== browseId || (location.pathname || "/") + (location.search || "") !== snapshot.url) {
        history[push ? "pushState" : "replaceState"](Object.assign({}, history.state, {wpBrowse: browseId}), "", snapshot.url + (location.hash || ""));
      }
    } catch (e) { /* navigation and denied storage are never required for filters */ }
  }
  function applyBrowseSnapshot(snapshot, useURL) {
    clearTimeout(searchTimer);
    var s = snapshot && typeof snapshot === "object" ? snapshot : {}, f = s.filters || {};
    document.getElementById("q").value = typeof s.query === "string" ? s.query.slice(0, 500) : "";
    document.getElementById("storesel").value = knownStore(s.store);
    var minimum = Number(s.minimum);
    document.getElementById("minpct").value = Number.isFinite(minimum) && minimum >= 0 && minimum <= 100 ? String(minimum) : "0";
    state.catTab = categoryIndex(s.category);
    var sort = Object.prototype.hasOwnProperty.call(BROWSE_SORTS, s.sort) ? s.sort : state.sortKey;
    sortsel.value = BROWSE_SORTS[sort] || BROWSE_SORTS.top;
    applySort(sortsel.value);
    browseFilters = {preset: presetValue(f.preset), max: maxValue(f.max), newOnly: f.newOnly === true, plainOnly: f.plainOnly === true};
    state.shown = Number.isSafeInteger(s.shown) && s.shown >= PAGE_CARDS ? Math.min(s.shown, Math.max(PAGE_CARDS, state.deals.length)) : PAGE_CARDS;
    if (Number.isSafeInteger(s.seed)) state.seed = s.seed;
    if (useURL) {
      var p = new URLSearchParams(location.search), names = ["store", "category", "sort", "price", "new", "steps"];
      if (names.some(function (name) { return p.has(name); })) {
        // A shared URL describes a complete public selection. A different
        // URL must not inherit a private query or custom ceiling.
        document.getElementById("storesel").value = knownStore(p.getAll("store").length === 1 ? p.get("store") : "");
        state.catTab = categoryIndex(p.getAll("category").length === 1 ? p.get("category") : "");
        sort = p.getAll("sort").length === 1 ? p.get("sort") : "top";
        sortsel.value = Object.prototype.hasOwnProperty.call(BROWSE_SORTS, sort) ? BROWSE_SORTS[sort] : BROWSE_SORTS.top;
        applySort(sortsel.value);
        browseFilters = {preset: presetValue(p.getAll("price").length === 1 ? p.get("price") : ""), max: "",
          newOnly: p.getAll("new").length === 1 && p.get("new") === "1",
          plainOnly: p.getAll("steps").length === 1 && p.get("steps") === "none"};
        if (s.url === browseURL() || s.url === "/" + (location.search || "")) browseFilters.max = maxValue(f.max);
        else {
          document.getElementById("q").value = "";
          document.getElementById("minpct").value = "0";
          state.shown = PAGE_CARDS;
        }
      }
    }
<!--saved-->    savedDeals.view = savedOn() && s.savedView === true && (!useURL || !s.url || s.url === browseURL() || s.url === "/" + (location.search || ""));
    renderSavedControls();
<!--/saved-->    paintEligibility();
    appliedSearch = searchText();
    browseSignature = actionSignature();
  }
  function finishBrowseAction(method, push) {
    if (!loadedOnce || document.getElementById("filterfields").disabled) return;
    var signature = actionSignature();
    if (signature === browseSignature) { resetWall(); rememberBrowse(false); return; }
    browseSignature = signature;
    resetWall();
    rememberBrowse(push);
    // Sorting changes the order, not the eligibility of an offer.
    if (method === "sort") return;
    track("filter_apply", {method: method});
    if (!filtered().concat(activeSpotlight()).some(function (d) { return d.status !== "dead"; })) {
      track("filter_empty", {method: method});
      if (searchText()) track("search_zero", {method: "filter"});
    }
  }
  function navigationMethod() {
    try {
      var n = performance.getEntriesByType("navigation")[0];
      return n && n.type === "back_forward" ? "back" : n && n.type === "reload" ? "reload" : "";
    } catch (e) { return ""; }
  }
  function initBrowse() {
    readBrowseMemory();
    browseId = historyBrowseId();
    var snapshot = browseMemory.entries[browseId] || browseMemory.entries[browseMemory.last];
    document.getElementById("filterfields").disabled = false;
    document.getElementById("filtertoggle").disabled = false;
    applyBrowseSnapshot(snapshot, true);
    var method = navigationMethod();
    if (snapshot && method && (snapshot.url === browseURL() || snapshot.url === "/" + (location.search || ""))) {
      browsePending = {snapshot: snapshot, method: method};
    }
    rememberBrowse(false);
  }
  function restoreBrowsePosition(snapshot, method) {
    if (!loadedOnce || !snapshot) return;
    var a = snapshot.anchor, key = a && typeof a.key === "string" && a.key.length <= 256 ? a.key : "";
    var index = browseRows.findIndex(function (d) { return dealKey(d) === key; });
    if (index >= state.shown) {
      state.shown = Math.ceil((index + 1) / PAGE_CARDS) * PAGE_CARDS;
      render();
    }
    // The rebuilt wall contains only the current publish. If the old key
    // disappeared or fails its saved filters, choose the nearest position.
    var cards = Array.prototype.slice.call(cardEls());
    var card = cards.find(function (el) { return el.getAttribute("data-key") === key; });
    if (!card && a && Number.isSafeInteger(a.index) && a.index >= 0 && cards.length) card = cards[Math.min(a.index, cards.length - 1)];
    var y = typeof snapshot.y === "number" && Number.isFinite(snapshot.y) && snapshot.y >= 0 ? Math.min(snapshot.y, 10000000) : 0;
    var offset = a && typeof a.offset === "number" && Number.isFinite(a.offset) && Math.abs(a.offset) <= window.innerHeight ? a.offset : 0;
    if (card) y = (window.scrollY || 0) + card.getBoundingClientRect().top - offset;
    window.scrollTo({top: Math.max(0, y), behavior: "auto"});
    if (method) track("back_restore", {method: method});
    rememberBrowse(false);
  }
  function restoreInitialBrowse() {
    var pending = browsePending;
    browsePending = null;
    if (pending) requestAnimationFrame(function () { restoreBrowsePosition(pending.snapshot, pending.method); });
  }
  function restoreBrowseOnReturn() {
    if (browseReturning) return;
    var snapshot = browseMemory.entries[browseId];
    browseReturning = true;
    return Promise.resolve(refreshOnReturn()).then(function () {
      if (snapshot) restoreBrowsePosition(snapshot, "back");
    }).then(function () { browseReturning = false; });
  }
  window.addEventListener("popstate", function () {
    if (!loadedOnce) return;
    var id = historyBrowseId();
    if (id === browseId) return;
    browseId = id;
    var snapshot = browseMemory.entries[id];
    closeFilters();
    applyBrowseSnapshot(snapshot, true);
    renderTabs();
    render();
    restoreBrowsePosition(snapshot, "history");
    if (!snapshot) rememberBrowse(false);
  });
  window.addEventListener("scroll", function () {
    clearTimeout(browseScrollTimer);
    browseScrollTimer = setTimeout(function () { rememberBrowse(false); }, 200);
  }, {passive: true});
  window.addEventListener("pagehide", function () { clearTimeout(browseScrollTimer); rememberBrowse(false); });
  function closeFilters() {
    if (filterSheet.open) filterSheet.close();
  }
  document.getElementById("filtertoggle").addEventListener("click", function () {
    if (!loadedOnce || this.disabled || typeof filterSheet.showModal !== "function") return;
    paintEligibility();
    filterSheet.appendChild(filterForm);
    filterSheet.showModal();
    this.setAttribute("aria-expanded", "true");
    document.getElementById("pricepreset").focus();
  });
  filterSheet.addEventListener("close", function () {
    document.getElementById("desktopfilters").appendChild(filterForm);
    paintEligibility();
    document.getElementById("filtertoggle").setAttribute("aria-expanded", "false");
    document.getElementById(window.innerWidth > 640 ? "pricepreset" : "filtertoggle").focus({preventScroll: true});
  });
  filterSheet.addEventListener("click", function (e) {
    if (e.target !== filterSheet) return;
    var r = filterSheet.getBoundingClientRect();
    if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) closeFilters();
  });
  document.getElementById("filterclose").addEventListener("click", closeFilters);
  window.addEventListener("resize", function () { if (window.innerWidth > 640) closeFilters(); });
  document.getElementById("pricepreset").addEventListener("change", function () { if (this.value) document.getElementById("maxprice").value = ""; });
  document.getElementById("maxprice").addEventListener("input", function () { if (this.value) document.getElementById("pricepreset").value = ""; });
  filterForm.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!loadedOnce || document.getElementById("filterfields").disabled || !filterForm.reportValidity()) return;
    var method = filterSheet.open ? "sheet" : "desktop";
    browseFilters = {preset: presetValue(document.getElementById("pricepreset").value), max: maxValue(document.getElementById("maxprice").value),
      newOnly: document.getElementById("newonly").checked, plainOnly: document.getElementById("plainonly").checked};
    paintEligibility();
    finishBrowseAction(method, true);
    closeFilters();
  });
  document.getElementById("filterclear").addEventListener("click", function () {
    if (!loadedOnce || document.getElementById("filterfields").disabled) return;
    document.getElementById("q").value = "";
    appliedSearch = "";
    document.getElementById("storesel").value = "";
    document.getElementById("minpct").value = "0";
    state.catTab = 0;
    clearEligibility();
    renderTabs();
    finishBrowseAction("clear", true);
    closeFilters();
  });
