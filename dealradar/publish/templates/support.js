"use strict";
(function () {
  var meta = {}, dismissed = false, queued = false;
  var KEY = "wp.support.dismissed", WEEK = 7 * 24 * 60 * 60 * 1000;
  var panel = document.getElementById("paywall");
  try { dismissed = Number(localStorage.getItem(KEY)) > Date.now(); }
  catch (e) { /* storage blocked: dismissal lasts for this page view */ }
  function track(name, params) {
    try { if (typeof window.wpTrack === "function") window.wpTrack(name, params); }
    catch (e) { /* never in the way */ }
  }
  function fromHash() {
    if (["#join", "#signup", "#login"].includes(location.hash.toLowerCase())) location.replace("/join");
  }
  function refresh(m) {
    if (m) meta = m;
    var link = String((meta.support || {}).link || "");
    if (!/^https:\/\/buy\.stripe\.com\/[^\s<>"'\\]+$/.test(link)) link = "";
    document.querySelectorAll("[data-support]").forEach(function (el) {
      el.hidden = !link;
      if (link) el.href = link;
    });
    var monthly = String((meta.support || {}).monthly_link || "");
    if (!link || !/^https:\/\/buy\.stripe\.com\/[^\s<>"'\\]+$/.test(monthly)) monthly = "";
    document.querySelectorAll("[data-support-monthly]").forEach(function (el) {
      el.hidden = !monthly;
      if (monthly) el.href = monthly;
    });
    document.querySelectorAll("[data-support-note]").forEach(function (el) { el.hidden = !link; });
    if (!panel) return;
    var cards = document.querySelectorAll("#content .card:not(.gone)[data-key]");
    var header = document.querySelector("header");
    // The header is sticky on desktop and scrolls away on phones: measure
    // from the viewport top, or the header's bottom while it is on screen.
    var edge = header ? Math.max(0, header.getBoundingClientRect().bottom) : 0;
    panel.hidden = dismissed || cards.length <= 8 || cards[7].getBoundingClientRect().bottom > edge;
    var bar = document.getElementById("subbar");
    if (bar) bar.hidden = !panel.hidden;
  }
  document.addEventListener("click", function (e) {
    if (e.target.closest("[data-support]")) track("support_click", {plan: "once"});
    if (e.target.closest("[data-support-monthly]")) track("support_click", {plan: "monthly"});
    if (e.target.closest("[data-support-close]")) {
      dismissed = true;
      try { localStorage.setItem(KEY, String(Date.now() + WEEK)); }
      catch (err) { /* storage blocked: still hide for this page view */ }
      refresh();
    }
  });
  document.addEventListener("submit", function (e) {
    var el = e.target;
    if (!el.matches(".joinform")) return;
    e.preventDefault();
    var input = el.querySelector('input[type="email"]'), btn = el.querySelector('button[type="submit"]');
    var msg = el.querySelector("[data-join-message]");
    function say(text, error) { msg.textContent = text; msg.hidden = false; msg.setAttribute("role", error ? "alert" : "status"); }
    var api = String(meta.api_url || "").replace(/\/+$/, "");
    if (!api) { say("Sign-up is unavailable right now. Please try again soon.", true); return; }
    btn.disabled = true;
    msg.hidden = true;
    fetch(api + "/subscribe", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({email: input.value.trim()})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 200 && j.ok === true) {
          say("You're on the list. When our deals email starts, we'll send you one email to confirm first.", false);
          track("sign_up", {method: "email"});
        } else {
          say(r.status === 429 ? "Too many tries. Please try again in an hour."
            : r.status === 400 ? "Please enter a valid email address."
            : "Sign-up is unavailable right now. Please try again soon.", true);
        }
      });
    }).catch(function () { say("Couldn't reach the server. Please check your connection and try again.", true); })
      .then(function () { btn.disabled = false; });
  });
  window.addEventListener("scroll", function () {
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () { queued = false; refresh(); });
  }, {passive: true});
  window.addEventListener("resize", function () { refresh(); });
  window.addEventListener("hashchange", fromHash);
  window.wpSupport = {fromHash: fromHash, refresh: refresh};
  fromHash();
  if (!document.getElementById("content")) {
    fetch("/deals.json?t=" + Date.now(), {cache: "no-store"})
      .then(function (r) { return r.json(); }).then(function (d) { refresh(d.meta || {}); }).catch(function () {});
  }
})();
