function toolSimilar(item) {
  var task = toolActive, raw = task ? task.raw : searchText();
  var input = task ? task.input : analyzeInput(raw);
  function shorten(title) {
    var previous;
    do {
      previous = title;
      title = title.replace(/\([^()]*\)|\[[^\[\]]*\]/g, ' ');
    } while (title !== previous);
    return title.split(/,| - |\|/)[0]
      .trim().split(/\s+/).slice(0, 6).join(' ');
  }
  var link = input.kind === 'query' ? null : discountsLink();
  var words = input.kind === 'query' ? toolQuery(raw) :
    toolQuery(shorten(item.title || '')) || toolQuery(shorten(link && link.words || ''));
  if (analyzeAdult(raw) || analyzeAdult(item.title || '') || analyzeAdult(words)) words = '';
  return 'https://www.amazon.com/' + (words ? 's?k=' + encodeURIComponent(words) +
    '&rh=p_n_deal_type%3A23566065011&' : 'deals?') + 'tag=' + encodeURIComponent(toolSettings.tag);
}
// TOOL_QUERY_HANDOFF
// A single mount-time handoff: reload creates a fresh run; BFCache resumes its existing run.
var toolInitialQuery = new URLSearchParams(window.location.search).get('q');
if (toolInitialQuery && toolInitialQuery.length <= 300 && toolInitialQuery.trim()) {
  document.getElementById('q').value = toolInitialQuery;
  void toolSubmit({preventDefault:function () {}});
}
