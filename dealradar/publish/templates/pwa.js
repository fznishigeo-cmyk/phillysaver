(function () {
  "use strict";
  var mode = window.matchMedia("(display-mode: standalone)");
  var deferred = null, installed = false, offered = false, accepted = false, prompted = false;
  var sessionRecorded = false, actions, button, ios, help;
  function standalone() { return mode.matches || navigator.standalone === true; }
  function track(name) {
    try { if (typeof window.wpTrack === "function") window.wpTrack(name, {}); }
    catch (e) { /* analytics never stops installation */ }
  }
  function standaloneSession() {
    if (!standalone() || sessionRecorded) return;
    sessionRecorded = true;
    try {
      if (sessionStorage.getItem("wp.standalone.session") === "1") return;
      sessionStorage.setItem("wp.standalone.session", "1");
    } catch (e) { /* blocked storage: once in this page */ }
    track("standalone_session");
  }
  function iosSafari() {
    var ua = navigator.userAgent || "";
    var apple = /iPad|iPhone|iPod/.test(ua) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    return apple && /Version\/[\d.]+.*Safari\//.test(ua) &&
      !/CriOS|FxiOS|EdgiOS|OPiOS|GSA|DuckDuckGo|FBAN|FBAV|Instagram/.test(ua);
  }
  function update() {
    if (!actions) return;
    var hide = installed || standalone();
    button.hidden = hide || !deferred || prompted;
    ios.hidden = hide || !iosSafari();
    actions.hidden = button.hidden && ios.hidden;
    if (hide) help.hidden = true;
    if (!actions.hidden && !offered) {
      offered = true;
      track("install_offer_shown");
    }
    standaloneSession();
  }
  function accept() {
    installed = true;
    deferred = null;
    update();
    if (!accepted) { accepted = true; track("install_accepted"); }
  }
  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    if (installed || standalone() || prompted || deferred) return;
    deferred = event;
    update();
  });
  window.addEventListener("appinstalled", accept);
  if (typeof mode.addEventListener === "function") mode.addEventListener("change", update);
  function ready() {
    actions = document.getElementById("pwa-actions");
    button = document.getElementById("pwa-install");
    ios = document.getElementById("pwa-ios");
    help = document.getElementById("pwa-help");
    if (!actions) { standaloneSession(); return; }
    button.addEventListener("click", function () {
      if (!deferred || installed || standalone() || prompted) return;
      var prompt = deferred;
      deferred = null;
      prompted = true;
      update();
      try {
        Promise.resolve(prompt.prompt()).then(function () { return prompt.userChoice; })
          .then(function (choice) { if (choice && choice.outcome === "accepted") accept(); })
          .catch(function () { /* browser dismissed or unavailable */ });
      } catch (e) { /* browser prompt unavailable */ }
    });
    ios.addEventListener("click", function (event) {
      if (installed || standalone() || !iosSafari()) { event.preventDefault(); return; }
      help.hidden = false;
      help.focus();
    });
    document.getElementById("pwa-help-close").addEventListener("click", function () {
      help.hidden = true;
      ios.focus();
    });
    update();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready, {once: true});
  else ready();
})();
