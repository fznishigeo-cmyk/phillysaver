# Codex task 019: "Join free" (email) and "Support our work" replace the trial

**Read first:** AGENTS.md, CLAUDE.md, docs/paywall.md. Files:
`config.yaml`, `dealradar/config.py`, `dealradar/publish/site.py`,
`dealradar/publish/templates/*.html` (index, subscribe, about, how-it-works,
contact, privacy, welcome), `scripts/site_check.js`, tests.

Operator (2026-10-12): drop the membership pitch. The site stays free for
everyone and asks for two things only: **Join free** (an email address, for
a daily deals email that starts in about a week) and **Support our work**
(a one-time, pay-what-you-want Stripe Payment Link, Wikipedia-style ask).
Keep every membership/paywall code path; this is a config switch so it can
come back.

## Config
```
support:
  enabled: true        # free site: Join free + Support; trial/membership UI hidden
  payment_link: ""     # Stripe pay-what-you-want Payment Link (one-time); "" hides Support buttons
```
Load into AppConfig; deals.json `meta.support = {"enabled": bool, "link": str}`.
Validate the link: `https://buy.stripe.com/...` only, else treat as "" (warn).
Add `support` as a third template variant beside paywall/open
(`<!--support-->…<!--/support-->`), chosen when support.enabled; it wins over
open/paywall wording.

## Join free (email only, no account)
- The header trial button (`#subscribe`) and the phone bar (`#subbar`) read
  **Join free** and open a small dialog: one email field, a **Join free**
  button, and the line "The day's best deals in one email. Free. Unsubscribe
  anytime." Submit = `POST {api_url}/subscribe` with JSON `{"email": ...}`
  (the Worker route already exists: 200 `{ok:true}`; 429 = too many tries;
  400 = bad address). Success: "You're on the list. Our daily email starts
  soon; you'll get one confirmation email first." Errors in the same
  dialog. `track("sign_up", {method: "email"})` on success. Never ask for a
  password or card. Escape and the ✕ close it; focus returns.
- The in-wall band after 8 cards becomes the same offer inline: heading
  "Join free: the day's best deals in your inbox", an email field and a
  Join free button (same POST and messages).
- Hide the header "Log in" link and every trial/membership element (trial
  buttons, "$0 today", plan picker, trial disclosures) in this mode.

## Support our work (the Wikipedia-style ask)
- Replace the sticky bottom panel with a support banner. It appears only
  after the visitor scrolls past the 8th card, has a ✕ that hides it for
  7 days (localStorage in try/catch; without storage it just hides for the
  page view), and never covers the last card (keep the panel's existing
  sticky placement rules).
- Banner copy, exactly:
  - Title: **A quick, honest ask.**
  - Body: "wowpriced is free for everyone: no paywall, no ads. Every day we
    check thousands of prices so you only see real deals. If wowpriced saved
    you money, please chip in whatever it's worth to you. Most people never
    do. If you can, you keep it free for everyone."
  - Buttons: **Support our work** (opens `meta.support.link` in a new tab,
    `rel="noopener"`; `track("support_click")`) and **Join free** (opens the
    Join dialog).
  - Small print under the buttons: "One-time payment, any amount, through
    Stripe. wowpriced is a for-profit business: support is not
    tax-deductible and buys nothing extra; every deal stays free."
- No link configured: the banner shows only Join free (no Support button,
  no small print).
- Footer: replace the membership sentence with "wowpriced is free for
  everyone. **Support our work** · **Join free**" (links; Support hidden
  without a link).
- `subscribe.html` in support mode becomes the support page: the same
  appeal, the Support button, the small print and the Join free form.
- about / how-it-works / contact / privacy / welcome: support-mode wording
  wherever they describe membership or the trial (privacy: emails are
  stored by our Cloudflare Worker and used only for the deals email).

## Checks and tests
- `scripts/site_check.js` step 6 in support mode: the header button opens
  the Join dialog with an email field (never submit); step 7: skipped
  (log "n/a") unless the plan picker is expected (support off and yearly on).
- Tests: config + meta, the variant choice, every copy line above verbatim,
  the POST target and body, the ✕ memory guarded by try/catch, Support hidden
  without a link, the disclaimer next to every Support button, nothing
  membership-related rendered in support mode, and support off = today's
  pages unchanged (byte-for-byte for index.html).

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
