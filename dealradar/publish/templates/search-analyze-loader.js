  var analyzeUI, analyzeLoading, analyzePending = null, analyzeFailed = false;
  var analyzeTask = null, analyzeQueryPromise;
  function analyzeInput(value) {
    if (analyzeUI) return analyzeUI.input(value);
    // Mask links before loading too; extraction belongs to the module.
    return {kind:/^(?:[a-z][a-z0-9+.-]*:|\/\/|[^\s/]+\.[a-z]{2,}(?:[/:?#]|$))|https?:\/\/|www\.|\b(?:amazon\.com|amzn\.to|a\.co)\//i.test(String(value || "").trim()) ? "link" : "query"};
  }
  function analyzeSearchText() {
    var value = document.getElementById("q").value.trim();
    return analyzeInput(value).kind === "query" ? value : "";
  }
  function analyzeCurrent(task) { return !!analyzeUI && analyzeUI.current(task); }
  function analyzeRender() { return !!analyzeUI && analyzeUI.render(); }
  function analyzeCancel() {
    analyzePending = null;
    if (analyzeUI) analyzeUI.cancel();
  }
  function analyzeLoad() {
    if (!analyzeLoading) analyzeLoading = import("/search-analyze.js?v={{ANALYZE_VERSION}}")
      .then(function (module) {
        analyzeUI = module.default({
          get analyzeTask() { return analyzeTask; }, set analyzeTask(value) { analyzeTask = value; },
          get analyzeQueryPromise() { return analyzeQueryPromise; },
          get amazonResultsQuery() { return amazonResultsQuery; }, set amazonResultsQuery(value) { amazonResultsQuery = value; },
          get amazonResultsRequest() { return amazonResultsRequest; },
          get amazonResultsTimer() { return amazonResultsTimer; },
          get searchTimer() { return searchTimer; }, get loadedOnce() { return loadedOnce; },
          searchAmazonQuery, amazonResultsEnabled, amazonResultsSettings, searchInventory, filtered,
          track, esc, cardHtml, dealKey, searchWords, updateSearch, amazonResultsSeen, amazonResultsCancel,
          wpAndroidLimited:function () { return typeof wpAndroidLimited === "function" && wpAndroidLimited(); },
          {{ANALYZE_EXTRA}}
        });
        var pending = analyzePending;
        analyzePending = null;
        if (pending !== null && pending === document.getElementById("q").value) return analyzeUI.submit();
      }).catch(function () {
        analyzePending = null; analyzeFailed = true;
        updateSearch();
      });
    return analyzeLoading;
  }
  function analyzeSubmit() {
    clearTimeout(searchTimer);
    if (analyzeUI) return analyzeUI.submit();
    if (analyzeFailed) { updateSearch(); return; }
    analyzePending = document.getElementById("q").value;
    return analyzeLoad();
  }
  var analyzeBox = document.getElementById("q"), analyzeButton = document.getElementById("analyze-search");
  if (analyzeBox) ["focus", "input", "paste"].forEach(function (type) {
    analyzeBox.addEventListener(type, function () {
      if (type === "input" && analyzePending !== this.value) analyzePending = null;
      analyzeLoad();
    });
  });
  if (analyzeButton) analyzeButton.addEventListener("click", analyzeSubmit);
