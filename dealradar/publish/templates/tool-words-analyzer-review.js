  // tool_words_analyzer (operator 2026-10-23 ~20:05Z, ~20:10Z "just make them wait for it"): typed
  // words open the link's check box with the same wait, with steps about what is searched (no
  // review claim about one product), then the same words box. Still no lookup.
  var wordsCopy = 'Searching for deals, please wait..', linkCopy = document.getElementById('loading-copy').textContent;
  var wordsSteps = ['Searching our verified deals..', 'Finding discounts on Amazon..', 'Finding top-rated picks..'];
  dialog.addEventListener('close', function () {document.getElementById('loading-copy').textContent = linkCopy;});
  function analyzeWords(value) {
    stop(false); names.hidden = true;
    opener = document.activeElement?.tagName === 'BODY' ? input : document.activeElement;
    // stop() aborts a check's request; typed words make none.
    var task = {controller:{abort:function () {}}, started:performance.now(), duration:10000 + Math.random() * 15000, lastStep:''};
    active = task;
    document.getElementById('loading-product').textContent = names.dataset.words;
    document.getElementById('loading-copy').textContent = wordsCopy;
    function tick() {
      if (active !== task) return;
      var percent = reviewProgress(performance.now() - task.started, task.duration, true);
      var progress = document.getElementById('review-progress'); progress.setAttribute('aria-valuenow', percent);
      progress.firstElementChild.style.width = percent + '%'; document.getElementById('review-percent').textContent = percent + '%';
      var step = wordsSteps[Math.min(wordsSteps.length - 1, Math.floor(percent / 100 * wordsSteps.length))];
      if (step !== task.lastStep) {document.getElementById('loading-step').textContent = step; task.lastStep = step;}
      if (percent === 100) {stop(false); namePanel(names, value).focus();}
    }
    tick(); dialog.showModal(); document.getElementById('loading-close').focus();
    task.timer = setInterval(tick, 100);
  }
