function reviewInput(value) {
  var text = String(value || '').trim();
  // A bare ASIN needs a digit, so a typed ten-letter word never spends a lookup.
  return /^(?=.*\d)[a-z0-9]{10}$/i.test(text) ? {kind:'amazon', asin:text.toUpperCase()} : analyzeInput(text);
}
var reviewExclude = toolSettings.exclude ? new RegExp(toolSettings.exclude, 'iu') : null;
function reviewExcluded(title) { return !!reviewExclude && reviewExclude.test(String(title || '')); }
function reviewProgress(elapsed, duration, ready) {
  return ready && elapsed >= duration ? 100 : Math.min(99, Math.floor(elapsed / duration * 100));
}
(function () {
  var form = document.getElementById('review-form'), input = document.getElementById('review-input');
  var message = document.getElementById('input-message'), dialog = document.getElementById('review-loading');
  var report = document.getElementById('review-report'), start = document.getElementById('review-start');
  var active = null, opener = null;
  function element(tag, text, className) {
    var el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  }
  function link(text, url, className) {
    var el = element('a', text, className); el.href = url; return el;
  }
  function photo(url, title) {
    var holder = element('div', undefined, 'report-photo');
    if (typeof url === 'string' && /^https:\/\/m\.media-amazon\.com\/images\/I\/[A-Za-z0-9+_.%-]+\.(?:jpg|jpeg|png|webp)$/i.test(url)) {
      var img = element('img'); img.src = url; img.alt = title; img.width = 180; img.height = 190;
      img.referrerPolicy = 'no-referrer'; holder.append(img);
    } else {var icon = element('span', '◇'); icon.setAttribute('aria-hidden', 'true'); holder.append(icon);}
    return holder;
  }
  function stop(restoreFocus) {
    if (active) {clearInterval(active.timer); active.controller.abort(); active = null;}
    if (dialog.open) dialog.close();
    if (restoreFocus && opener?.isConnected) opener.focus();
  }
  function showReport(data, asin) {
    var item = data.items?.[0], review = data.review;
    if (!item || item.asin !== asin || typeof item.title !== 'string' ||
        !review || !['A','B','C'].includes(review.grade) || typeof review.label !== 'string' || !Array.isArray(review.checks)) {
      message.textContent = 'We couldn’t complete this check just now. Please try again in a moment.';
      input.focus(); return;
    }
    // Same adult exclusion as Deal Checker and Deal Spotter.
    if (reviewExcluded(item.title)) {message.textContent = 'This product is not shown in our results.'; input.focus(); return;}
    report.replaceChildren();
    var line = element('div', undefined, 'report-topline');
    line.append(element('span', 'YOUR REVIEW SIGNAL REPORT'), link('How the grades work', '#grades'));
    var hero = element('div', undefined, 'report-hero'), copy = element('div', undefined, 'product-copy');
    var title = element('h1', item.title); title.id = 'report-title'; title.tabIndex = -1;
    copy.append(element('p', 'Checked on amazon.com', 'checked-on'), title);
    var checked = Date.parse(item.as_of), age = Date.now() - checked;
    if (Number.isSafeInteger(item.price_cents) && item.price_cents > 0 && age >= 0 && age <= 86400000) {
      copy.append(element('p', new Intl.NumberFormat('en-US', {style:'currency',currency:'USD'}).format(item.price_cents / 100), 'current-price'));
      var asOf = element('p', 'Price as of ', 'as-of'), time = element('time', new Intl.DateTimeFormat('en-US', {
        month:'short', day:'numeric', hour:'numeric', minute:'2-digit', timeZone:'America/New_York', timeZoneName:'short'
      }).format(new Date(checked)));
      time.dateTime = item.as_of; asOf.append(time); copy.append(asOf);
      var details = element('details', undefined, 'price-note');
      details.append(element('summary', 'Price details'), element('p', toolSettings.disclaimer)); copy.append(details);
    } else copy.append(element('p', 'See today’s price on Amazon.', 'as-of'));
    var grade = element('div', undefined, 'grade-box'); grade.dataset.grade = review.grade;
    grade.append(element('h2', 'Wowpriced review grade'), element('strong', review.grade, 'grade-letter'), element('p', review.label));
    hero.append(photo(item.image, item.title), copy, grade);
    report.append(line, hero, element('h2', '✓  The checks this product passed', 'blue-bar'),
      element('p', 'We show the checks this product passed.', 'checks-intro'));
    var table = element('table', undefined, 'checks-table'), tbody = element('tbody');
    var caption = element('caption', 'Passed review-signal checks', 'sr-only'); table.append(caption, tbody);
    var seen = new Set(), ids = ['plenty_of_reviews','highly_rated','steady_rating','natural_pace','popular_now'];
    review.checks.forEach(function (check) {
      if (!check || !ids.includes(check.id) || seen.has(check.id) ||
          !['title','result','detail'].every(k => typeof check[k] === 'string')) return;
      seen.add(check.id);
      var row = element('tr'), tick = element('td', '✓'), cell = element('td'); tick.setAttribute('aria-label', 'Passed');
      var details = element('details'); details.append(element('summary', 'What this means'), element('p', check.detail));
      cell.append(element('h3', check.title), element('p', check.result), details); row.append(tick, cell); tbody.append(row);
    });
    report.append(seen.size ? table : element('p', 'Read a few reviews to get to know this product and see how it fits your needs.', 'empty-checks'));
    var actions = element('div', undefined, 'report-actions');
    var amazon = link('Read the reviews on Amazon', toolAmazon(asin), 'button'); amazon.rel = 'sponsored noopener'; amazon.target = '_blank';
    actions.append(amazon);
    if (toolSettings.spotter) actions.append(link('Is the price right? → Deal Spotter', '/deal-spotter/?asin=' + asin, 'secondary'));
    var another = element('button', '← Check another product', 'report-another'); another.type = 'button';
    another.addEventListener('click', function () {
      report.hidden = true; start.hidden = false; history.replaceState(null, '', location.pathname); input.value = ''; input.focus();
    });
    report.append(actions, element('p', 'Review signals can be incomplete or older than today’s price. Always read a few reviews before you buy.', 'report-disclaimer'), another);
    start.hidden = true; report.hidden = false;
    history.replaceState(null, '', location.pathname + '?asin=' + asin);
    title.focus();
  }
  function submit(value, knownTitle) {
    var parsed = reviewInput(value);
    if (parsed.kind !== 'amazon') {
      message.textContent = parsed.kind === 'short' ? 'Open the short link first, then paste the full Amazon product address.' :
        'Paste an Amazon.com product link or a ten-character ASIN to get started.';
      input.setAttribute('aria-invalid', 'true'); input.focus(); return;
    }
    stop(false); input.removeAttribute('aria-invalid'); message.textContent = '';
    input.value = parsed.asin; opener = document.activeElement?.tagName === 'BODY' ? input : document.activeElement;
    var task = {asin:parsed.asin, controller:new AbortController(), started:performance.now(),
      duration:10000 + Math.random() * 15000, ready:false, data:null, lastStep:''};
    active = task;
    document.getElementById('loading-product').textContent = knownTitle || 'Your Amazon product';
    function tick() {
      if (active !== task) return;
      var elapsed = performance.now() - task.started, percent = reviewProgress(elapsed, task.duration, task.ready);
      var progress = document.getElementById('review-progress'); progress.setAttribute('aria-valuenow', percent);
      progress.firstElementChild.style.width = percent + '%'; document.getElementById('review-percent').textContent = percent + '%';
      var steps = ['Finding the product..', 'Reading rating signals..', 'Checking the review pace..', 'Grading..'];
      var step = steps[Math.min(3, Math.floor(percent / 25))];
      if (step !== task.lastStep) {document.getElementById('loading-step').textContent = step; task.lastStep = step;}
      if (percent === 100) {
        var data = task.data; stop(false);
        if (data?.reason) {
          message.textContent = ['rate_limited','limit_reached','budget_exhausted'].includes(data.reason) ?
            'Review Checker is busy right now. Please try again a little later.' :
            'We couldn’t complete this check just now. Please try again in a moment.';
          input.focus();
        } else showReport(data || {}, task.asin);
      } else if (elapsed >= task.duration + 10000) {
        stop(false); message.textContent = 'This check is taking a little longer. Please try again in a moment.'; input.focus();
      }
    }
    tick(); dialog.showModal(); document.getElementById('loading-close').focus();
    task.timer = setInterval(tick, 100);
    fetch('/api/search?asin=' + parsed.asin + '&grade=reviews', {signal:task.controller.signal, cache:'no-store',
      credentials:'omit', referrerPolicy:'no-referrer'}).then(function (response) {
        if (!response.ok) throw new Error('unavailable'); return response.json();
      }).then(function (data) {
        if (active !== task) return;
        task.data = data; task.ready = true;
        if (typeof data.items?.[0]?.title === 'string' && !reviewExcluded(data.items[0].title))
          document.getElementById('loading-product').textContent = data.items[0].title;
        tick();
      }).catch(function () {if (active === task) {task.data = {reason:'unavailable'}; task.ready = true; tick();}});
  }
  form.addEventListener('submit', function (event) {event.preventDefault(); submit(input.value);});
  document.querySelectorAll('[data-check-asin]').forEach(function (button) {
    button.addEventListener('click', function () {submit(button.dataset.checkAsin, button.dataset.title);});
  });
  document.getElementById('loading-close').addEventListener('click', function () {stop(true);});
  dialog.addEventListener('cancel', function (event) {event.preventDefault(); stop(true);});
  dialog.addEventListener('keydown', function (event) {
    if (event.key === 'Tab') {event.preventDefault(); document.getElementById('loading-close').focus();}
  });
  window.addEventListener('pagehide', function () {stop(false);});
  var pause = document.getElementById('demo-pause');
  pause.addEventListener('click', function () {
    var paused = pause.getAttribute('aria-pressed') !== 'true';
    pause.setAttribute('aria-pressed', String(paused)); pause.textContent = paused ? 'Play animation' : 'Pause animation';
    document.querySelector('.animation').classList.toggle('paused', paused);
  });
  document.getElementById('cookies-ok').addEventListener('click', function () {
    try {localStorage.setItem('wp.tools.cookies', '1');} catch (_) {}
    document.documentElement.classList.add('cookies-ok');
  });
  window.addEventListener('storage', function (event) {
    if (event.key === 'wp.tools.cookies' && event.newValue === '1') document.documentElement.classList.add('cookies-ok');
  });
  document.getElementById('watch-close').addEventListener('click', function () {
    try {sessionStorage.setItem('wp.review.watch', '1');} catch (_) {}
    document.documentElement.classList.add('watch-hidden');
  });
  document.querySelector('.watch-bar a').addEventListener('click', function () {
    start.hidden = false;
    document.getElementById('how-it-works').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth'});
  });
  var signup = document.querySelector('[data-join-footer]');
  signup.addEventListener('submit', async function (event) {
    event.preventDefault(); var button = signup.querySelector('button'), status = signup.querySelector('[data-join-message]');
    if (button.disabled || !signup.reportValidity()) return;
    button.disabled = true; status.hidden = true;
    try {
      if (!toolSettings.api) throw new Error('unavailable');
      var response = await fetch(toolSettings.api.replace(/\/+$/, '') + '/subscribe', {method:'POST',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:signup.querySelector('input').value.trim()}), referrerPolicy:'no-referrer'});
      var data = await response.json(); if (!response.ok || data.ok !== true) throw new Error('unavailable');
      signup.hidden = true; document.getElementById('footer-signup-success').hidden = false;
    } catch (_) {status.textContent = 'Sign-up is temporarily unavailable. Please try again.'; status.hidden = false;}
    finally {button.disabled = false;}
  });
  var params = new URLSearchParams(location.search), asin = params.get('asin');
  if (asin !== null) {
    if (params.getAll('asin').length === 1 && /^(?=.*\d)[a-z0-9]{10}$/i.test(asin)) submit(asin);
    else {message.textContent = 'Paste an Amazon.com product link or ASIN above to get started.'; input.focus();}
  }
})();
