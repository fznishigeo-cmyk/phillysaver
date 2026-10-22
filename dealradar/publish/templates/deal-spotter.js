// Task 366: UI only. Grades and explanatory facts come from the server.
function parseSpotterInput(value) {
  var text = String(value || '').trim();
  return /^(?=.*\d)[a-z0-9]{10}$/i.test(text) ? {kind:'amazon', asin:text.toUpperCase()} : analyzeInput(text);
}
// Operator 2026-10-22 ("CONFIRMED proceed!"): Amazon's best sellers of the same
// kind that are on deal, under the price cap, so the exit earns on whatever the
// shopper buys. Falls back to the checker's product words.
function spotterSimilar(item) {
  var kind = toolQuery(String(item && item.category || '').replace(/&/g, ' '));
  if (kind && analyzeAdult(kind)) kind = '';
  var url = toolSimilar(kind ? Object.assign({}, item, {title: kind}) : item);
  return url.replace('&tag=', '&s=exact-aware-popularity-rank&tag=');
}
(function () {
  'use strict';
  var $ = id => document.getElementById(id), modal = $('analyze-modal'), active = null, resultItem = null;
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  var cookie = $('cookie-bar');
  if (cookie) {
    try { cookie.hidden = localStorage.getItem('wp.spotter.cookies') === '1'; } catch (_) { cookie.hidden = false; }
    $('cookie-ok').addEventListener('click', () => {
      cookie.hidden = true;
      try { localStorage.setItem('wp.spotter.cookies', '1'); } catch (_) { /* Dismiss this visit. */ }
    });
  }
  document.querySelectorAll('.spotter-menu').forEach(menu => {
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape') { menu.open = false; menu.querySelector('summary').focus(); }
    });
    document.addEventListener('click', event => { if (!menu.contains(event.target)) menu.open = false; });
  });
  if (!modal) return;

  function inputError(message, field) {
    $('start').hidden = false;
    $('spotter-result').hidden = true;
    $('spotter-story').hidden = false;
    $('input-error').textContent = message;
    if (field) { field.setAttribute('aria-invalid', 'true'); field.focus(); }
    $('input-error').scrollIntoView({block:'nearest', behavior:'instant'});
  }
  function current(task) { return active === task && !task.cancelled; }
  function cancel() {
    var task = active;
    active = null;
    if (task) {
      task.cancelled = true;
      if (task.controller) task.controller.abort();
      clearTimeout(task.timeout); clearInterval(task.clock);
    }
    if (modal.open) modal.close();
    document.body.style.overflow = '';
    if (task && task.opener) task.opener.focus();
  }
  function installSync() {
    var button = $('pwa-install'), ios = $('pwa-ios');
    var available = button && !button.hidden || ios && !ios.hidden;
    $('modal-install').hidden = !available;
    $('modal-deals').hidden = !!available;
    if (modal.open && document.activeElement.hidden) $('cancel-analysis').focus();
  }
  var actions = $('pwa-actions');
  if (actions) new MutationObserver(installSync).observe(actions, {attributes:true, subtree:true, attributeFilter:['hidden']});
  $('modal-install').addEventListener('click', () => {
    var button = $('pwa-install');
    if (button && !button.hidden) button.click();
    else if ($('pwa-ios') && !$('pwa-ios').hidden) $('pwa-ios').click();
  });
  $('cancel-analysis').addEventListener('click', cancel);
  modal.addEventListener('cancel', event => { event.preventDefault(); cancel(); });
  modal.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    var stops = [...modal.querySelectorAll('button,a[href]')].filter(el => !el.hidden && !el.disabled);
    var first = stops[0], last = stops[stops.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });

  function timeText(asOf) {
    return new Intl.DateTimeFormat('en-US', {month:'short', day:'numeric', year:'numeric', hour:'numeric',
      minute:'2-digit', timeZoneName:'short', timeZone:'America/New_York'}).format(new Date(asOf));
  }
  function renderResult(task) {
    var item = task.item, grade = task.data.deal_grade;
    if (!item || !grade || !['A','B','C'].includes(grade.grade)) {
      var reason = task.data.reason || 'no_price';
      cancel(); inputError(analyzeReason(reason), task.field); return;
    }
    cancel(); resultItem = item;
    $('start').hidden = true; $('spotter-story').hidden = true; $('spotter-result').hidden = false;
    $('result-title').textContent = item.title;
    $('result-photo').innerHTML = typeof item.image === 'string' &&
      /^https:\/\/m\.media-amazon\.com\/images\/I\/[A-Za-z0-9+_.%-]+\.(?:jpg|jpeg|png|webp)$/i.test(item.image)
      ? '<img src="' + esc(item.image) + '" alt="' + esc(item.title) + '" width="300" height="300" decoding="async" referrerpolicy="no-referrer">'
      : '<span class="no-photo">Product image unavailable</span>';
    $('result-price').className = 'result-price';
    $('result-price').textContent = '$' + (item.price_cents / 100).toFixed(2);
    if (Number.isSafeInteger(item.shipping_cents) && item.shipping_cents > 0) {
      var ship = document.createElement('small');
      ship.className = 'result-shipping';
      ship.textContent = '+ $' + (item.shipping_cents / 100).toFixed(2) + ' shipping';
      $('result-price').appendChild(ship);
    }
    var listFact = Array.isArray(grade.facts) && grade.facts.find(fact => typeof fact === 'string' && /^\d+% below the list price$/.test(fact));
    $('result-list').textContent = Number.isSafeInteger(item.list_price_cents) && item.list_price_cents > item.price_cents ?
      '$' + (item.list_price_cents / 100).toFixed(2) + ' Amazon list price' +
      (listFact ? ' · ' + listFact.replace('below the list price', 'off list price') : '') : '';
    $('result-time').textContent = 'Price as of ' + timeText(item.as_of) + '. Prices can change; confirm at Amazon before buying.';
    $('grade-letter').textContent = grade.grade;
    $('grade-letter').className = 'grade-letter ' + grade.grade.toLowerCase();
    $('grade-label').textContent = grade.label;
    $('result-buy').href = toolAmazon(item.asin);
    toolActive = {raw:task.raw, input:task.input};
    $('result-similar').href = spotterSimilar(item);
    if ($('review-link')) $('review-link').href = '/review-checker/?asin=' + encodeURIComponent(item.asin);
    var facts = (Array.isArray(grade.facts) ? grade.facts : []).filter(fact => typeof fact === 'string');
    var minutes = Math.max(0, Math.floor((Date.now() - Date.parse(item.as_of)) / 60000));
    facts.push(minutes < 1 ? 'Price checked less than a minute ago' : 'Price checked ' + minutes + ' minutes ago');
    var monthly = monthlyBoughtLabel(item.monthlySold);
    if (monthly) facts.push('Popular: ' + monthly);
    $('result-facts').innerHTML = facts.map(fact => '<li>' + esc(fact) + '</li>').join('');
    $('share-status').textContent = '';
    var shareURL = new URL('/deal-spotter/', location.origin); shareURL.searchParams.set('asin', item.asin);
    history.replaceState(null, '', shareURL.pathname + shareURL.search);
    $('result-title').focus({preventScroll:true});
    $('spotter-result').scrollIntoView({block:'start', behavior:'instant'});
  }
  function tick(task) {
    if (!current(task)) return;
    var elapsed = performance.now() - task.started;
    var percent = Math.min(task.ready ? 100 : 99, elapsed / task.duration * 100);
    $('modal-bar').style.width = percent + '%';
    $('modal-percent').textContent = percent.toFixed(1) + '%';
    modal.querySelector('[role="progressbar"]').setAttribute('aria-valuenow', String(Math.floor(percent)));
    var index = Math.min(3, Math.floor(percent / 25));
    var step = ['Finding the product..', "Checking today's price..", 'Comparing with verified deals..', 'Grading..'][index];
    if ($('modal-step').textContent !== step) $('modal-step').textContent = step;
    if (task.ready && elapsed >= task.duration) renderResult(task);
  }
  async function lookup(task) {
    for (var retry = 0; retry < 3 && current(task); retry++) {
      task.controller = new AbortController();
      var data;
      try {
        // Past the server's 10 s wait for Keepa's live refresh (functions/api/search.js).
        task.timeout = setTimeout(() => task.controller.abort(), 14000);
        var response = await fetch('/api/search?asin=' + task.input.asin + '&grade=deal',
          {signal:task.controller.signal, cache:'no-store', credentials:'omit', referrerPolicy:'no-referrer'});
        data = response.ok ? await response.json() : {items:[], reason:'unavailable'};
      } catch (error) { data = {items:[], reason:error.name === 'AbortError' ? 'timeout' : 'unavailable'}; }
      finally { clearTimeout(task.timeout); }
      if (!current(task)) return;
      if (['rate_limited','timeout'].includes(data?.reason) && retry < 2) {
        await new Promise(resolve => setTimeout(resolve, 1500)); continue;
      }
      task.data = data && typeof data === 'object' ? data : {items:[], reason:'unavailable'};
      task.item = analyzeItems(task.data.items, task.input.asin)[0];
      if (task.item) $('modal-product').textContent = task.item.title;
      task.ready = true; tick(task); return;
    }
  }
  function submit(raw, field, opener) {
    if (active) return;
    var input = parseSpotterInput(raw);
    document.querySelectorAll('[data-analyze] input').forEach(el => el.removeAttribute('aria-invalid'));
    if (input.kind !== 'amazon') {
      inputError(input.kind === 'short' ? analyzeReason('short') : 'Paste an Amazon product link or a 10-character ASIN to get started.', field); return;
    }
    $('input-error').textContent = '';
    $('q').value = raw; $('header-link').value = raw;
    var task = {raw, input, field, opener:opener || field, started:performance.now(), duration:10000 + Math.random() * 15000,
      ready:false, cancelled:false};
    active = task;
    $('modal-product').textContent = 'Amazon product · ' + input.asin;
    $('modal-bar').style.width = '0%'; $('modal-percent').textContent = '0.0%';
    $('modal-step').textContent = 'Finding the product..';
    modal.querySelector('[role="progressbar"]').setAttribute('aria-valuenow', '0');
    installSync(); modal.showModal(); document.body.style.overflow = 'hidden'; $('cancel-analysis').focus();
    task.clock = setInterval(() => tick(task), 100);
    void lookup(task);
  }
  document.querySelectorAll('[data-analyze]').forEach(form => {
    form.addEventListener('submit', event => {
      event.preventDefault(); var field = form.querySelector('input'); submit(field.value.trim(), field, event.submitter || field);
    });
    form.querySelector('input').addEventListener('input', event => { event.target.removeAttribute('aria-invalid'); $('input-error').textContent = ''; });
  });
  $('reanalyze').addEventListener('click', event => { if (resultItem) submit(resultItem.asin, $('q'), event.currentTarget); });
  $('share').addEventListener('click', async () => {
    var url = new URL('/deal-spotter/', location.origin); url.searchParams.set('asin', resultItem.asin);
    if (navigator.share) {
      try { await navigator.share({title:'Fake Deal Spotter by wowpriced', url:url.href}); return; }
      catch (error) { if (error.name === 'AbortError') return; }
    }
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(url.href);
      else {
        var text = document.createElement('textarea'); text.value = url.href;
        text.style.position = 'fixed'; text.style.top = '0'; document.body.append(text); text.select();
        var copied = document.execCommand('copy'); text.remove(); $('share').focus();
        if (!copied) throw Error('copy');
      }
      $('share-status').textContent = 'Link copied';
    } catch (error) { if (error.name !== 'AbortError') $('share-status').textContent = 'Copy the link from your address bar to share.'; }
  });

  // Below-the-fold animation starts only when visible and pauses for keyboard use.
  var stepButtons = [...document.querySelectorAll('.how-step')], stepIndex = 0, paused = false, visible = false;
  function step(index) {
    stepIndex = index;
    stepButtons.forEach((button, i) => { button.classList.toggle('active', i === index); button.setAttribute('aria-pressed', String(i === index)); });
    document.querySelector('.illustration').dataset.illustration = String(index);
  }
  stepButtons.forEach((button, index) => button.addEventListener('click', () => { paused = true; step(index); $('pause-steps').textContent = 'Play animation'; $('pause-steps').setAttribute('aria-pressed','true'); }));
  $('pause-steps').addEventListener('click', () => {
    paused = !paused; $('pause-steps').textContent = paused ? 'Play animation' : 'Pause animation';
    $('pause-steps').setAttribute('aria-pressed', String(paused));
  });
  var interval = setInterval(() => {
    if (visible && !paused && !reduced.matches && !document.hidden && !document.querySelector('.how-steps').contains(document.activeElement)) step((stepIndex + 1) % 4);
  }, 5000);
  var observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.target.id === 'how-it-works') visible = entry.isIntersecting;
      else entry.target.classList.toggle('paused', !entry.isIntersecting || entry.target.dataset.paused === 'true');
    });
  });
  observer.observe($('how-it-works')); observer.observe(document.querySelector('.demo-frame'));
  $('pause-demo').addEventListener('click', () => {
    var frame = document.querySelector('.demo-frame'), paused = frame.dataset.paused !== 'true';
    frame.dataset.paused = String(paused); frame.classList.toggle('paused', paused);
    $('pause-demo').setAttribute('aria-pressed', String(paused));
  });
  // Replay restarts the CSS demo and its progress line from the first frame.
  $('replay-demo').addEventListener('click', () => {
    document.querySelectorAll('.demo-frame *, .demo-progress i').forEach(el => { el.style.animation = 'none'; void el.offsetWidth; el.style.animation = ''; });
  });
  var slide = 0, names = ['Fake Deal Spotter', "Today's Deals", 'Discount Finder'];
  var captions = ['A link goes in. A clear deal grade comes out.', 'Explore the verified deals published on wowpriced.', 'Find Amazon discounts for what you have in mind.'];
  function showSlide(index) {
    slide = (index + 3) % 3;
    document.querySelectorAll('[data-slide]').forEach(el => { el.hidden = Number(el.dataset.slide) !== slide; });
    document.querySelectorAll('.guide-item').forEach((item, i) => { item.classList.toggle('active', i === slide); item.setAttribute('aria-pressed', String(i === slide)); });
    $('slide-title').textContent = names[slide]; $('slide-caption').textContent = captions[slide]; $('slide-count').textContent = '0' + (slide + 1) + ' / 03';
  }
  $('phone-prev').addEventListener('click', () => showSlide(slide - 1)); $('phone-next').addEventListener('click', () => showSlide(slide + 1));
  document.querySelectorAll('.guide-item').forEach((item, i) => item.addEventListener('click', () => showSlide(i)));
  // The same screens, framed as a computer browser or a phone.
  var tabs = [...document.querySelectorAll('.screen-tab')];
  tabs.forEach(tab => tab.addEventListener('click', () => {
    $('screen-stage').dataset.view = tab.dataset.view;
    tabs.forEach(other => other.setAttribute('aria-pressed', String(other === tab)));
  }));
  window.addEventListener('pagehide', () => { cancel(); clearInterval(interval); observer.disconnect(); });
  // A shared URL reruns the same cache-respecting lookup. No price facts in URLs.
  var params = new URLSearchParams(location.search), initial = params.get('asin');
  if (initial) {
    if (params.getAll('asin').length !== 1 || !/^[a-z0-9]{10}$/i.test(initial)) inputError('That product link looks incomplete. Paste a new Amazon link to try again.', $('q'));
    else submit(initial, $('q'));
  }
})();
