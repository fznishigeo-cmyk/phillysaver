  function searchToolHref(tool) {
    var raw = document.getElementById("q").value;
    return "/amazon-" + tool + "/" + (raw.trim() ? "?q=" + encodeURIComponent(raw) : "");
  }
  // Offline logic harnesses stub a minimal document; real pages always have querySelectorAll.
  function toolLinks() {
    return typeof document.querySelectorAll == "function" ? document.querySelectorAll("[data-tool-link]") : [];
  }
  function searchToolLinks() {
    toolLinks().forEach(function (link) {
      link.href = searchToolHref(link.dataset.toolLink);
    });
  }
  document.getElementById("q").addEventListener("input", searchToolLinks);
  if (document.getElementById("q").form) document.getElementById("q").form.addEventListener("submit", searchToolLinks);
  toolLinks().forEach(function (link) {
    link.addEventListener("click", function () {
      searchToolLinks();
      track(link.dataset.toolLink === "deal-checker" ? "search_analyze" : "search_discounts", {to_tool:true});
    });
  });
  searchToolLinks();
