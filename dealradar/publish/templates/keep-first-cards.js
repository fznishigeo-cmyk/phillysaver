  // keep_first_cards: a render that shows the cards already on screen, in the
  // same order, patches them in place instead of replacing them, so the server's
  // first screen (its text, photos and taps) survives the module's first render.
  var freshKeptNew = new Map(), freshKeptOld = new Set(), freshHold;
  function freshSig(card) {
    var img = card.querySelector('img');
    return ['.title', '.pricerow'].map(function (s) {
      var el = card.querySelector(s); return el ? el.textContent : '';
    }).concat(img ? img.getAttribute('src') : '').join('|');
  }
  function freshSame(a, b) {
    return a.nodeType === b.nodeType && a.nodeName === b.nodeName && !freshKeptOld.has(a) &&
      (a.nodeType !== 1 || a.className === b.className);
  }
  // Leave `a` equal to `b` (attributes and children), reusing the nodes that match.
  function freshPatch(a, b) {
    if (a.nodeType !== 1) { if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue; return; }
    for (var i = a.attributes.length; i--;) if (!b.hasAttribute(a.attributes[i].name)) a.removeAttribute(a.attributes[i].name);
    for (var j = 0; j < b.attributes.length; j++)
      if (a.getAttribute(b.attributes[j].name) !== b.attributes[j].value) a.setAttribute(b.attributes[j].name, b.attributes[j].value);
    freshChildren(a, b);
  }
  function freshChildren(a, b) {
    var old = a.firstChild, add = b.firstChild;
    while (add) {
      var next = add.nextSibling, kept = freshKeptNew.get(add);
      if (kept) {
        freshPatch(kept, add);
        if (kept === old) old = old.nextSibling; else a.insertBefore(kept, old);
      } else if (old && freshSame(old, add)) {
        freshPatch(old, add); old = old.nextSibling;
      } else if (old && !freshKeptOld.has(old) && old.nextSibling && freshSame(old.nextSibling, add)) {
        var gone = old; old = old.nextSibling; a.removeChild(gone); continue;
      } else if (add.nodeType === 1 && add.querySelector('.card')) {
        // A new grid (after the Join band) takes the kept cards themselves.
        var box = add.cloneNode(false); a.insertBefore(box, old); freshChildren(box, add);
      } else a.insertBefore(add, old);
      add = next;
    }
    while (old) { var after = old.nextSibling; if (!freshKeptOld.has(old)) a.removeChild(old); old = after; }
  }
  function freshKeep(content, html) {
    var shown = content.querySelectorAll('.card');
    if (!freshView() || !shown.length) return false;
    // Page cards carry their key: compare keys before parsing (a new drop replaces).
    var keyed = shown[0].hasAttribute('data-key'), keys = html.match(/<article class="card[^"]*" data-key="[^"]*"/g) || [];
    if (keyed && (keys.length !== shown.length || keys.some(function (k, i) {
      return k.split('data-key="')[1].slice(0, -1) !== shown[i].getAttribute('data-key');
    }))) return false;
    var tpl = document.createElement('template');
    tpl.innerHTML = html;
    var cards = tpl.content.querySelectorAll('.card');
    if (cards.length !== shown.length) return false;
    // The server's cards carry none: the same title, price and photo, in order.
    for (var i = 0; !keyed && i < cards.length; i++) if (freshSig(shown[i]) !== freshSig(cards[i])) return false;
    cards.forEach(function (card, i) { freshKeptNew.set(card, shown[i]); freshKeptOld.add(shown[i]); });
    try { freshChildren(content, tpl.content); } finally { freshKeptNew.clear(); freshKeptOld.clear(); }
    return true;
  }
  // A publication older than the drop clock painted its own mark. Keep that drop
  // (every rule already applied) until the clock's next mark, then drop as usual.
  function freshMark() {
    var now = freshClock();
    return freshHold && freshHold.until === now && freshHold.meta === host.state.meta ? freshHold.mark : now;
  }
  function freshDecide(rows) {
    if (freshHold !== undefined) return;
    freshHold = null;
    var content = document.getElementById('content'), titles = content.querySelectorAll('.card .title');
    var meta = host.state.meta, now = freshClock();
    if (!titles.length || content.querySelector('.card[data-key]')) return;
    var step = (Number(meta.cadence_minutes) > 0 ? Number(meta.cadence_minutes) : 15) * 60000;
    var mark = Math.floor(Date.parse(meta.published_at || meta.generated_at || '') / step);
    if (!isFinite(mark) || mark === now) return;
    var page = shuffleDropPicks(rows, mark);
    for (var i = 0; i < titles.length; i++) if (!page[i] || titles[i].textContent !== String(page[i].item)) return;
    freshHold = {mark: mark, until: now, meta: meta};
  }
