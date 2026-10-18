  function dropPickIndex() {
    var step = dropStepMs(state.meta);
    return markIndex(Date.now(), dropPhase(state.meta, step), step);
  }
  function shuffleDropPicks(rows, seed) {
    // Keep each 24-card window's store/brand/boost shares, and every protected
    // clothing, checkout and Allure slot. Only eligible ranked picks can trade.
    var options = state.meta.front_page || {}, groups = new Map(), limit = Math.min(120, rows.length);
    for (var i = 0; i < limit; i++) {
      var d = rows[i];
      if (d.pick == null || isLocked(d) || d.status === 'dead' || !d.image_url || d.niche ||
          (options.niche_categories || []).includes(d.category) || d.click_price_unknown ||
          d.price_unconfirmed || d.price_aged || (d.conditions || []).some(c => c === 'used' || c === 'open_box')) continue;
      var signature = [Math.floor(i / 24), !!d.cl, !!d.pick_checkout, !!d.allure].join(':');
      if (!groups.has(signature)) groups.set(signature, []);
      groups.get(signature).push(i);
    }
    var variants = [], seen = new Set(), run = options.max_same_category_run || 24;
    function keep(candidate) {
      for (var i = run; i < Math.min(8, candidate.length); i++) {
        if (candidate.slice(i - run, i).every(d => (d.category || 'other') === (candidate[i].category || 'other'))) return;
      }
      var key = candidate.slice(0, 15).map(dealKey).join(',');
      if (!seen.has(key)) { seen.add(key); variants.push(candidate); }
    }
    // Enumerate distinct valid heads before choosing the mark. Adjacent marks
    // cannot collide, unlike independently random shuffles or a repair fallback.
    keep(rows.slice(0, limit));
    for (var shift = 1; shift < 24; shift++) {
      var candidate = rows.slice(0, limit);
      groups.forEach(function (slots) {
        slots.forEach(function (at, j) { candidate[at] = rows[slots[(j + shift) % slots.length]]; });
      });
      keep(candidate);
    }
    if (variants.length < 2) groups.forEach(function (slots) {
      slots.forEach(function (at, j) {
        if (at >= 15) return;
        slots.slice(j + 1).forEach(function (other) {
          var candidate = rows.slice(0, limit);
          [candidate[at], candidate[other]] = [candidate[other], candidate[at]];
          keep(candidate);
        });
      });
    });
    // Tiny or fully constrained inventories have no legal alternate selection.
    if (!variants.length) return rows;
    var at = ((seed % variants.length) + variants.length) % variants.length;
    return variants[at].concat(rows.slice(limit));
  }
  function refreshDropPicks() {
    if (!frontPageView() || state.sortKey !== 'top') return;
    var focus = rememberFocus();
    // No FLIP motion for a clock-only reorder. Preserve a scrolled reader's
    // anchor synchronously, while the top-of-page view shows the new picks.
    rememberBrowse(false);
    var snapshot = window.scrollY > 0 && browseMemory.entries[browseId];
    render();
    if (snapshot) restoreBrowsePosition(snapshot, '');
    restoreFocus(focus);
    rememberBrowse(false);
  }
