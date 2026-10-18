  var searchMoreUI, searchMoreLoading;
  function searchSpellingState() { return searchMoreUI && searchMoreUI.spelling; }
  function searchMoreCall(method, first, second) {
    var q = searchAmazonQuery(), content = document.getElementById("content"), last = content.lastChild;
    if (!searchMoreUI && !q && method !== "paint") return Promise.resolve(false);
    if (!searchMoreLoading) searchMoreLoading = import("/search-more.js?v={{SEARCH_MORE_VERSION}}")
      .then(function (module) {
        return searchMoreUI = module.default({searchAmazonQuery, amazonResultsEnabled, amazonResultsSettings,
          getResultsQuery:function () { return amazonResultsQuery; }, queueAmazonResults, track, searchInventory,
          searchWords, oneSearchEdit, filtered, esc, amazonResultsCancel, updateSearch,
          analyzeCancel:typeof analyzeCancel === "function" ? analyzeCancel : null});
      }).catch(function () { return null; });
    return searchMoreLoading.then(function (ui) {
      if (!ui || q !== searchAmazonQuery() || (method === "mount" && last !== content.lastChild)) return false;
      return ui[method](first, second);
    });
  }
