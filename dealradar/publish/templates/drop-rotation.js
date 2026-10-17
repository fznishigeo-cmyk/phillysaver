/*clothing*/  function mixAllowed(out, d) {
    var mix = state.meta.front_page.top_picks || {}, pos = out.length;
    var cap = mix.max_clothing_per_window || 0, width = Math.max(1, mix.mix_window || 6);
    if (!cap) return true;
    var run = state.meta.front_page.max_same_category_run || DROP_WINDOW;
    if (pos < 8 && pos >= run && out.slice(-run).every(r => (r.category || 'other') === (d.category || 'other'))) return false;
    return !d.cl || pos >= (mix.mix_slots ?? 24) ||
      out.slice(Math.floor(pos / width) * width).filter(d => d.cl).length < cap;
  }
/*/clothing*/  function rotatePicks(picks, seed, repair) {
    var pages = repair ? 0 : Math.min({{ROTATION_WINDOWS}}, Math.floor(picks.length / DROP_WINDOW));
    var end = pages * DROP_WINDOW, at = pages ? ((seed % pages + pages) % pages) * DROP_WINDOW : 0;
    var rows = picks.slice(at, end).concat(picks.slice(0, at), picks.slice(end));
    if (!repair) {
      var fresh = new Set(picks.filter(d => state.newKeys[dealKey(d)]).map(dealKey));
      try {
        var signature = seed + ":{{ROTATION_WINDOWS}}:" + picks.map(dealKey).join(",");
        var saved = JSON.parse(sessionStorage.getItem("wp.drop-picks") || "[]");
        if (!Array.isArray(saved)) saved = [];
        var entry = saved.find(d => d && d.signature === signature && Array.isArray(d.keys));
        if (entry) {
          fresh = new Set(entry.keys);
        } else {
          saved.push({signature: signature, keys: Array.from(fresh)});
          sessionStorage.setItem("wp.drop-picks", JSON.stringify(saved.slice(-20)));
        }
      } catch (e) {}
      var first = rows.slice(0, DROP_WINDOW).sort((a, b) => fresh.has(dealKey(b)) - fresh.has(dealKey(a)));
      rows = first.concat(rows.slice(DROP_WINDOW));
    }
    var pools = new Map();
    for (var i = 0; i < rows.length; i += DROP_WINDOW) pools.set(i, rows.slice(i, i + DROP_WINDOW));
    var options = state.meta.front_page, opts = options.checkout_price_deals;/*clothing*/
    // Keep each class in its server slots, including arrival promotion.
    var mixOn = (options.top_picks || {}).max_clothing_per_window > 0;
    if (mixOn && !repair && Array.from(pools).some(([i, pool]) =>
        pool.filter(d => d.cl).length !== picks.slice(i, i + DROP_WINDOW).filter(d => d.cl).length)) return picks;
/*/clothing*/
    var maxRun = options.max_same_category_run || DROP_WINDOW;
    var width = Math.max(1, Math.trunc(opts.top_pick_window ?? 5));
    var cap = Math.max(0, Math.trunc(opts.top_pick_max_in_window ?? 1));
    function promo(d) { return !!d.pick_checkout; }
    function fits(pos, recent, left) {
      for (; pos < rows.length; pos++) {
        if (pools.has(pos)) left = pools.get(pos).filter(promo).length;
        recent = recent.filter(n => pos - n < width);
        if (left && recent.length < cap && (!repair || rows[pos].pick != null || promo(rows[pos]))) { left--; recent.push(pos); }
        if (left && (pools.has(pos + 1) || pos + 1 === rows.length)) return false;
      }
      return true;
    }
    function allureFits(pos, recent, pool) {
      var last = out.findLastIndex(d => d.allure), ready = Math.max(pos, {{ALLURE_START}}, {{ALLURE_LAST}});
      var deadline = Math.min(rows.length - 1, Math.ceil((pos + 1) / DROP_WINDOW) * DROP_WINDOW - 1, repair.nextAllure - {{ALLURE_EVERY}});
      if (pool.filter(d => d.allure).length > Math.max(0, Math.floor((deadline - ready) / {{ALLURE_EVERY}}) + 1)) return false;
      if (cap !== 1) return true;
      var both = pool.filter(d => d.allure && promo(d)).length;
      if (!both) return true;
      var plain = pool.filter(promo).length - both;
      var first = Math.max(pos, recent.length ? recent[recent.length - 1] + width : pos);
      for (var lead = 0; lead <= plain; lead++) {
        var firstAllure = Math.max(ready, first + lead * width);
        for (var tail = 0; tail <= plain - lead; tail++) {
          var between = plain - lead - tail;
          var lastAllure = firstAllure + (both - 1 + between) * width + Math.max(0, both - 1 - between) * Math.max(0, {{ALLURE_EVERY}} - width);
          if (lastAllure <= deadline && lastAllure + tail * width < rows.length) return true;
        }
      }
      return false;
    }
    var out = [], limit = 1024;
    function slots(pos, recent, last, run, pool) {
      if (!limit--) return false;
      if (pos === rows.length) return true;
      if (pools.has(pos)) pool = pools.get(pos).slice();
      var left = pool.filter(promo).length;
      recent = recent.filter(n => pos - n < width);
      if (!fits(pos, recent, left) || (repair && !allureFits(pos, recent, pool))) return false;
      var want = promo(rows[pos]), tried = new Set();
      var choices = pool.slice().sort((a, b) => (repair ? !!b.allure - !!a.allure : 0) ||
        (promo(b) === want) - (promo(a) === want));
      for (var d of choices) {
        if (!limit) break;
        var cat = d.category || "other", bit = promo(d), next = bit ? recent.concat(pos) : recent;
        var key = (pos < 8 ? cat + bit : bit) + (repair ? ':' + !!d.allure : '')/*clothing*/ + ':' + !!d.cl/*/clothing*/;
        if (/*clothing*/!mixAllowed(out, d) || (mixOn && !repair && !!d.cl !== !!picks[pos].cl) ||
            /*/clothing*/next.length > cap || tried.has(key) || (pos < 8 && cat === last && run >= maxRun) ||
            (repair && ((d.pick == null || rows[pos].pick == null) && d !== rows[pos] ||
              d.status !== rows[pos].status || !!d.click_price_unknown !== !!rows[pos].click_price_unknown ||
              (d.allure && (pos < {{ALLURE_START}} || repair.nextAllure - pos < {{ALLURE_EVERY}} || {{ALLURE_RECENT}} ||
                rows.slice(pos + 1, pos + {{ALLURE_EVERY}}).some(d => d.pick == null && d.allure)))))) continue;
        tried.add(key);
        var i = pool.indexOf(d);
        pool.splice(i, 1); out.push(d);
        if (slots(pos + 1, next, cat, cat === last ? run + 1 : 1, pool)) return true;
        out.pop(); pool.splice(i, 0, d);
      }
      return false;
    }
    if (slots(0, [], "", 0, [])) return out;
    return repair ? null : picks;
  }
  function safeRotatedPromos(picks, width, cap) {
    var safe = [], recent = [], lastAllure = -{{ALLURE_EVERY}}, nextGone = 8;
    var end = picks.length;
    for (var i = 0; i < end; i++) {
      var d = picks[i], pos = safe.length;
      recent = recent.filter(n => pos - n < width);
      if (/*clothing*/!mixAllowed(safe, d) || /*/clothing*/(d.pick_checkout && recent.length >= cap) ||
          (d.allure && (pos < {{ALLURE_START}} || pos - lastAllure < {{ALLURE_EVERY}})) ||
          (d.click_price_unknown && pos < 120) || (d.status === 'dead' && pos < nextGone)) {
        end = Math.min(end, Math.ceil((i + 1) / DROP_WINDOW) * DROP_WINDOW);
        continue;
      }
      if (d.pick_checkout) recent.push(pos);
      if (d.allure) lastAllure = pos;
      if (d.status === 'dead') nextGone = pos + 8;
      safe.push(d);
    }
    return safe;
  }
  function spaceRotatedPromos(rows) {
    if (!frontPageView() || state.sortKey !== 'top') return rows;
    var end = rows.length;
    while (end && rows[end - 1].pick == null) end--;
    var picks = rows.slice(0, end), opts = state.meta.front_page.checkout_price_deals/*clothing*/ || {}/*/clothing*/;
    var width = Math.max(1, Math.trunc(opts.top_pick_window ?? 5)), cap = Math.max(0, Math.trunc(opts.top_pick_max_in_window ?? 1));
    if (!picks.some((_, i) => picks.slice(i, i + width).filter(d => d.pick_checkout).length > cap/*clothing*/ ||
        !mixAllowed(picks.slice(0, i), _)/*/clothing*/)) return rows;
    var nextAllure = rows.findIndex((d, i) => i >= end && d.allure);
    var repaired = rotatePicks(picks, state.seed, {nextAllure: nextAllure < 0 ? Infinity : nextAllure});
    return repaired ? repaired.concat(rows.slice(end)) : safeRotatedPromos(rows, width, cap);
  }
