// The visual clock is independent of real work, which starts in parallel.
function toolTiming(task) {
  task.duration = (10 + 15 * Math.random()) * 1000;
  return new Promise(resolve => {
    task.stopTiming = complete => {
      cancelAnimationFrame(task.frame);
      clearTimeout(task.deadline);
      resolve(complete);
    };
    function frame() {
      if (!toolCurrent(task)) return task.stopTiming(false);
      var elapsed = Math.max(0, performance.now() - task.started);
      var complete = elapsed >= task.duration && (task.ready || task.expired);
      var pct = complete ? 100 : elapsed >= task.duration ? 99 : elapsed / task.duration * 100;
      var progress = document.getElementById('tool-progress');
      progress.firstElementChild.style.width = pct + '%';
      progress.setAttribute('aria-valuenow', String(pct));
      for (var i = 0; i < task.steps.length; i++) {
        var done = elapsed >= task.duration * (i + 1) / task.steps.length &&
          (i < task.steps.length - 1 || complete);
        var waiting = i === task.steps.length - 1 && elapsed >= task.duration;
        var state = done ? 'done' : waiting ? 'pending' :
          i === 0 || task.steps[i - 1].dataset.state === 'done' ? 'working' : 'pending';
        if (task.steps[i].dataset.state !== state) {
          task.steps[i].dataset.state = state;
          if (state !== 'pending') document.getElementById('tool-status').textContent =
            task.steps[i].textContent + (done ? ' — done' : '');
        }
      }
      if (complete) return task.stopTiming(true);
      task.frame = requestAnimationFrame(frame);
    }
    task.deadline = setTimeout(() => {
      task.expired = true;
      if (task.controller) task.controller.abort();
      frame();
    }, task.duration + 10000);
    frame();
  });
}
// TOOL_SUBMIT_1020
async function toolCollect(task, finder) {
  var input = task.input, raw = task.raw, inventory = toolInventory();
  task.matches = [];
  task.sellers = [];
  try {
    if (finder) {
      var discount = toolDiscount(raw);
      task.discountUrl = discount.url;
      if (!discount.words && !discount.asin) throw Error(analyzeAdult(raw) ? 'excluded' :
        input.kind === 'query' ? 'invalid_query' : input.kind);
      task.words = discount.words;
      task.result = '<div><h2>' + esc(discount.words || 'Amazon product ' + discount.asin) + '</h2>' +
        toolButton(task.discountUrl, 'See Amazon discounts →', 'search_discounts') + '</div>';
      task.matches = (await inventory).filter(d => matchesSearch(d, searchWords(discount.words))).slice(0, 6);
    } else {
      if (input.kind !== 'query' && input.kind !== 'amazon') throw Error(input.kind);
      if (analyzeAdult(raw)) throw Error('excluded');
      task.words = input.kind === 'query' ? toolQuery(raw) : '';
      if (input.kind === 'query' && !task.words) throw Error('invalid_query');
      if (!toolSettings.live) throw Error('disabled');
      task.key = input.asin ? 'asin=' + input.asin : 'q=' + encodeURIComponent(task.words.toLowerCase().replace(/\s+/g, ' '));
      var data = await toolLookup(task);
      if (!toolCurrent(task) || task.expired) return;
      var items = analyzeItems(data.items || [], '');
      task.lead = input.asin ? items.find(d => d.asin === input.asin) : items[0];
      if (!task.lead) throw Error(data.reason || 'no_price');
      task.sellers = (input.asin ? items.filter(d => d.asin !== input.asin) : items).slice(0, 5);
      var rows = await inventory;
      task.matches = input.asin ? await analyzeCompare(rows, task.lead.title, input.asin, null) :
        rows.filter(d => matchesSearch(d, searchWords(task.words))).slice(0, 6);
    }
  } catch (error) {
    task.reason = error.message;
    if (!finder && input.kind === 'query' && task.words && !analyzeAdult(raw))
      task.matches = (await inventory).filter(d => matchesSearch(d, searchWords(task.words))).slice(0, 6);
  } finally { task.ready = true; }
}
async function toolSubmit(event) {
  event.preventDefault();
  var raw = searchText();
  if (!raw || toolActive && !toolActive.finished && toolActive.raw === raw) return;
  toolCancel();
  var finder = document.body.dataset.tool === 'finder';
  var task = {raw:raw, input:analyzeInput(raw), started:performance.now(),
    steps:Array.from(document.querySelectorAll('.steps li'))};
  toolActive = task;
  for (var step of task.steps) step.dataset.state = 'pending';
  for (var id of ['tool-result', 'top-sellers', 'verified-deals']) document.getElementById(id).hidden = true;
  var analyzer = document.getElementById('analyzer');
  analyzer.hidden = false; analyzer.setAttribute('aria-busy', 'true');
  document.getElementById('analyzer-title').textContent = finder ? 'Finding discounts for your product' : 'Analyzing your product';
  document.getElementById('tool-status').focus({preventScroll:true});
  track(finder ? 'search_discounts' : 'analyze');
  if (!finder) track('search_analyze', {link:task.input.kind !== 'query'});
  var timing = toolTiming(task);
  void toolCollect(task, finder);
  if (!await timing || !toolCurrent(task)) return;
  if (task.lead || finder && task.result) {
    var result = document.getElementById('tool-result');
    result.innerHTML = task.lead ? toolLiveCard(task.lead, true) : task.result;
    result.hidden = false;
    var top = document.getElementById('top-sellers');
    top.querySelector('.product-grid').innerHTML = task.sellers.map(d => toolLiveCard(d, false)).join('');
    top.hidden = !task.sellers.length;
    if (!finder) track('analyze_result', {count:Math.min(16, 1 + task.sellers.length + task.matches.length), live:true, matched:!!task.matches.length});
  } else {
    toolFailure(task, task.reason || 'timeout');
    task.steps.at(-1).dataset.state = 'failed';
    document.getElementById('tool-status').textContent = analyzeReason(task.reason || 'timeout');
  }
  toolPaintDeals(task.matches);
  task.finished = true;
  analyzer.setAttribute('aria-busy', 'false');
}
