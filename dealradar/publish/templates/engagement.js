  // Browser-only visit state. Never included in track() or an API request.
  var engagement = {started: false, storage: false, visitAt: 0, newKeys: {}, newCount: 0,
                    week: "", finds: [], streak: 1};
  function engagementOn(name) { return (state.meta.engagement || {})[name] === true; }
  function localDateKey(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function localDay(d) {
    // Compare calendar dates, not 24-hour spans (daylight saving changes).
    return Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86400000;
  }
  function mondayKey(now) {
    var d = new Date(now);
    d.setDate(d.getDate() - (d.getDay() + 6) % 7);
    return localDateKey(d);
  }
  function readEngagement(key) {
    var value = localStorage.getItem(key);
    try { return JSON.parse(value || "null"); } catch (e) { return null; }
  }
  function disableLocalEngagement() {
    engagement.storage = false;
    engagement.newKeys = {};
    engagement.newCount = 0;
    engagement.finds = [];
  }
  function refreshFinds(now) {
    var week = mondayKey(now), saved = readEngagement("wp.weeklyFinds");
    engagement.week = week;
    engagement.finds = saved && saved.week === week && Array.isArray(saved.finds)
      ? saved.finds.filter(function (f, i, rows) {
          return f && typeof f.key === "string" && f.key && Number.isSafeInteger(f.cents) && f.cents > 0 &&
            rows.findIndex(function (x) { return x && x.key === f.key; }) === i;
        }) : [];
  }
  function visitToday(now) {
    var today = localDay(new Date(now)), saved = readEngagement("wp.visitStreak");
    var valid = saved && Number.isSafeInteger(saved.day) && Number.isSafeInteger(saved.count) && saved.count > 0;
    engagement.streak = valid && saved.day === today ? saved.count
      : valid && saved.day === today - 1 ? saved.count + 1 : 1;
    localStorage.setItem("wp.visitStreak", JSON.stringify({day: today, count: engagement.streak}));
  }
  function initEngagement() {
    if (engagement.started) return;
    engagement.started = true;
    engagement.visitAt = Date.now();
    if (!["new_since_visit", "weekly_finds", "visit_streak"].some(engagementOn)) return;
    try {
      // A read-only or blocked store means the local features stay silent.
      localStorage.setItem("wp.engagement.check", "1");
      localStorage.removeItem("wp.engagement.check");
      engagement.storage = true;
      if (engagementOn("new_since_visit")) {
        var last = Number(localStorage.getItem("wp.lastVisit"));
        if (last > 0 && last <= engagement.visitAt) state.deals.forEach(function (d) {
          var seen = Date.parse(d.first_seen_at || ""), key = dealKey(d);
          if (key && d.status !== "dead" && seen > last && seen <= engagement.visitAt && !engagement.newKeys[key]) {
            engagement.newKeys[key] = true;
            engagement.newCount++;
          }
        });
      }
      if (engagementOn("weekly_finds")) refreshFinds(engagement.visitAt);
      if (engagementOn("visit_streak")) visitToday(engagement.visitAt);
    } catch (e) { disableLocalEngagement(); }
  }
  function newSinceVisit(d) {
    return engagementOn("new_since_visit") && engagement.storage && d.status !== "dead" && !!engagement.newKeys[dealKey(d)];
  }
  function renderEngagement() {
    var tagline = document.getElementById("tagline");
    tagline.textContent = tagline.textContent.replace(/ · \d+ new since your last visit$/, "");
    if (engagementOn("new_since_visit") && engagement.storage && engagement.newCount > 0) {
      tagline.textContent += " · " + engagement.newCount + " new since your last visit";
    }
    var surprise = document.getElementById("surprise");
    surprise.hidden = !engagementOn("surprise_me");
    surprise.disabled = !surpriseRows().length;
  }
  function engagementLineText() {
    var cents = engagement.finds.reduce(function (sum, f) { return sum + f.cents; }, 0);
    return {
      finds: engagementOn("weekly_finds") && engagement.storage && cents >= 1000
        ? "You've found $" + (cents / 100).toLocaleString("en-US", {
            minimumFractionDigits: 2, maximumFractionDigits: 2
          }) + " in savings this week" : "",
      streak: engagementOn("visit_streak") && engagement.storage && engagement.streak >= 2
        ? "🔥 " + engagement.streak + "-day streak" : ""
    };
  }
  function engagementLineHtml(blankFinds) {
    var text = engagementLineText();
    if (!text.finds && !text.streak) return "";
    return '<p id="engagementline" class="engagement-line">' +
      '<span id="visitstreak"' + (text.streak ? "" : " hidden") + '>' + esc(text.streak) + '</span>' +
      '<span id="engagementsep" aria-hidden="true"' + (text.streak && text.finds ? "" : " hidden") + '> · </span>' +
      '<span id="weeklyfinds">' + (blankFinds ? "" : esc(text.finds)) + '</span></p>';
  }
  function updateEngagementLine(announceFinds) {
    var text = engagementLineText(), line = document.getElementById("engagementline");
    if (!text.finds && !text.streak) {
      if (line) line.remove();
      return;
    }
    if (!line) document.getElementById("content").insertAdjacentHTML("afterbegin", engagementLineHtml(true));
    var streak = document.getElementById("visitstreak"), finds = document.getElementById("weeklyfinds");
    streak.hidden = !text.streak;
    streak.textContent = text.streak;
    document.getElementById("engagementsep").hidden = !(text.streak && text.finds);
    finds.setAttribute("aria-live", announceFinds ? "polite" : "off");
    finds.textContent = text.finds;
  }
  function recordFind(d) {
    if (!engagementOn("weekly_finds") || !engagement.storage || d.status === "dead" || isLocked(d)) return;
    var key = dealKey(d), amount = Number(d.abs_savings), cents = Math.round(amount * 100);
    if (!key || !Number.isFinite(amount) || !Number.isSafeInteger(cents) || cents <= 0) return;
    try {
      refreshFinds(Date.now()); // Monday rollover and changes from another tab.
      if (!engagement.finds.some(function (f) { return f.key === key; })) {
        engagement.finds.push({key: key, cents: cents});
        localStorage.setItem("wp.weeklyFinds", JSON.stringify({week: engagement.week, finds: engagement.finds}));
      }
    } catch (e) { disableLocalEngagement(); }
    renderEngagement();
    updateEngagementLine(true);
  }
  function hotSignals(d) {
    if (!engagementOn("hot_meter") || d.status === "dead" || isLocked(d)) return [];
    var signals = [], pct = Number(d.pct_at_checkout), seen = Date.parse(d.first_seen_at || "");
    if (Number.isFinite(pct) && pct >= 60) signals.push(pct + "% off");
    if (d.badge === "corroborated") {
      var sources = new Set((d.credits || []).map(function (c) { return c.name; }).filter(Boolean)).size;
      signals.push("confirmed by " + (sources >= 2 ? sources : "2+") + " sources");
    }
    var age = Date.now() - seen;
    if (age >= 0 && age < 2 * 60 * 60 * 1000) signals.push("first seen less than 2 hours ago");
    return signals;
  }
  function hotHtml(d) {
    var signals = hotSignals(d);
    if (signals.length < 2) return "";
    var label = esc("Hot: " + signals.join(", "));
    return '<span class="hotmeter" role="img" title="' + label + '" aria-label="' + label + '">' +
      '<span aria-hidden="true">' + "🔥".repeat(signals.length) + "</span></span>";
  }
  function surpriseRows() {
    // Match the wall's floor split as well as its current filters and sort.
    var rows = ordered(filtered()).filter(function (d) { return d.status !== "dead" && !isLocked(d); });
    var floor = num((state.meta.floors || {}).min_pct);
    if (floor != null) rows = rows.filter(function (d) { return num(d.pct_at_checkout) >= floor; })
      .concat(rows.filter(function (d) { return num(d.pct_at_checkout) < floor; }));
    return rows.slice(0, 50);
  }
  document.getElementById("surprise").addEventListener("click", function () {
    if (!engagementOn("surprise_me")) return;
    var rows = surpriseRows();
    if (!rows.length) return;
    var d = rows[Math.floor(Math.random() * rows.length)], key = dealKey(d);
    var pos = rows.indexOf(d);
    if (pos >= state.shown) {
      state.shown = Math.ceil((pos + 1) / PAGE_CARDS) * PAGE_CARDS;
      render();
    }
    var card = Array.prototype.find.call(cardEls(), function (el) { return el.getAttribute("data-key") === key; });
    if (!card) return;
    track("surprise_click");
    card.scrollIntoView({behavior: reduceMotion() ? "auto" : "smooth", block: "center"});
    // Focus also takes keyboard and screen-reader visitors to the find.
    card.setAttribute("tabindex", "-1");
    card.focus({preventScroll: true});
    card.classList.add("flash");
    setTimeout(function () { card.classList.remove("flash"); }, 1600);
  });
  window.addEventListener("pagehide", function () {
    if (!engagementOn("new_since_visit") || !engagement.storage) return;
    try { localStorage.setItem("wp.lastVisit", String(Date.now())); }
    catch (e) { disableLocalEngagement(); }
  });
  // A tab can stay open across midnight/Monday, or return from the bfcache.
  function refreshLocalDay() {
    if (!engagement.storage) return;
    try {
      if (engagementOn("weekly_finds")) refreshFinds(Date.now());
      if (engagementOn("visit_streak")) visitToday(Date.now());
    } catch (e) { disableLocalEngagement(); }
    renderEngagement();
    updateEngagementLine(false);
  }
  window.addEventListener("pageshow", refreshLocalDay);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) refreshLocalDay(); });
