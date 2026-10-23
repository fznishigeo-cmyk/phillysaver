  // tool_words_analyzer (operator 2026-10-23 ~20:05Z, ~20:10Z "just make them wait for it"): typed
  // words open the link's analyzer box with the same wait, with steps about what is searched (no
  // price claim about one product), then the same words box. Still no lookup.
  var wordsStatus = 'Searching for deals, please wait..', linkStatus = $('modal-status').textContent;
  var wordsSteps = ['Searching our verified deals..', 'Finding discounts on Amazon..', 'Finding top-rated picks..'];
  modal.addEventListener('close', () => { $('modal-status').textContent = linkStatus; });
  function analyzeWords(raw, field, opener) {
    var panel = $('name-results'), task = {raw, field, opener:opener || field, started:performance.now(),
      duration:10000 + Math.random() * 15000, cancelled:false};
    active = task; panel.hidden = true;
    $('modal-product').textContent = panel.dataset.words; $('modal-status').textContent = wordsStatus;
    function tick() {
      if (!current(task)) return;
      var percent = Math.min(100, (performance.now() - task.started) / task.duration * 100);
      $('modal-bar').style.width = percent + '%';
      $('modal-percent').textContent = percent.toFixed(1) + '%';
      modal.querySelector('[role="progressbar"]').setAttribute('aria-valuenow', String(Math.floor(percent)));
      var step = wordsSteps[Math.min(wordsSteps.length - 1, Math.floor(percent / 100 * wordsSteps.length))];
      if ($('modal-step').textContent !== step) $('modal-step').textContent = step;
      if (percent === 100) { cancel(); namePanel(panel, raw).focus(); }
    }
    tick(); installSync(); modal.showModal(); document.body.style.overflow = 'hidden'; $('cancel-analysis').focus();
    task.clock = setInterval(tick, 100);
  }
