(function () {
  "use strict";
  // Static deals have explicit labels. Dynamic wall outbounds belong to
  // its existing listener, which also records local finds and saved clicks.
  var scope = document.getElementById("content") || document.querySelector("main");
  if (!scope) return;
  var path = location.pathname;
  var method = /^\/deal\/[^/]+\/$/.test(path) ? "deal"
    : /^\/stores\/[^/]+\/$/.test(path) ? "store"
    : /^\/deals\/under-(10|25|50)\/$/.test(path) ? "price"
    : /^\/deals\/[^/]+\/$/.test(path) ? "category"
    : path === "/best-deals-today/" ? "best_today" : "home";
  function track(name, params) {
    try { if (typeof window.wpTrack === "function") window.wpTrack(name, params); }
    catch (e) { /* analytics never stops navigation or copying */ }
  }
  if (scope.querySelector("[data-deal-detail]")) track("deal_detail_view", {method: "deal"});
  var clipboard = navigator.clipboard;
  scope.querySelectorAll("[data-copy-code]").forEach(function (button) {
    // The separate code text stays visible and selectable with or without JS.
    button.hidden = !(clipboard && typeof clipboard.writeText === "function");
  });
  var hideTimer;
  function message(text) {
    var status = scope.querySelector(".code-status") || document.getElementById("toast");
    if (!status) return;
    status.textContent = text;
    status.hidden = false;
    // The home toast is shared: it closes itself like every other notice.
    if (status.id === "toast") {
      clearTimeout(hideTimer);
      hideTimer = setTimeout(function () { status.hidden = true; }, 8000);
    }
  }
  function click(e) {
    if ((e.type === "auxclick" && e.button !== 1) ||
        (e.type !== "auxclick" && e.button != null && e.button !== 0)) return;
    var target = e.target.closest ? e.target : e.target.parentElement;
    if (!target || !target.closest) return;
    var button = target.closest("[data-copy-code], .card .code[data-code]");
    if (button && scope.contains(button) && e.type !== "auxclick") {
      var code = button.getAttribute("data-copy-code") || button.getAttribute("data-code");
      function failed() { message("Could not copy. Select the code to copy it: " + code); }
      try {
        navigator.clipboard.writeText(code).then(function () {
          message("Code copied: " + code);
          track("deal_code_copy", {method: method});
        }, failed);
      } catch (err) { failed(); }
      return;
    }
    var go = target.closest('a[data-deal-outbound][rel~="sponsored"]');
    if (go && scope.contains(go)) {
      track("deal_click", {store: go.getAttribute("data-store") || "",
        category: go.getAttribute("data-category") || "other", method: method});
    }
    // Modified clicks, middle clicks and keyboard activation keep native navigation.
  }
  scope.addEventListener("click", click);
  scope.addEventListener("auxclick", click);
})();