/* saved-deals */
  // Saved prices are comparison baselines, never current offers. Offers
  // always come from the last successfully loaded public payload.
  var SAVED_KEY = "wp.savedDeals", SAVED_VERSION = 2;
  var savedDeals = {started: false, persistent: true, entries: [], view: false, publicDeals: [],
                    matches: new Map(), pending: new Set(), toastPending: false};
  function savedOn() {
    return (state.meta.support || {}).enabled === true && engagementOn("saved_deals");
  }
  function savedLimit() {
    var limit = state.meta.saved_deals_limit;
    return Number.isSafeInteger(limit) ? Math.max(1, Math.min(200, limit)) : 100;
  }
  function savedText(value, max, empty) {
    return typeof value === "string" && value.length <= max && (empty || value.trim()) &&
      !/[\u0000-\u001f\u007f\u202a-\u202e\u2066-\u2069]/.test(value);
  }
  function savedPath(path) {
    // A root-relative path with no query, fragment, escapes or dot segments.
    return typeof path === "string" && /^\/deal\/[a-z0-9][a-z0-9-]{0,100}\/$/.test(path) ? path : "";
  }
  function watchPrice(price) {
    return typeof price === "number" && Number.isFinite(price) && price >= 0 &&
      Number(price.toFixed(2)) === price ? price : null;
  }
  function watchCondition(conditions) {
    if (!Array.isArray(conditions) || conditions.length > 32 || !conditions.every(function (c) {
      return typeof c === "string" && /^[a-z][a-z0-9_]{0,39}$/.test(c);
    })) return null;
    return Array.from(new Set(conditions)).sort();
  }
  function savedEntry(entry) {
    if (!entry || !savedText(entry.key, 256, false) ||
        !savedText(entry.label, 200, false) || !savedText(entry.store, 80, true)) return null;
    var clean = {key: entry.key, label: entry.label, store: entry.store};
    var path = savedPath(entry.path);
    if (path) clean.path = path;
    clean.final_price = watchPrice(entry.final_price);
    clean.condition = watchCondition(entry.condition);
    if (watchPrice(entry.target_price) !== null) clean.target_price = entry.target_price;
    clean.matched_prices = Array.isArray(entry.matched_prices) ? Array.from(new Set(
      entry.matched_prices.slice(0, 200).filter(function (p) { return watchPrice(p) !== null; })
    )) : [];
    return clean;
  }
  function parseSaved(raw) {
    if (typeof raw !== "string" || raw.length > 2000000) return [];
    try {
      var record = JSON.parse(raw);
      if (!record || (record.version !== 1 && record.version !== SAVED_VERSION) || !Array.isArray(record.deals)) return [];
      var entries = [], seen = new Set();
      // Bound work as well as output when a record is corrupt or excessive.
      record.deals.slice(0, 200).forEach(function (entry) {
        // v1 never recorded a price or condition. Do not infer either from
        // a later visit, or retain unexpected price fields in a v1 record.
        if (record.version === 1 && entry) entry = {key: entry.key, label: entry.label, store: entry.store, path: entry.path};
        var clean = savedEntry(entry);
        if (clean && !seen.has(clean.key) && entries.length < savedLimit()) {
          entries.push(clean);
          seen.add(clean.key);
        }
      });
      return entries;
    } catch (e) { return []; }
  }
  function savedRecord() {
    return JSON.stringify({version: SAVED_VERSION, deals: savedDeals.entries});
  }
  function persistSaved() {
    if (!savedDeals.persistent) return;
    try { localStorage.setItem(SAVED_KEY, savedRecord()); }
    catch (e) { savedDeals.persistent = false; }
  }
  function readSaved() {
    if (!savedDeals.persistent) return;
    try {
      var raw = localStorage.getItem(SAVED_KEY);
      savedDeals.entries = parseSaved(raw);
      // Remove obsolete or unwanted fields rather than carrying them forward.
      if (raw !== null && raw !== savedRecord()) persistSaved();
    } catch (e) { savedDeals.persistent = false; }
  }
  function reconcileSaved(pub) {
    if (!savedOn()) return;
    savedDeals.publicDeals = pub.deals;
    savedDeals.started = true;
    readSaved(); // Also take another tab's targets and observed prices before comparing.
    savedDeals.entries = savedDeals.entries.slice(0, savedLimit());
    checkSavedWatches(true);
    renderSavedControls();
  }
  function isSaved(key) {
    return savedDeals.entries.some(function (entry) { return entry.key === key; });
  }
  function savedPublicMatch(key) {
    return savedDeals.publicDeals.find(function (d) { return d.key === key; });
  }
  function savedWatchMatch(entry, current) {
    if (!current || isLocked(current) || current.status !== "alive" || current.gone === true ||
        current.alive === false || current.key !== entry.key || entry.condition === null) return null;
    var price = watchPrice(current.final_price), condition = watchCondition(current.conditions);
    if (price === null || condition === null || JSON.stringify(condition) !== JSON.stringify(entry.condition)) return null;
    var dropped = entry.final_price !== null && price < entry.final_price;
    var target = entry.target_price != null && price <= entry.target_price;
    return dropped || target ? {price: price, dropped: dropped, target: target, method: target ? "target" : "drop"} : null;
  }
  function checkSavedWatches(announce) {
    savedDeals.matches.clear();
    if (!savedOn()) return;
    var changed = false;
    savedDeals.entries.forEach(function (entry) {
      var match = savedWatchMatch(entry, savedPublicMatch(entry.key));
      if (!match) return;
      savedDeals.matches.set(entry.key, match);
      if (!announce || entry.matched_prices.indexOf(match.price) !== -1 || entry.matched_prices.length >= 200) return;
      // Bound notification memory without forgetting and reannouncing old prices.
      entry.matched_prices.push(match.price);
      changed = true;
      savedDeals.pending.add(entry.key);
      track("watch_match", {method: match.method});
    });
    if (changed) persistSaved();
    if (!savedDeals.pending.size || savedDeals.toastPending) return;
    savedDeals.toastPending = true;
    // Run after the wall's normal drop toast, and combine simultaneous matches.
    setTimeout(function () {
      savedDeals.toastPending = false;
      var count = savedDeals.entries.filter(function (entry) {
        return savedDeals.pending.has(entry.key) && savedDeals.matches.has(entry.key);
      }).length;
      savedDeals.pending.clear();
      if (savedOn() && count) toast((count === 1 ? "A saved deal matched its price watch."
        : count + " saved deals matched their price watches.") + " Checked when you visit.");
    }, 0);
  }
  function setWatchTarget(key, target) {
    if (!savedOn() || (target !== null && watchPrice(target) === null)) return false;
    var entry = savedDeals.entries.find(function (e) { return e.key === key; });
    if (!entry || (entry.target_price == null ? null : entry.target_price) === target) return false;
    if (target !== null) {
      var current = savedPublicMatch(key), condition = current && watchCondition(current.conditions);
      if (!current || isLocked(current) || current.status !== "alive" || current.gone === true ||
          current.alive === false || watchPrice(current.final_price) === null || condition === null ||
          (entry.condition !== null && JSON.stringify(entry.condition) !== JSON.stringify(condition))) return false;
      if (entry.condition === null) entry.condition = condition;
      entry.target_price = target;
      track("watch_create", {method: "target"});
    } else delete entry.target_price;
    persistSaved();
    checkSavedWatches(false); // A target edit alone is not a fresh public price observation.
    return true;
  }
  function saveDeal(d) {
    if (!savedOn() || !d || isSaved(dealKey(d))) return false;
    var current = savedPublicMatch(dealKey(d));
    if (!current || isLocked(current) || current.status !== "alive" || current.gone === true || current.alive === false) return false;
    var entry = savedEntry({key: dealKey(current), label: String(current.item || "").slice(0, 200),
      store: String(current.store || "").slice(0, 80), path: current.deal_path,
      final_price: current.final_price, condition: current.conditions});
    if (!entry) return false;
    if (savedDeals.entries.length >= savedLimit()) {
      toast("Saved list is full (" + savedLimit() + "). Remove a deal to save another.");
      return false;
    }
    savedDeals.entries.unshift(entry); // Newest saves first; duplicate saves never reorder.
    persistSaved();
    track("deal_save", {method: "save", count: 1});
    if (entry.final_price !== null && entry.condition !== null) track("watch_create", {method: "save"});
    return true;
  }
  function removeSaved(key) {
    if (!savedOn() || !isSaved(key)) return false;
    savedDeals.entries = savedDeals.entries.filter(function (entry) { return entry.key !== key; });
    savedDeals.matches.delete(key);
    savedDeals.pending.delete(key);
    persistSaved();
    track("deal_save", {method: "remove", count: 1});
    return true;
  }
  function clearSaved() {
    if (!savedOn() || !savedDeals.entries.length) return false;
    var count = savedDeals.entries.length;
    savedDeals.entries = [];
    savedDeals.matches.clear();
    savedDeals.pending.clear();
    persistSaved();
    track("deal_save", {method: "clear", count: count});
    return true;
  }
  function savedButtonHtml(d) {
    if (!savedOn() || isLocked(d)) return "";
    var key = dealKey(d), on = isSaved(key), current = savedPublicMatch(key);
    if (!on && (!current || isLocked(current) || current.status !== "alive")) return "";
    var label = on ? "Remove saved deal: " : "Save deal: ";
    return '<button type="button" class="save-deal" data-save-key="' + esc(key) +
      '" data-clarity-mask="true" aria-pressed="' + on + '" aria-label="' + esc(label + (d.item || "deal")) + '">' +
      '<svg viewBox="0 0 32 32" aria-hidden="true" focusable="false"><circle cx="16" cy="16" r="15"/>' +
      '<path d="M16 24.2l-1.2-1.1C10.4 19.1 7.5 16.5 7.5 13.3c0-2.6 2-4.6 4.6-4.6 1.5 0 2.9.7 3.9 1.8 1-1.1 2.4-1.8 3.9-1.8 2.6 0 4.6 2 4.6 4.6 0 3.2-2.9 5.8-7.3 9.8L16 24.2z"/></svg></button>';
  }
  function renderSavedControls() {
    var button = document.getElementById("savedview");
    if (!button) return;
    var on = savedOn();
    button.hidden = !on || (!savedDeals.entries.length && !savedDeals.view);
    document.getElementById("savedtools").hidden = !on || !savedDeals.view;
    if (!on) return;
    var content = document.getElementById("content");
    if (savedDeals.view) {
      content.removeAttribute("data-clarity-unmask");
      content.setAttribute("data-clarity-mask", "true");
    } else {
      content.removeAttribute("data-clarity-mask");
      content.setAttribute("data-clarity-unmask", "true");
    }
    var matches = savedDeals.matches.size;
    button.innerHTML = "Saved (" + savedDeals.entries.length + ")" + (matches
      ? '<span class="watch-count" aria-hidden="true">' + matches + '</span>' +
        '<span class="sr-only">, ' + matches + (matches === 1 ? ' price watch match' : ' price watch matches') + '</span>' : "");
    button.setAttribute("aria-pressed", String(savedDeals.view));
    document.getElementById("savedall").hidden = !savedDeals.view;
    var clear = document.getElementById("savedclear");
    clear.hidden = !savedDeals.view;
    clear.disabled = !savedDeals.entries.length;
    var status = document.getElementById("savedstatus"), message = savedDeals.persistent
      ? "Saved on this device. Checked when you visit. Clearing site data removes your saved list."
      : "Saved in this tab only. Checked when you visit. Your browser blocked storage; this list will not persist.";
    if (status.textContent !== message) status.textContent = message;
    ["storesel", "sortsel", "minpct", "surprise", "filtertoggle", "filterfields"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.disabled = savedDeals.view || (id === "surprise" && !surpriseRows().length);
    });
  }
  function setSavedView(on) {
    if (!savedOn() || savedDeals.view === on) return;
    savedDeals.view = on;
    if (on) {
      document.getElementById("q").value = "";
      document.getElementById("clearsearch").hidden = true;
      document.getElementById("searchcount").textContent = "";
    }
    if (on) track("saved_view", {method: "button", count: savedDeals.entries.length});
    renderSavedControls();
    render();
  }
  function savedUnavailableHtml(entry, current) {
    var ended = current && !isLocked(current) && current.status === "dead" && current.gone === true;
    var label = ended ? current.item : entry.label, store = ended ? current.store : entry.store;
    return '<article class="card saved-unavailable' + (ended ? ' gone' : '') + '" data-key="' + esc(entry.key) + '">' +
      '<div class="body"><h3 class="title">' + esc(label) + '</h3><p class="meta">' + esc(store) + '</p>' +
      '<p>' + (ended ? "This deal ended." : "Not currently listed; availability unknown") + '</p>' +
      '<button type="button" class="save-deal" data-save-key="' + esc(entry.key) +
      '" data-clarity-mask="true" aria-pressed="true" aria-label="' + esc("Remove saved deal: " + label) + '">Remove</button></div></article>';
  }
  function savedViewHtml() {
    if (!savedDeals.entries.length) return '<h2 class="secth">Saved deals (0)</h2><p class="empty">Save a deal to revisit it here.</p>';
    return '<h2 class="secth">Saved deals (' + savedDeals.entries.length + ')</h2><div class="grid">' +
      savedDeals.entries.filter(function (entry) { return savedDeals.matches.has(entry.key); })
      .concat(savedDeals.entries.filter(function (entry) { return !savedDeals.matches.has(entry.key); })).map(function (entry) {
        var current = savedPublicMatch(entry.key);
        return current && !isLocked(current) && current.status === "alive"
          ? cardHtml(current) : savedUnavailableHtml(entry, current);
      }).join("") + '</div>';
  }
  function savedWatchHtml(d) {
    if (!savedOn() || !savedDeals.view) return "";
    var entry = savedDeals.entries.find(function (e) { return e.key === dealKey(d); });
    if (!entry) return "";
    var match = savedWatchMatch(entry, d), note = "", condition = watchCondition(d.conditions);
    if (match) note = '<p class="watch-drop">' + (match.dropped
      ? "Price dropped since you saved: was $" + entry.final_price.toFixed(2) + ", now $" + match.price.toFixed(2)
      : "Target price reached: now $" + match.price.toFixed(2) + " (target $" + entry.target_price.toFixed(2) + ")") + '</p>';
    var changed = entry.condition !== null && JSON.stringify(condition) !== JSON.stringify(entry.condition);
    var ready = watchPrice(d.final_price) !== null && condition !== null && !changed;
    if (changed) note += '<p class="watch-note">Condition changed; price watch paused. Re-save to watch this condition.</p>';
    else if (entry.final_price === null) note += '<p class="watch-note">No baseline yet: no price was recorded when you saved.</p>';
    return note + '<details class="watch-price"><summary>Watch price' +
      (entry.target_price != null ? ' <span>Target $' + entry.target_price.toFixed(2) + '</span>' : '') + '</summary>' +
      '<form data-watch-key="' + esc(entry.key) + '" data-clarity-mask="true">' +
      '<label>Target price ($)<input type="number" name="target" min="0" step="0.01" required inputmode="decimal"' +
      ' autocomplete="off" data-clarity-mask="true" value="' + (entry.target_price == null ? '' : entry.target_price) + '"' +
      (ready ? '' : ' disabled') + '></label><button type="submit"' + (ready ? '' : ' disabled') + '>Apply target</button>' +
      (entry.target_price != null ? '<button type="button" data-watch-remove-key="' + esc(entry.key) + '">Remove target</button>' : '') +
      '<p class="watch-note" role="status">Checked when you visit.' + (ready ? '' : ' No comparable current price.') + '</p></form></details>';
  }
  function savedWatchEditor() {
    var active = document.activeElement, details = active && active.closest && active.closest(".watch-price");
    var card = details && details.closest(".card[data-key]");
    return card ? {key: card.getAttribute("data-key"), open: details.open,
                   input: active.name === "target", value: active.value} : null;
  }
  function restoreSavedWatchEditor(editor) {
    if (!editor) return;
    var card = Array.prototype.find.call(cardEls(), function (el) { return el.getAttribute("data-key") === editor.key; });
    var details = card && card.querySelector(".watch-price");
    if (!details) return;
    details.open = editor.open;
    var input = details.querySelector("input"), focus = details.querySelector("summary");
    if (editor.input && input && !input.disabled) { input.value = editor.value; focus = input; }
    if (focus) focus.focus({preventScroll: true});
  }
  function repaintSaved() {
    var focus = rememberFocus();
    renderSavedControls();
    render();
    restoreFocus(focus);
    if (focus && document.activeElement === document.body) document.getElementById("savedview").focus({preventScroll: true});
  }
  function savedButtonClick(e) {
    var remove = e.target.closest("button[data-watch-remove-key]");
    if (remove && savedOn()) {
      e.preventDefault();
      e.stopPropagation();
      if (setWatchTarget(remove.getAttribute("data-watch-remove-key"), null)) repaintSaved();
      return true;
    }
    var button = e.target.closest("button[data-save-key]");
    if (!button || !savedOn()) return false;
    e.preventDefault();
    e.stopPropagation();
    var key = button.getAttribute("data-save-key");
    var changed = isSaved(key) ? removeSaved(key) : saveDeal(savedPublicMatch(key));
    if (changed) repaintSaved();
    return true;
  }
  function savedOutbound(d) {
    var current = savedPublicMatch(dealKey(d));
    if (savedOn() && savedDeals.view && isSaved(dealKey(d)) && current && !isLocked(current) && current.status === "alive") {
      track("saved_deal_click", {method: "outbound", count: 1});
      if (savedDeals.matches.has(dealKey(d))) track("watch_deal_click", {method: "outbound"});
    }
  }
  var savedViewButton = document.getElementById("savedview");
  if (savedViewButton) {
    savedViewButton.addEventListener("click", function () { setSavedView(!savedDeals.view); });
    document.getElementById("savedall").addEventListener("click", function () {
      setSavedView(false);
      savedViewButton.focus({preventScroll: true});
    });
    document.getElementById("savedclear").addEventListener("click", function () {
      if (clearSaved()) { repaintSaved(); savedViewButton.focus({preventScroll: true}); }
    });
    document.getElementById("content").addEventListener("submit", function (e) {
      var form = e.target.closest("form[data-watch-key]");
      if (!form || !savedOn() || !savedDeals.view) return;
      e.preventDefault();
      var input = form.querySelector('input[name="target"]'), value = input.value.trim();
      if (!/^\d+(?:\.\d{1,2})?$/.test(value) || watchPrice(Number(value)) === null) {
        form.querySelector('[role="status"]').textContent = "Enter a target price of $0 or more, with up to two decimal places.";
        return;
      }
      var key = form.getAttribute("data-watch-key");
      if (!setWatchTarget(key, Number(value))) return;
      repaintSaved();
      // Enter in the input must also keep focus inside this price control.
      restoreSavedWatchEditor({key: key, open: true, input: true, value: value});
    });
  }
  window.addEventListener("storage", function (e) {
    if (!savedOn() || !savedDeals.started || !savedDeals.persistent || (e.key !== SAVED_KEY && e.key !== null)) return;
    if (e.storageArea && e.storageArea !== localStorage) return;
    savedDeals.entries = parseSaved(e.key === null ? null : e.newValue);
    checkSavedWatches(false);
    repaintSaved(); // No write or analytics: another tab already performed this action.
  });
  function refreshSaved() {
    if (!savedOn() || !savedDeals.started || !savedDeals.persistent) return;
    readSaved();
    checkSavedWatches(false);
    repaintSaved();
  }
  window.addEventListener("pageshow", refreshSaved);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) refreshSaved(); });
/* /saved-deals */
