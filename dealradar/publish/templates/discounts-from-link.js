  function discountsLink() {
    var path = document.getElementById("q").value.trim().match(/^(?:https?:\/\/|\/\/)?(?:(?:www|smile|m)\.)?amazon\.com\/(?:([^/?#\\\s]+)\/)?(?:dp|gp\/product)\/([a-z0-9]{10})(?:[/?#]|$)/i);
    if (!path || (typeof wpAndroidLimited === "function" && wpAndroidLimited())) return null;
    try {
      var asin = path[2].toUpperCase();
      var clean = value => value.replace(/[-_]/g, " ").split(/\s+/).filter(word =>
        word.toUpperCase() !== asin && !/^(?:B0[a-z0-9]{8}|\d{10}|ref(?:=.*)?)$/i.test(word)).join(" ").trim();
      var words = clean(decodeURIComponent(path[1] || ""));
      var task = typeof analyzeTask !== "undefined" && analyzeTask;
      if (!words && task && task.finished && !task.reason) {
        var item = task.items?.find(d => d.asin === asin);
        words = clean(task.asin === asin && task.local ? task.local.item : item?.title || "");
      }
      return {words, asin:words ? "" : asin};
    } catch (_) { return null; }
  }
