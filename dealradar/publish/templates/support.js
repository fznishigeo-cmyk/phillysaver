"use strict";
(function () {
  var meta = {}, dismissed = false, queued = false;
  var KEY = "wp.support.dismissed", WEEK = 7 * 24 * 60 * 60 * 1000;
  var panel = document.getElementById("paywall");
  var prompt = document.getElementById("join-prompt"), started = Date.now();
  var SHOWN = "wp.join.promptUntil", JOINED = "wp.join.joined";
  var engaged = false, shown = false, joined = false, timer = null, previousFocus;
  var googleLoading = false, googleBusy = false, emailBusy = false, successTimer = null;
  var popup = false, footer = document.getElementById("footer-signup");
  var onJoinPage = /^\/join(?:\.html)?\/?$/.test(location.pathname);
  // support-ask
  var ASK = "wp.support.askShown", SUPPORTED = "wp.support.clicked";
  var askSeen = false, supported = false, activeAsk = null;
  function showSupportAsk(success) {
    if (activeAsk && activeAsk.success === success) return true;
    var support = meta.support || {};
    if (!support.enabled || !/^https:\/\/buy\.stripe\.com\/[^\s<>"'\\]+$/.test(support.link || "") ||
        joinedBefore || askSeen || supported) return false;
    try {
      if (localStorage.getItem(ASK) || localStorage.getItem(SUPPORTED)) return false;
      localStorage.setItem(ASK, "1");
    } catch (e) { /* blocked storage: once in this page view */ }
    askSeen = true;
    var style = document.createElement("style");
    style.textContent = {{SUPPORT_ASK_STYLE}};
    document.head.appendChild(style);
    var ask = document.createElement("span");
    ask.className = "supportask";
    ask.innerHTML = {{SUPPORT_ASK_HTML}};
    success.setAttribute("role", "status");
    success.appendChild(ask);
    activeAsk = {node: ask, success: success};
    if (successTimer !== null) { clearTimeout(successTimer); successTimer = null; }
    if (promptOpen()) {
      prompt.setAttribute("data-support-asking", "");
      prompt.querySelector('[role="dialog"]').setAttribute("aria-describedby", "join-success");
    }
    if (onJoinPage && success.id === "join-success") {
      var deals = document.getElementById("join-deals");
      deals.textContent = "See today's deals";
      deals.className = "supportask-deals";
    }
    if (success.id === "footer-signup-success") {
      success.setAttribute("tabindex", "-1");
      success.focus({preventScroll: true});
    }
    track("support_ask_shown");
    return true;
  }
  function finishSupportAsk(method) {
    if (!activeAsk) return;
    var success = activeAsk.success;
    // Keep the chosen link connected for the browser's normal new-tab action.
    if (method) activeAsk.node.hidden = true;
    else activeAsk.node.remove();
    activeAsk = null;
    track(method ? "support_ask_click" : "support_ask_dismiss", method ? {method: method} : undefined);
    if (success.id === "join-success" && promptOpen()) {
      closePrompt();
    } else {
      var focus = document.getElementById(success.id === "join-success" ? "join-deals" : "footer-signup-title");
      if (focus) {
        if (success.id !== "join-success") focus.setAttribute("tabindex", "-1");
        focus.focus({preventScroll: true});
      }
    }
  }
  // /support-ask
  function promptOpen() { return prompt && !prompt.hidden; }
  function emailReady(input) {
    input = input || document.getElementById("join-email");
    return input && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(input.value.trim());
  }
  function updateContinue() {
    var fields = document.getElementById("join-email-fields");
    if (fields) fields.querySelector('button[type="submit"]').disabled = emailBusy || !emailReady();
  }
  function step(email) {
    var title = document.getElementById("join-title");
    title.textContent = email ? "What's your email?" : "Join wowpriced";
    title.className = email ? "joinemailtitle" : "";
    document.getElementById("join-step-one").hidden = email;
    document.getElementById("join-description").hidden = email;
    document.getElementById("join-email-fields").hidden = !email;
    document.getElementById("join-email-choice").setAttribute("aria-expanded", String(email));
    updateContinue();
    document.getElementById(email ? "join-email" : "join-email-choice").focus({preventScroll: true});
  }
  function focusCard() {
    var id = !document.getElementById("join-success").hidden ? "join-title"
      : !document.getElementById("join-email-fields").hidden ? "join-email" : "join-email-choice";
    document.getElementById(id).focus({preventScroll: true});
  }
  function positionPrompt() {
    if (!promptOpen()) return;
    var dialog = prompt.querySelector('[role="dialog"]');
    dialog.setAttribute("aria-modal", !popup && window.innerWidth > 640 ? "false" : "true");
    var subscribe = document.getElementById("subscribe");
    if (subscribe) {
      var rect = subscribe.getBoundingClientRect();
      prompt.style.setProperty("--join-top", Math.max(16, rect.bottom + 8) + "px");
      prompt.style.setProperty("--join-right", Math.max(16, window.innerWidth - rect.right) + "px");
    }
  }
  function openPrompt(manual) {
    popup = !manual;
    shown = true;
    previousFocus = document.activeElement;
    prompt.hidden = false;
    prompt.className = popup ? "joinoverlay joinpopup" : "joinoverlay";
    // support-ask
    prompt.removeAttribute("data-support-asking");
    prompt.querySelector('[role="dialog"]').setAttribute("aria-describedby", "join-description");
    // /support-ask
    document.getElementById("join-success").hidden = true;
    document.getElementById("join-legal").hidden = false;
    document.getElementById("join-google-message").hidden = true;
    document.getElementById("join-email-fields").querySelector("[data-join-message]").hidden = true;
    var fields = document.getElementById("join-email-fields");
    var title = document.getElementById("join-title");
    var description = document.getElementById("join-description");
    fields.querySelector("label").textContent = popup ? "Email address" : "Email";
    document.getElementById("join-email").placeholder = popup ? "Email address" : "Email";
    fields.querySelector('button[type="submit"]').textContent = popup ? "Get the deals" : "Continue";
    document.getElementById("join-email-choice").hidden = popup;
    document.getElementById("join-back").hidden = popup;
    document.getElementById("join-google-or").hidden = !popup || document.getElementById("join-google").hidden;
    description.textContent = popup ? "The day's best verified deals in one free email. Unsubscribe anytime."
      : "The day's best deals in one email. Free.";
    document.getElementById("join-legal").innerHTML = popup
      ? 'By joining, you agree to get deal emails from wowpriced. See our <a href="/privacy.html">privacy policy</a>.'
      : 'By continuing, you agree to wowpriced\'s <a href="/privacy.html">privacy policy</a>.';
    if (popup) {
      title.innerHTML = 'Save up to <span>90% OFF</span>';
      title.className = "";
      description.hidden = false;
      document.getElementById("join-step-one").hidden = false;
      fields.hidden = false;
      updateContinue();
      focusCard();
    } else step(false);
    positionPrompt();
    // The join card takes the banner's place: one ask per page view.
    if (panel) panel.hidden = true;
    var bar = document.getElementById("subbar");
    if (bar) bar.hidden = true;
    track("signup_prompt_shown");
    loadGoogle();
  }
  function rememberJoin(method) {
    joined = true;
    try { localStorage.setItem(JOINED, "1"); } catch (e) { /* remembered for this page */ }
    if (footer && method !== "footer") footer.hidden = true;
    // support-ask
    if (footer && activeAsk && activeAsk.success.id === "footer-signup-success") footer.hidden = false;
    // /support-ask
    track("sign_up", {method: method});
  }
  function closePrompt() {
    if (!promptOpen()) return;
    // support-ask
    if (activeAsk && activeAsk.success.id === "join-success") {
      activeAsk.node.remove();
      activeAsk = null;
      track("support_ask_dismiss");
    }
    if (previousFocus && (!previousFocus.isConnected || (footer && footer.hidden && footer.contains(previousFocus)))) {
      previousFocus = document.getElementById("subscribe");
    }
    // /support-ask
    if (successTimer !== null) { clearTimeout(successTimer); successTimer = null; }
    prompt.hidden = true;
    track("signup_prompt_closed");
    if (previousFocus && previousFocus.isConnected) previousFocus.focus({preventScroll: true});
    refresh();
  }
  function joinedCard(method) {
    rememberJoin(method);
    var success = document.getElementById("join-success");
    if (!success || (!onJoinPage && !promptOpen())) return;
    document.getElementById("join-step-one").hidden = true;
    document.getElementById("join-email-fields").hidden = true;
    document.getElementById("join-description").hidden = true;
    document.getElementById("join-legal").hidden = true;
    success.hidden = false;
    document.getElementById("join-deals").hidden = !onJoinPage;
    var title = document.getElementById("join-title");
    title.textContent = "You're on the list.";
    title.className = "";
    title.setAttribute("tabindex", "-1");
    title.focus({preventScroll: true});
    // support-ask
    if (showSupportAsk(success)) return;
    // /support-ask
    successTimer = setTimeout(function () {
      successTimer = null;
      if (onJoinPage) location.replace("/");
      else closePrompt();
    }, onJoinPage ? 1500 : 1800);
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
    openPrompt();
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
        document.getElementById("join-google-or").hidden = !popup;
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
        joinedCard("google");
      }).catch(function () {
        msg.textContent = "Google sign-up is unavailable right now. Please continue with email or try again.";
        msg.setAttribute("role", "alert");
      }).then(function () { googleBusy = false; });
  }
  try { dismissed = Number(localStorage.getItem(KEY)) > Date.now(); }
  catch (e) { /* storage blocked: dismissal lasts for this page view */ }
  try { joined = Boolean(localStorage.getItem(JOINED)); }
  catch (e) { /* joins still work without storage */ }
  // support-ask
  var joinedBefore = joined;
  // /support-ask
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
    if (footer) footer.hidden = joined && document.getElementById("footer-signup-success").hidden;
    positionPrompt();
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
      '#content .trialband, #content .joinform, footer a[href="/join"], footer [data-support], footer [data-support-monthly], #footer-signup'
    )).some(function (el) { return inView(el, edge); });
    panel.hidden = shown || dismissed || otherAsk || cards.length <= 8 || cards[7].getBoundingClientRect().bottom > edge;
    var bar = document.getElementById("subbar");
    if (bar) bar.hidden = shown || !panel.hidden || otherAsk || inView(document.getElementById("subscribe"));
    maybePrompt();
  }
  document.addEventListener("click", function (e) {
    // support-ask
    var supportButton = e.target.closest("[data-support], [data-support-monthly]");
    if (supportButton) {
      supported = true;
      try { localStorage.setItem(SUPPORTED, "1"); } catch (err) { /* remembered for this page */ }
      if (activeAsk && activeAsk.node.contains(supportButton)) {
        finishSupportAsk(supportButton.hasAttribute("data-support-monthly") ? "monthly" : "once");
        return;
      }
    }
    if (activeAsk && (e.target.closest("[data-support-ask-dismiss]") || e.target.closest("#join-deals"))) {
      finishSupportAsk();
      return;
    }
    // /support-ask
    if (prompt && window.innerWidth > 640 && e.target.closest("#subscribe") &&
        !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey && !e.button) {
      e.preventDefault();
      engaged = true;
      if (promptOpen()) closePrompt();
      else {
        try { localStorage.setItem(SHOWN, String(Date.now() + WEEK)); } catch (err) { /* manual joins need no storage */ }
        openPrompt(true);
      }
      return;
    }
    if (e.target.closest("#join-email-choice")) {
      step(true);
    }
    if (e.target.closest("#join-back")) step(false);
    if (promptOpen() && (!prompt.querySelector('[role="dialog"]').contains(e.target) ||
        e.target.closest("[data-join-close]"))) closePrompt();
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
  document.addEventListener("input", function (e) {
    if (e.target.id === "join-email") updateContinue();
  });
  document.addEventListener("submit", function (e) {
    var el = e.target;
    if (!el.matches(".joinform")) return;
    e.preventDefault();
    var input = el.querySelector('input[type="email"]'), btn = el.querySelector('button[type="submit"]');
    var msg = el.querySelector("[data-join-message]");
    function say(text, error) { msg.textContent = text; msg.hidden = false; msg.setAttribute("role", error ? "alert" : "status"); }
    var api = String(meta.api_url || "").replace(/\/+$/, "");
    if (emailBusy) return;
    if (!emailReady(input)) {
      say("Please enter a valid email address.", true); return;
    }
    if (!api) { say("Sign-up is unavailable right now. Please try again soon.", true); return; }
    emailBusy = true;
    btn.disabled = true;
    msg.hidden = true;
    fetch(api + "/subscribe", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({email: input.value.trim()})
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (r.status === 200 && j.ok === true) {
          if (el.hasAttribute("data-join-footer")) {
            el.hidden = true;
            document.getElementById("footer-signup-success").hidden = false;
            rememberJoin("footer");
            // support-ask
            if (activeAsk) return; // another join response must not close the visible ask
            // /support-ask
            if (promptOpen()) closePrompt();
            // support-ask
            showSupportAsk(document.getElementById("footer-signup-success"));
            // /support-ask
          } else {
            say("You're on the list. When our deals email starts, we'll send you one email to confirm first.", false);
            joinedCard("email");
          }
        } else {
          say(r.status === 429 ? "Too many tries. Please try again in an hour."
            : r.status === 400 ? "Please enter a valid email address."
            : "Sign-up is unavailable right now. Please try again soon.", true);
        }
      });
    }).catch(function () { say("Couldn't reach the server. Please check your connection and try again.", true); })
      .then(function () { emailBusy = false; btn.disabled = false; updateContinue(); });
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
      // support-ask
      if (activeAsk && !promptOpen() && e.key === "Escape") {
        e.preventDefault(); finishSupportAsk(); return;
      }
      // /support-ask
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
      if (promptOpen() && !prompt.contains(e.target)) focusCard();
    });
    document.addEventListener("visibilitychange", function () { refresh(); });
  }
  // support-ask
  if (!prompt) document.addEventListener("keydown", function (e) {
    if (activeAsk && e.key === "Escape") { e.preventDefault(); finishSupportAsk(); }
  });
  // /support-ask
  window.addEventListener("storage", function (e) {
    // support-ask
    if ((e.key === ASK || e.key === SUPPORTED) && e.newValue) {
      if (e.key === ASK) askSeen = true;
      else supported = true;
    }
    // /support-ask
    if (e.key === JOINED && e.newValue) {
      joined = true;
      if (footer) footer.hidden = true;
      closePrompt();
    }
  });
  window.addEventListener("resize", function () { refresh(); });
  var subscribe = document.getElementById("subscribe");
  if (subscribe && "IntersectionObserver" in window) {
    new IntersectionObserver(function () { refresh(); }).observe(subscribe);
  }
  window.addEventListener("hashchange", fromHash);
  window.wpSupport = {fromHash: fromHash, refresh: refresh};
  fromHash();
  if (footer) footer.hidden = joined;
  if (!document.getElementById("content")) {
    fetch("/deals.json?t=" + Date.now(), {cache: "no-store"})
      .then(function (r) { return r.json(); }).then(function (d) { refresh(d.meta || {}); }).catch(function () {});
  }
})();
