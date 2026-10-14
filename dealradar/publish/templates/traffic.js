(function () {
  "use strict";
  function track(name, method) {
    try { if (typeof window.wpTrack === "function") window.wpTrack(name, {method: method}); }
    catch (e) { /* measurement never prevents an action */ }
  }
  // Bounded campaign arrivals are page loads, not unique people or purchases.
  if (/^\/deal\/[^/]+\/$/.test(location.pathname)) {
    var query = new URLSearchParams(location.search);
    if (query.get("utm_campaign") === "deal") {
      if (query.get("utm_source") === "share" && query.get("utm_medium") === "referral") track("share_arrival", "deal");
      if (query.get("utm_source") === "feed" && query.get("utm_medium") === "rss") track("feed_arrival", "deal");
    }
  }
  document.querySelectorAll("[data-share]").forEach(function (box) {
    var native = box.querySelector("[data-share-native]");
    var copy = box.querySelector("[data-share-copy]");
    var manual = box.querySelector("[data-share-manual]");
    var input = box.querySelector("input"), status = box.querySelector("[data-share-status]");
    var canCopy = navigator.clipboard && typeof navigator.clipboard.writeText === "function";
    native.hidden = typeof navigator.share !== "function";
    copy.hidden = !canCopy;
    // The selectable URL stays available even when native sharing is the only API.
    manual.hidden = !!canCopy;
    function fallback() {
      manual.hidden = false;
      status.textContent = "Select and copy the deal link.";
      input.focus(); input.select();
    }
    copy.addEventListener("click", function () {
      copy.disabled = true;
      Promise.resolve().then(function () { return navigator.clipboard.writeText(input.value); }).then(function () {
        status.textContent = "Deal link copied.";
        track("deal_share", "copy");
      }, fallback).then(function () { copy.disabled = false; });
    });
    native.addEventListener("click", function () {
      native.disabled = true;
      function failed(e) {
        if (!e || e.name !== "AbortError") fallback();
      }
      // Call inside the user gesture: Web Share requires transient activation.
      try {
        navigator.share({title: box.getAttribute("data-share-title"), url: input.value}).then(function () {
          status.textContent = "Link handed to your sharing app.";
          track("deal_share", "native");
        }, failed).then(function () { native.disabled = false; });
      } catch (e) { failed(e); native.disabled = false; }
    });
  });
  function click(e) {
    if ((e.type === "auxclick" && e.button !== 1) ||
        (e.type !== "auxclick" && e.button != null && e.button !== 0)) return;
    var target = e.target.closest ? e.target : e.target.parentElement;
    if (!target || !target.closest) return;
    var feed = target.closest("a[data-feed-link]");
    if (feed) track("feed_open", "footer");
    var hub = target.closest("a[data-related-hub]");
    if (hub) track("related_hub_click", hub.getAttribute("data-related-hub"));
    // Preserve browser behavior, including modifier/middle clicks and Back.
  }
  document.addEventListener("click", click);
  document.addEventListener("auxclick", click);
})();
