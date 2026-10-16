"use strict";
(async function () {
  const api = Wowpriced.api;
  const enabled = document.getElementById("enabled"), hide = document.getElementById("hide");
  const result = document.getElementById("result"), status = document.getElementById("status");
  let site = "", generation = 0, expiry = null;
  function clear() { clearTimeout(expiry); generation++; result.replaceChildren(); status.textContent = ""; }
  async function paint() {
    const settings = await api(chrome.storage.sync, "get", {enabled: true, hiddenSites: {}});
    enabled.checked = settings.enabled === true;
    hide.checked = !!settings.hiddenSites[site];
    clear();
    const list = document.getElementById("hidden-sites"); list.replaceChildren();
    for (const host of Object.keys(settings.hiddenSites).filter(h => settings.hiddenSites[h] === true)) {
      const b = document.createElement("button"); b.type = "button"; b.textContent = "Unhide " + host;
      b.addEventListener("click", async () => {
        const latest = await api(chrome.storage.sync, "get", {hiddenSites: {}});
        await api(chrome.storage.sync, "set", {hiddenSites: {...latest.hiddenSites, [host]: false}});
      });
      list.appendChild(b);
    }
  }
  await paint();
  enabled.addEventListener("change", async () => {
    clear(); await api(chrome.storage.sync, "set", {enabled: enabled.checked});
  });
  try {
    const tabs = await api(chrome.tabs, "query", {active: true, currentWindow: true});
    if (tabs[0]) {
      const response = await api(chrome.tabs, "sendMessage", tabs[0].id, {type: "site"});
      site = response.site || "";
    }
  } catch (_) { /* No content script on this tab. Paste-a-link still works. */ }
  if (site) {
    document.getElementById("site-row").hidden = false;
    document.getElementById("site-label").textContent = "Hide on " + site;
    await paint();
  }
  hide.addEventListener("change", async () => {
    const settings = await api(chrome.storage.sync, "get", {hiddenSites: {}});
    await api(chrome.storage.sync, "set", {hiddenSites: {...settings.hiddenSites, [site]: hide.checked}});
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "sync" && (changes.enabled || changes.hiddenSites)) paint().catch(() => {});
    if (area === "local" && changes.priceIndex) clear();
  });
  document.getElementById("check-form").addEventListener("submit", async event => {
    event.preventDefault(); clear(); const current = generation;
    try {
      const settings = await api(chrome.storage.sync, "get", {enabled: true});
      if (settings.enabled !== true) {status.textContent = "Turn on wowpriced to check a link."; return;}
      const rules = await api(chrome.runtime, "sendMessage", {type: "rules"});
      const aliases = Wowpriced.aliases(document.getElementById("product-url").value.trim(), rules);
      if (!aliases.length) {status.textContent = "Open short links first, then paste the full product address."; return;}
      status.textContent = "Checking…";
      for (const alias of aliases) {
        const model = await Wowpriced.lookup(alias, rules);
        if (current !== generation) return;
        if (model && model.expiresAt > Date.now()) {
          result.appendChild(Wowpriced.card(document, model)); status.textContent = "";
          expiry = setTimeout(clear, model.expiresAt - Date.now()); return;
        }
      }
      status.textContent = "We haven't verified a price for this product yet.";
    } catch (_) {if (current === generation) status.textContent = "Price check is unavailable. Try again later.";}
  });
})().catch(() => {document.getElementById("status").textContent = "Extension settings are unavailable.";});
