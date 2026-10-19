  var pagePolishQuery = (function () {
    var url = new URL(location.href), params = url.searchParams;
    if (!params.has("q") && !params.has("analyze")) return null;
    var analyze = params.has("analyze");
    var task = {analyze:analyze, value:Array.from(params.get(analyze ? "analyze" : "q") || "").slice(0, 200).join("").trim()};
    params.delete("q");
    params.delete("analyze");
    history.replaceState(history.state, "", url.pathname + url.search + url.hash);
    return task;
  })();
  function applyPagePolishQuery() {
    if (!pagePolishQuery) return false;
    var task = pagePolishQuery;
    pagePolishQuery = null;
    browsePending = null;
    applyBrowseSnapshot({sort:"top"}, true);
    // Assign a DOM value, never interpolate a visitor's query into markup.
    document.getElementById("q").value = task.value;
    document.getElementById("clearsearch").hidden = !task.value;
    if (task.value && task.analyze && typeof analyzeSubmit === "function") analyzeSubmit();
    else updateSearch();
    return true;
  }
