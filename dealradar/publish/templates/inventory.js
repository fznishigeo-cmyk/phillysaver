(function () {
  "use strict";
  function click(e) {
    if ((e.type === "auxclick" && e.button !== 1) ||
        (e.type !== "auxclick" && e.button != null && e.button !== 0)) return;
    var target = e.target && (e.target.closest ? e.target : e.target.parentElement);
    if (!target || !target.closest) return;
    var link = target.closest("a[data-hub-page],a[data-product-family]");
    if (!link) return;
    var family = link.getAttribute("data-hub-page");
    var name = family ? "hub_page_click" : "product_family_click";
    var method = family || "deal";
    if (["category", "store", "price", "product_family", "deal"].indexOf(method) < 0) return;
    try { if (typeof window.wpTrack === "function") window.wpTrack(name, {method: method}); }
    catch (err) { /* Navigation works without analytics. */ }
  }
  document.addEventListener("click", click);
  document.addEventListener("auxclick", click);
})();
