/* Anonymous first-party click counts; no navigation changes or browser writes. */
(function () {
  "use strict";
  var config = document.querySelector("script[data-click-endpoint]");
  if (!config || !config.getAttribute("data-click-endpoint")) return;
  var endpoint = config.getAttribute("data-click-endpoint");
  function click(event) {
    try {
      if (event.defaultPrevented || event.isTrusted === false ||
          (event.type === "auxclick" ? event.button !== 1 : event.button !== 0)) return;
      if (navigator.globalPrivacyControl === true) return;
      // Recheck at each click so a same-tab opt-out takes effect immediately.
      try { if (localStorage.getItem("wp.adsOptOut") === "1") return; }
      catch (_) { return; } // Unknown privacy preference: fail closed.
      if (typeof navigator.sendBeacon !== "function") return;
      var link = event.target.closest("a.getdeal");
      if (!link || !link.getAttribute("href")) return;
      var card = link.closest(".card");
      if (card && (card.classList.contains("gone") || card.classList.contains("locked"))) return;
      var key = link.getAttribute("data-click-key") || (card && card.getAttribute("data-key"));
      if (!/^[0-9a-f]{16}$/.test(key || "")) return;
      var path = location.pathname;
      var home = path === "/" || path === "/index.html";
      var query = home && document.getElementById("q");
      var surface = home ? (query && query.value.trim() ? "search" : "wall") :
                    path.indexOf("/deal/") === 0 ? "deal_page" : "hub";
      var position = "detail";
      if (card) {
        var cards = document.querySelectorAll(".card");
        var n = Array.prototype.indexOf.call(cards, card) + 1;
        if (!n) return;
        position = n <= 8 ? "1-8" : n <= 30 ? "9-30" : n <= 60 ? "31-60" : n <= 120 ? "61-120" : "121+";
      }
      navigator.sendBeacon(endpoint, JSON.stringify({key: key, surface: surface,
        position: position, hour: Math.floor(Date.now() / 3600000)}));
    } catch (_) { /* Dropped counts never interfere with a shopper's click. */ }
  }
  document.addEventListener("click", click, {passive: true});
  document.addEventListener("auxclick", click, {passive: true});
}());
