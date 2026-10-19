  var freshUI = null, freshFailed = false;
  function freshView() { return !freshFailed && frontPageView() && state.sortKey === 'top' && !(typeof savedOn === 'function' && savedOn() && savedDeals.view); }
  requestAnimationFrame(function () { setTimeout(function () {
    import('/drop-fresh.js?v={{FRESH_VERSION}}').then(function (module) {
      freshUI = module.default({get state() { return state; }, freshView,
        dealKey, isLocked, dropPickIndex, reduceMotion, cardEls, render, rememberBrowse, renderTabs, finishBrowseAction});
      if (loadedOnce && freshView()) { render(); restoreInitialBrowse(); }
    }).catch(function () { var redraw = loadedOnce && freshView(); freshFailed = true; if (redraw) render(); });
  }, 0); });
