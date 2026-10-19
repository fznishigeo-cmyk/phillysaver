  var freshRules = {{FRESH_RULES}}, freshCache = null;
  function freshView() { return host.freshView(); }
  function freshSize() { return (state.meta.front_page || {}).fresh_page_size || 60; }
  function freshWomen(d) {
    return !!d.allure || ['beauty','home','kitchen','kids_baby','toys_games'].includes(d.category) ||
      /\b(women|womens|woman|ladies|girls|baby|kids|beauty|home|kitchen)\b/i.test(d.item || '');
  }
  function freshCheap(d) { return typeof d.final_price === 'number' && d.final_price >= 0 && d.final_price <= 50; }
  function freshFamily(d) {
    var title = String(d.item || '').toLowerCase().replace(/['’]s/g, '')
      .replace(/{{FAMILY_PACK}}/g, ' ').replace(/[^a-z0-9]+/g, ' ').replace(/{{FAMILY_WORDS}}/g, ' ').trim();
    return title ? String(d.store || '').trim().toLowerCase() + '|' + title.split(/\s+/).slice(0,5).join(' ') : '';
  }
  function freshRotation(rows) {
    var options = state.meta.front_page || {}, seen = new Set(), keyFor = dealKey, locked = isLocked;
    var pool = rows.slice().sort((a,b) => (a.pick == null ? Infinity : a.pick) - (b.pick == null ? Infinity : b.pick) ||
      (b.ps || 0) - (a.ps || 0) || Number(!!b.earns) - Number(!!a.earns) || (keyFor(a) < keyFor(b) ? -1 : keyFor(a) > keyFor(b) ? 1 : 0))
      .filter(function (d) {
        if (locked(d) || d.status !== 'alive' || !d.image_url || d.niche ||
            (options.niche_categories || []).includes(d.category) || d.click_price_unknown || d.price_unconfirmed ||
            d.price_aged || d.fresh_blocked || (d.conditions || []).some(c => ['used','open_box','refurbished'].includes(c))) return false;
        var key = freshFamily(d) || keyFor(d);
        if (seen.has(key)) return false;
        seen.add(key); return true;
      });
    var lanes = [new Map(),new Map()], female = 0;
    pool.forEach(function (d) {
      var woman = freshWomen(d) ? 1 : 0; female += woman;
      var signature = (d.category ?? '') + ':' + freshCheap(d) + ':' + !!d.cl + ':' + !!d.pick_checkout + ':' + !!d.allure;
      if (!lanes[woman].has(signature)) lanes[woman].set(signature,{rows:[],at:0});
      lanes[woman].get(signature).rows.push(d);
    });
    var groups = lanes.map(lane => Array.from(lane.values())), out = [], total = pool.length, served = [0,0], totals = [total-female,female];
    for (var pos = 0; pos < total; pos++) {
      var lane = Math.floor((pos+1)*female/total) > Math.floor(pos*female/total) ? 1 : 0, best = null;
      var choices = groups[lane], turn = served[lane]+1, count = totals[lane], score = -Infinity;
      for (var i = 0; i < choices.length; i++) {
        var g = choices[i], priority = turn*g.rows.length-g.at*count;
        if (g.at < g.rows.length && priority > score) { best = g; score = priority; }
      }
      out.push(best.rows[best.at++]); served[lane]++;
    }
    return out;
  }
  function freshBasePage(pool, mark) {
    if (!pool.length) return [];
    var size = freshSize(), start = ((mark * size % pool.length) + pool.length) % pool.length;
    var pending = Array.from({length:Math.min(size,pool.length)}, (_,i) => pool[(start+i)%pool.length]), out = [];
    var target = Math.min(19,pending.filter(freshCheap).length,pending.length), options = state.meta.front_page || {}, p = freshRules;
    function fits(d) {
      var pos = out.length, run = options.max_same_category_run || 2;
      if (d.allure && p.allure && (pos < p.first || out.slice(Math.max(0,pos-p.allure+1)).some(r => r.allure))) return false;
      if (d.cl && p.clothing && pos < p.slots && out.slice(Math.floor(pos/p.mix)*p.mix).filter(r => r.cl).length >= p.clothing) return false;
      if (d.pick_checkout && out.slice(Math.max(0,pos-p.promo+1)).filter(r => r.pick_checkout).length >= p.cap) return false;
      return !(pos >= run && out.slice(-run).every(r => r.category === d.category));
    }
    var categories = new Map(), total = pending.length;
    pending.forEach(function (d) {
      categories.set(d.category,(categories.get(d.category)||0)+1);
    });
    function room(d) {
      var pos = out.length;
      var run = options.max_same_category_run || 2;
      for (var [cat,count] of categories) {
        var remaining = count-Number(d.category===cat), other = total-pos-1-remaining, streak = 0;
        if (d.category === cat) {
          streak=1;
          for (var i=pos-1;i>=0 && out[i].category===cat;i--) streak++;
        }
        if (remaining > run*(other+1)-streak) return false;
      }
      return true;
    }
    while (pending.length) {
      var need = out.length < 24 && 24-out.length <= target-out.slice(0,24).filter(freshCheap).length;
      // Select the same stable priority winner without sorting every remaining
      // row at every slot. Keep the best fitting fallback if no row leaves room.
      var at = -1, fallback = -1, best = -1, backup = -1;
      for (var i = 0; i < pending.length; i++) {
        var d = pending[i], cheap = freshCheap(d);
        var priority = 8*(need && cheap)+2*(!!d.allure + !!d.pick_checkout)+(out.length<24 && cheap);
        if (priority <= best || !fits(d)) continue;
        if (priority > backup) { fallback = i; backup = priority; }
        if (room(d)) { at = i; best = priority; }
      }
      if (at < 0) at = fallback < 0 ? 0 : fallback;
      var d = pending.splice(at,1)[0]; out.push(d);
      categories.set(d.category,categories.get(d.category)-1);
    }
    return out;
  }
  function freshPage(pool, mark) {
    var out = freshBasePage(pool,mark), size = freshSize();
    if (pool.length < 2*size) return out;
    // The untouched prefix gives exact previous positions without recursive history.
    var boundary = size-Math.floor(size/4), previous = freshBasePage(pool,mark-1);
    var unavailable = new Set(out.map(dealKey)), start = (((mark+1)*size%pool.length)+pool.length)%pool.length;
    for (var i=0;i<size;i++) unavailable.add(dealKey(pool[(start+i)%pool.length]));
    function signature(d) { return [d.category,freshWomen(d),freshCheap(d),!!d.cl,!!d.pick_checkout,!!d.allure].join(':'); }
    var candidates = previous.slice(0,boundary).map((d,i)=>({d:d,old:i})).sort((a,b)=>
      (a.d.pick == null ? Infinity : a.d.pick)-(b.d.pick == null ? Infinity : b.d.pick) ||
      (b.d.ps||0)-(a.d.ps||0) || Number(!!b.d.earns)-Number(!!a.d.earns) ||
      (dealKey(a.d)<dealKey(b.d)?-1:dealKey(a.d)>dealKey(b.d)?1:0));
    var available = Array.from({length:size-boundary},(_,i)=>boundary+i), signatures = out.map(signature);
    candidates.forEach(function (c) {
      if (unavailable.has(dealKey(c.d))) return;
      var match = signature(c.d);
      var at = available.findIndex(i=>Math.abs(i-c.old)>=12 && match===signatures[i]);
      if (at >= 0) out[available.splice(at,1)[0]] = c.d;
    });
    return out;
  }
  function shuffleDropPicks(rows, seed) {
    // Cache by inventory identity and rank order, never by a visitor's history.
    var signature = rows.map(dealKey).join(',');
    if (!freshCache || freshCache.inventory !== state.deals || freshCache.signature !== signature || freshCache.options !== state.meta.front_page)
      freshCache = {inventory:state.deals, signature:signature, options:state.meta.front_page, pool:freshRotation(rows)};
    return freshPage(freshCache.pool, seed);
  }
  var freshDropHead = [], freshDropBefore = null, freshDropKeys = new Set(), freshDropMark = null;
  var freshDropAnimate = false, freshDropTimer, freshPending = false, freshCelebrated = null;
  function celebrateFreshDrop() {
    var mark = dropPickIndex();
    if (freshCelebrated === mark) return;
    freshCelebrated = mark; startConfetti();
  }
  function freshCycleText() {
    var n = freshCache ? freshCache.pool.length : 0;
    return n ? 'Full rotation: ' + Math.ceil(n/freshSize()) + ' drops (about ' + Math.ceil(n/freshSize()) * (state.meta.cadence_minutes || 15) + ' minutes)' : '';
  }
  function freshControls() {
    var total = state.deals.filter(d => d.status !== 'dead').length;
    return '<div class="more fresh-more"><button type="button" id="fresh-all" class="pwbtn">See all ' + total.toLocaleString() + '+ deals</button><p class="meta">' + freshCycleText() + '</p></div>';
  }
  function updateFreshDrop(rows) {
    var pill = document.getElementById('fresh-drop-pill');
    if (!freshView()) { pill.hidden = true; freshPending = false; return; }
    var mark = dropPickIndex(), head = rows.map(dealKey);
    if (freshCelebrated === null) freshCelebrated = mark;
    if (freshPending) return;
    pill.hidden = true;
    if (freshDropMark !== mark) freshDropKeys.clear();
    if (freshDropBefore && freshDropMark !== mark) {
      freshDropKeys = new Set(head.filter(key => !freshDropBefore.has(key)));
      freshDropAnimate = freshDropKeys.size > 0;
    }
    freshDropBefore = null; freshDropHead = head; freshDropMark = mark;
  }
  function acceptFreshDrop() {
    freshDropBefore = new Set(freshDropHead); freshPending = false;
    window.scrollTo({top:0,behavior:'auto'});
    render(); rememberBrowse(false);
  }
  function deferFreshDrop() {
    if (!freshView() || window.scrollY < 200 || freshDropMark === dropPickIndex()) return false;
    if (freshDropMark !== null) celebrateFreshDrop();
    freshPending = true; freshDropKeys.clear();
    document.querySelectorAll('.just-dropped').forEach(el => el.remove());
    var next = shuffleDropPicks(state.deals.concat(state.spotlight || []),dropPickIndex());
    var before = new Set(freshDropHead), count = next.filter(d => !before.has(dealKey(d))).length;
    var pill = document.getElementById('fresh-drop-pill');
    pill.textContent = count + ' new deals just dropped ↑'; pill.hidden = false; pill.onclick = acceptFreshDrop;
    return true;
  }
  function paintFreshDrop() {
    var all = document.getElementById('fresh-all');
    if (all) all.onclick = function () { state.catTab = 1; renderTabs(); finishBrowseAction('category', true); window.scrollTo({top:0,behavior:'auto'}); };
    if (!freshDropAnimate) return;
    freshDropAnimate = false;
    var el = document.getElementById('fresh-drop-toast');
    el.textContent = freshDropKeys.size + ' new deals just dropped'; el.hidden = false;
    clearTimeout(freshDropTimer);
    freshDropTimer = setTimeout(function () { el.hidden = true; }, 4000);
    celebrateFreshDrop();
    if (document.hidden || reduceMotion() || window.scrollY >= 200) return;
    Array.from(cardEls()).filter(el => freshDropKeys.has(el.getAttribute('data-key'))).forEach(function (el, i) {
      el.animate([{opacity:0,transform:'translateY(8px)'},{opacity:1,transform:'translateY(0)'}],
        {duration:250,delay:i * 300/Math.max(1,freshSize()-1),easing:'ease-out',fill:'backwards'});
    });
  }
