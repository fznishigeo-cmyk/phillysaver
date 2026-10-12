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
      var sources = new Set(d.src || []).size;
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
