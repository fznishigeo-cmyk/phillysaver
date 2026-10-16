function wpAndroidContext() {
  var prefix = "android-app://" + {{PACKAGE}};
  var app = new URLSearchParams(location.search).getAll("src").indexOf("twa") !== -1 ||
    document.referrer === prefix || document.referrer.indexOf(prefix + "/") === 0;
  try {
    if (app) sessionStorage.setItem("wp_app", "android");
    app = app || sessionStorage.getItem("wp_app") === "android";
  } catch (e) { /* detection still works for this page when storage is blocked */ }
  if (app) document.documentElement.classList.add("in-app");
  if ({{LIMITED}}) document.documentElement.classList.add("app-amazon-limited");
  return app;
}
function wpAndroidApp() { return document.documentElement.classList.contains("in-app"); }
function wpAndroidLimited() { return wpAndroidApp() && {{LIMITED}}; }
if (wpAndroidContext()) {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(function () {});
  // One delegate also covers links inserted by the wall. HTML hrefs stay intact.
  function untag(event) {
    if (!wpAndroidLimited()) return;
    var link = event.target.closest && event.target.closest("a[href]");
    if (!link) return;
    try {
      var url = new URL(link.href, location.href);
      if ((url.protocol === "https:" || url.protocol === "http:") &&
          new RegExp({{AMAZON_HOST}}).test(url.hostname)) {
        url.searchParams.delete("tag");
        link.href = url.href;
      }
    } catch (e) { /* leave malformed links to the browser */ }
  }
  document.addEventListener("click", untag, true);
  document.addEventListener("auxclick", untag, true);
  if (wpAndroidLimited()) {
    function markCards() {
      document.querySelectorAll(".card").forEach(function (card) {
        var store = card.querySelector(".storechip");
        var links = Array.from(card.querySelectorAll("a[href]"));
        if ((store && /amazon/i.test(store.textContent)) || links.some(function (link) {
          try { return new RegExp({{AMAZON_HOST}}).test(new URL(link.href, location.href).hostname); }
          catch (e) { return false; }
        })) card.setAttribute("data-app-amazon", "");
      });
    }
    document.addEventListener("DOMContentLoaded", function () {
      markCards();
      new MutationObserver(function (records) {
        if (records.some(function (record) {
          return Array.from(record.addedNodes).some(function (node) {
            return node.nodeType === 1 && (node.matches(".card") || node.querySelector(".card"));
          });
        })) markCards();
      }).observe(document.body, {childList:true, subtree:true});
    }, {once:true});
  }
}
