  var famPack = /{{FAMILY_PACK}}/g, famWords = /{{FAMILY_WORDS}}/g;
  var famRules = {{FAMILY_RULES}};
  function productFamily(d) {
    var stem = String(d.item || '').toLowerCase().replace(/['’]s/g, '').replace(famPack, ' ')
      .replace(/[^a-z0-9]+/g, ' ').replace(famWords, ' ').trim().split(/\s+/).slice(0, 5).join(' ');
    return stem ? String(d.store || '').trim().toLowerCase() + '|' + stem : '';
  }
  function famFits(d, min, out) {
    var pos = out.length, p = famRules;
    if (pos < min) return false;
    if (d.status === 'dead' && (pos < 8 || out.slice(Math.max(0, pos - p.gone + 1)).some(r => r.status === 'dead'))) return false;
    if (!frontPageView()) return true;
    if (d.allure && p.allure && (pos < p.first || out.slice(Math.max(0, pos - p.allure + 1)).some(r => r.allure))) return false;
    if (d.cl && p.clothing && pos < p.slots && out.slice(Math.floor(pos / p.mix) * p.mix).filter(r => r.cl).length >= p.clothing) return false;
    if (d.pick_checkout && out.slice(Math.max(0, pos - p.promo + 1)).filter(r => r.pick_checkout).length >= p.cap) return false;
    return true;
  }
  function spaceSameProducts(rows, prefix) {
    prefix = prefix || [];
    if (prefix.length >= 240) return rows.slice();
    var window = rows.slice(0, 299 - prefix.length);
    var first = {}, home = frontPageView();
    function flags(d) {
      return (home ? ['niche','locked','click_price_unknown','price_aged','price_unconfirmed','cl','allure','pick_checkout'] : ['locked'])
        .filter(k => d[k]).concat(d.image_url ? [] : ['photo'], d.status === 'dead' ? ['gone'] : []);
    }
    var pend = prefix.concat(window).map((d, i) =>
      [d, productFamily(d), Math.max(0, ...flags(d).map(k => first[k] ??= i))]).slice(prefix.length);
    var out = prefix.slice(), fams = prefix.map(productFamily), moved = false;
    while (pend.length && out.length < 240) {
      var pos = out.length, at = pend.slice(0, 60).findIndex((en, i) => {
        var key = en[1];
        return (!key || (!fams.slice(-8).includes(key) && (pos >= 48 || fams.filter(k => k === key).length < 2))) &&
          (!moved && i === 0 || famFits(en[0], en[2], out));
      });
      at = Math.max(0, at);
      moved = moved || at > 0;
      var en = pend.splice(at, 1)[0];
      out.push(en[0]);
      fams.push(en[1]);
    }
    return out.slice(prefix.length).concat(pend.map(e => e[0]), rows.slice(window.length));
  }
