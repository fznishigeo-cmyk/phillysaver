"use strict";
(function () {
  var meta = {}, dismissed = false, queued = false;
  var KEY = "wp.support.dismissed", WEEK = 7 * 24 * 60 * 60 * 1000;
  var panel = document.getElementById("paywall");
  var prompt = document.getElementById("join-prompt"), started = Date.now();
  var SHOWN = "wp.join.promptUntil", JOINED = "wp.join.joined";
  var engaged = false, shown = false, joined = false, timer = null, previousFocus;
  var googleLoading = false, googleBusy = false;
  var onJoinPage = /^\/join(?:\.html)?\/?$/.test(location.pathname);
  function promptOpen() { return prompt && !prompt.hidden; }
  function rememberJoin(method) {
    joined = true;
    try { localStorage.setItem(JOINED, "1"); } catch (e) { /* remembered for this page */ }
    track("sign_up", {method: method});
  }
  function closePrompt() {
    if (!promptOpen()) return;
    prompt.hidden = true;
    track("signup_prompt_closed");
    if (previousFocus && previousFocus.isConnected) previousFocus.focus({preventScroll: true});
    refresh();
  }
  function maybePrompt() {
    if (!prompt || onJoinPage || promptOpen() || shown || joined || document.hidden ||
        !(meta.join || {}).prompt || !meta.api_url) return;
    try {
      if (localStorage.getItem(JOINED) || Number(localStorage.getItem(SHOWN)) > Date.now()) return;
    } catch (e) { return; } // no durable frequency limit: no automatic prompt
    var delay = Math.max(15, Number(meta.join.prompt_delay_s) || 15) * 1000;
    var remaining = delay - (Date.now() - started);
    if (remaining > 0) {
      if (timer === null) timer = setTimeout(function () { timer = null; refresh(); }, remaining);
      return;
    }
    if (!engaged) return;
    try {
      var until = String(Date.now() + WEEK);
      localStorage.setItem(SHOWN, until);
      if (localStorage.getItem(SHOWN) !== until) return;
    } catch (e) { return; }
    shown = true;
    previousFocus = document.activeElement;
    prompt.hidden = false;
    // The weekly prompt takes the banner's place: one ask per page view.
    if (panel) panel.hidden = true;
    document.getElementById("join-email-choice").focus({preventScroll: true});
    var bar = document.getElementById("subbar");
    if (bar) bar.hidden = true;
    track("signup_prompt_shown");
    loadGoogle();
  }
  function loadGoogle() {
    var box = document.getElementById("join-google");
    if (!box || googleLoading || !meta.google_client_id || !meta.api_url) return;
    googleLoading = true;
    var script = document.createElement("script");
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.onload = function () {
      var g = window.google && window.google.accounts && window.google.accounts.id;
      if (!g) return;
      try {
        g.initialize({client_id: meta.google_client_id, auto_select: false, callback: googleJoin});
        g.renderButton(box, {theme: "filled_black", size: "large", text: "continue_with",
          shape: "rectangular", width: Math.min(400, document.getElementById("join-email-choice").clientWidth ||
            document.getElementById("join-email-fields").clientWidth || 280)});
        box.hidden = false;
      } catch (e) { box.hidden = true; }
    };
    document.head.appendChild(script);
  }
  function googleJoin(response) {
    if (!response || !response.credential || googleBusy) return;
    var msg = document.getElementById("join-google-message");
    var api = String(meta.api_url || "").replace(/\/+$/, "");
    googleBusy = true;
    msg.hidden = false;
    msg.textContent = "Joining…";
    msg.setAttribute("role", "status");
    // Existing auth verifies the token and returns a free account. Never
    // start checkout or retain the membership session for a newsletter join.
    fetch(api + "/api/google", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({credential: response.credential})})
      .then(function (r) {
        if (r.status !== 200) throw new Error("google");
        return r.json();
      }).then(function (j) {
        if (!j.me || j.me.email_verified !== true || !j.me.email) throw new Error("unverified");
        return fetch(api + "/subscribe", {method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({email: j.me.email})});
      }).then(function (r) {
        if (r.status !== 200) throw new Error("subscribe");
        return r.json();
      }).then(function (j) {
        if (j.ok !== true) throw new Error("subscribe");
        msg.textContent = "You're on the list. When our deals email starts, we'll send you one email to confirm first.";
        rememberJoin("google");
      }).catch(function () {
        msg.textContent = "Google sign-up is unavailable right now. Please continue with email or try again.";
        msg.setAttribute("role", "alert");
      }).then(function () { googleBusy = false; });
  }
  try { dismissed = Number(localStorage.getItem(KEY)) > Date.now(); }
  catch (e) { /* storage blocked: dismissal lasts for this page view */ }
  function track(name, params) {
    try { if (typeof window.wpTrack === "function") window.wpTrack(name, params); }
    catch (e) { /* never in the way */ }
  }
  function fromHash() {
    if (["#join", "#signup", "#login"].includes(location.hash.toLowerCase())) location.replace("/join");
  }
  function inView(el, edge) {
    if (!el || el.hidden) return false;
    var rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.bottom > (edge || 0) &&
      rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth;
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
    if (onJoinPage) loadGoogle();
    if (!panel) { maybePrompt(); return; }
    var cards = document.querySelectorAll("#content .card:not(.gone)[data-key]");
    var header = document.querySelector("header");
    // The header is sticky on desktop and scrolls away on phones: measure
    // from the viewport top, or the header's bottom while it is on screen.
    var edge = header ? Math.max(0, header.getBoundingClientRect().bottom) : 0;
    // Let the inline and footer asks speak for themselves.
    var otherAsk = Array.from(document.querySelectorAll(
      '#content .trialband, #content .joinform, footer a[href="/join"], footer [data-support], footer [data-support-monthly]'
    )).some(function (el) { return inView(el, edge); });
    panel.hidden = shown || dismissed || otherAsk || cards.length <= 8 || cards[7].getBoundingClientRect().bottom > edge;
    var bar = document.getElementById("subbar");
    if (bar) bar.hidden = shown || !panel.hidden || otherAsk || inView(document.getElementById("subscribe"));
    maybePrompt();
  }
  document.addEventListener("click", function (e) {
    if (e.target.closest("#join-email-choice")) {
      document.getElementById("join-email-fields").hidden = false;
      document.getElementById("join-email-choice").setAttribute("aria-expanded", "true");
      document.getElementById("join-email").focus();
    }
    if (prompt && (e.target === prompt || e.target.closest("[data-join-close]"))) closePrompt();
    if (e.target.closest("#cattabs a, #controls select, #controls input")) {
      engaged = true;
      refresh();
    }
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
          rememberJoin("email");
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
    if (window.scrollY >= window.innerHeight) engaged = true;
    if (queued) return;
    queued = true;
    requestAnimationFrame(function () { queued = false; refresh(); });
  }, {passive: true});
  document.addEventListener("change", function (e) {
    if (e.target.closest("#controls")) { engaged = true; refresh(); }
  });
  if (prompt) {
    document.addEventListener("keydown", function (e) {
      if (!promptOpen()) return;
      if (e.key === "Escape") { e.preventDefault(); closePrompt(); return; }
      if (e.key !== "Tab") return;
      var items = Array.from(prompt.querySelectorAll('button, input, a[href], iframe, [tabindex="0"]'))
        .filter(function (el) { return !el.disabled && el.getClientRects().length; });
      var first = items[0], last = items[items.length - 1];
      if (!first) { e.preventDefault(); prompt.querySelector('[role="dialog"]').focus(); return; }
      if (e.shiftKey && (document.activeElement === first || !items.includes(document.activeElement))) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !items.includes(document.activeElement))) {
        e.preventDefault(); first.focus();
      }
    });
    document.addEventListener("focusin", function (e) {
      if (promptOpen() && !prompt.contains(e.target)) document.getElementById("join-email-choice").focus();
    });
    document.addEventListener("visibilitychange", function () { refresh(); });
    window.addEventListener("storage", function (e) {
      if (e.key === JOINED && e.newValue) { joined = true; closePrompt(); }
    });
  }
  window.addEventListener("resize", function () { refresh(); });
  var subscribe = document.getElementById("subscribe");
  if (subscribe && "IntersectionObserver" in window) {
    new IntersectionObserver(function () { refresh(); }).observe(subscribe);
  }
  window.addEventListener("hashchange", fromHash);
  window.wpSupport = {fromHash: fromHash, refresh: refresh};
  fromHash();
  if (!document.getElementById("content")) {
    fetch("/deals.json?t=" + Date.now(), {cache: "no-store"})
      .then(function (r) { return r.json(); }).then(function (d) { refresh(d.meta || {}); }).catch(function () {});
  }
})();
