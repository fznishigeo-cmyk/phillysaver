"use strict";
(function () {
  let href = location.href;
  let host = "", card = null, dismissed = false, generation = 0, expiry = null;
  function remove() { clearTimeout(expiry); if (card) card.remove(); card = null; }
  async function refresh() {
    const current = ++generation;
    remove();
    const settings = await Wowpriced.api(chrome.storage.sync, "get", {enabled: true, hiddenSites: {}});
    if (settings.enabled !== true || dismissed) return;
    const rules = await Wowpriced.api(chrome.runtime, "sendMessage", {type: "rules"});
    if (!rules) return;
    host = Wowpriced.site(href, rules);
    if (settings.hiddenSites[host]) return;
    for (const alias of Wowpriced.aliases(href, rules)) {
      if (alias.startsWith("url:")) continue; // Automatic cards require an exact store product ID.
      const model = await Wowpriced.lookup(alias, rules, host);
      if (current !== generation || dismissed) return;
      if (!model) continue;
      if (model.expiresAt <= Date.now()) return;
      const wrapper = document.createElement("div");
      wrapper.style.cssText = "all:initial!important;position:fixed!important;bottom:20px!important;right:20px!important;width:340px!important;max-width:calc(100vw - 40px)!important;z-index:2147483647!important";
      const shadow = wrapper.attachShadow({mode: "closed"});
      shadow.appendChild(Wowpriced.card(document, model, () => {dismissed = true; remove();}, async () => {
        generation++; remove();
        const latest = await Wowpriced.api(chrome.storage.sync, "get", {hiddenSites: {}});
        await Wowpriced.api(chrome.storage.sync, "set", {hiddenSites: {...latest.hiddenSites, [host]: true}});
      }));
      card = wrapper; document.documentElement.appendChild(wrapper);
      expiry = setTimeout(remove, model.expiresAt - Date.now()); return;
    }
  }
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id === chrome.runtime.id && message && message.type === "site") {
      // The popup asks for the URL's hostname only; no tab URL permission.
      try { respond({site: new URL(href).hostname}); } catch (_) { respond({site: ""}); }
    }
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if ((area === "sync" && (changes.enabled || changes.hiddenSites)) ||
        (area === "local" && changes.priceIndex)) refresh().catch(remove);
  });
  // Retailers switch products with history.pushState/replaceState; content scripts cannot hook those.
  function moved() {
    if (location.href === href) return;
    href = location.href; dismissed = false; refresh().catch(remove);
  }
  addEventListener("popstate", moved);
  setInterval(moved, 1000);
  refresh().catch(remove);
})();
