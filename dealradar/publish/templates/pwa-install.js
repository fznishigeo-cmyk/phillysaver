(function () {
  "use strict";
  var mode = window.matchMedia("(display-mode: standalone)");
  var deferred = null, installed = false, accepted = false, recorded = false, offered = false;
  var busy = false, sessionRecorded = false, actions, button, ios, header, help, opener;
  var key = "wp.install.dismissedUntil", week = 7 * 24 * 60 * 60 * 1000, dismissedUntil = 0;
  try { dismissedUntil = Number(localStorage.getItem(key)) || 0; } catch (e) {}
  // Operator 2026-10-17: on phones the header control waits until the visitor has spent
  // a few minutes on wowpriced (visible time, summed across visits); desktop shows it at once.
  var engagedKey = "wp.install.engagedMs", phoneDelay = 180000, engagedMs = 0, engagedTimer = null;
  try { engagedMs = Number(localStorage.getItem(engagedKey)) || 0; } catch (e) {}
  function phoneWaiting() { return window.innerWidth > 0 && window.innerWidth <= 700 && engagedMs < phoneDelay; }
  function countEngagement() {
    if (!phoneWaiting() || engagedTimer) return;
    engagedTimer = setInterval(function () {
      if (document.visibilityState && document.visibilityState !== "visible") return;
      engagedMs += 15000;
      try { localStorage.setItem(engagedKey, String(engagedMs)); } catch (e) {}
      if (!phoneWaiting()) { clearInterval(engagedTimer); engagedTimer = null; update(); }
    }, 15000);
  }
  function standalone() { return mode.matches || navigator.standalone === true; }
  function blocked() {
    if (installed || standalone() || /FBAN|FBAV|Instagram/i.test(navigator.userAgent || "")) return true;
    if (document.documentElement.classList.contains("in-app") ||
        new URLSearchParams(location.search).getAll("src").indexOf("twa") !== -1 ||
        /^android-app:\/\//.test(document.referrer)) return true;
    try { return sessionStorage.getItem("wp_app") === "android"; } catch (e) { return false; }
  }
  function track(name) {
    try { if (typeof window.wpTrack === "function") window.wpTrack(name, {}); } catch (e) {}
  }
  function standaloneSession() {
    if (!standalone() || sessionRecorded) return;
    sessionRecorded = true;
    try {
      if (sessionStorage.getItem("wp.standalone.session") === "1") return;
      sessionStorage.setItem("wp.standalone.session", "1");
    } catch (e) {}
    track("standalone_session");
  }
  function iosSafari() {
    var ua = navigator.userAgent || "";
    var apple = /iPad|iPhone|iPod/.test(ua) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
    return apple && /Version\/[\d.]+.*Safari\//.test(ua) &&
      !/CriOS|FxiOS|EdgiOS|OPiOS|GSA|DuckDuckGo|FBAN|FBAV|Instagram/i.test(ua);
  }
  function closeHelp() {
    if (!help) return;
    if (help.open) help.close();
    help.hidden = true;
    if (opener && !opener.hidden && !blocked()) opener.focus();
    opener = null;
  }
  function update() {
    var hide = blocked() || busy || dismissedUntil > Date.now();
    if (button) button.hidden = hide || !deferred;
    if (ios) ios.hidden = hide || !iosSafari();
    if (actions) actions.hidden = button.hidden && ios.hidden;
    if (header) header.hidden = hide || phoneWaiting() || (!deferred && !iosSafari());
    if (hide) closeHelp();
    if (!offered && ((header && !header.hidden) || (actions && !actions.hidden))) {
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
  function dismiss() {
    dismissedUntil = Date.now() + week;
    try { localStorage.setItem(key, String(dismissedUntil)); } catch (e) {}
    track("install_dismissed");
  }
  function install(event) {
    event.preventDefault();
    if (blocked() || busy || dismissedUntil > Date.now()) return;
    if (!deferred) {
      if (!iosSafari() || !help) return;
      track("install_click");
      opener = event.currentTarget;
      help.hidden = false;
      help.showModal();
      document.getElementById("pwa-help-close").focus();
      return;
    }
    var prompt = deferred;
    deferred = null;
    busy = true;
    track("install_click");
    update();
    function finish(choice) {
      busy = false;
      if (choice && choice.outcome === "accepted") accept();
      else if (!installed) dismiss();
      update();
    }
    try {
      Promise.resolve(prompt.prompt()).then(function () { return prompt.userChoice; })
        .then(finish).catch(function () { finish(null); });
    } catch (e) { finish(null); }
  }
  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();
    if (blocked() || busy || dismissedUntil > Date.now() || deferred) return;
    deferred = event;
    update();
  });
  window.addEventListener("appinstalled", function () {
    accept();
    if (!recorded) { recorded = true; track("installed"); }
  });
  if (typeof mode.addEventListener === "function") mode.addEventListener("change", update);
  function ready() {
    actions = document.getElementById("pwa-actions");
    button = document.getElementById("pwa-install");
    ios = document.getElementById("pwa-ios");
    header = document.getElementById("pwa-header-install");
    help = document.getElementById("pwa-help");
    [button, ios, header].forEach(function (control) {
      if (control) control.addEventListener("click", install);
    });
    if (help) {
      document.getElementById("pwa-help-close").addEventListener("click", closeHelp);
      help.addEventListener("keydown", function (event) {
        if (!help.open || event.key !== "Tab") return;
        // The close button is the only tab stop; keep both directions in the sheet.
        event.preventDefault();
        document.getElementById("pwa-help-close").focus();
      });
      help.addEventListener("cancel", function (event) { event.preventDefault(); closeHelp(); });
      help.addEventListener("click", function (event) { if (event.target === help) closeHelp(); });
    }
    window.addEventListener("storage", function (event) {
      if (event.key === key) { dismissedUntil = Number(event.newValue) || 0; update(); }
    });
    countEngagement();
    update();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready, {once: true});
  else ready();
})();
