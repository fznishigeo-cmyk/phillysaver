# 200 — Study the best open-source clones; plan wowpriced's strongest architecture and "lifelong visitor" features

## Why (operator 2026-10-16, ~03:30Z, verbatim intent)
"Build the strongest code infrastructure imaginable ... the strongest code architecture possible. Look at
other top websites and READ THEIR CODE and figure out how to make our website so good that as soon as
someone visits they will be a lifelong visitor. ... https://github.com/gorvgoyl/clone-wars ... I'm not
saying make my website like TikTok. Some of the code in that set is pure genius; figure out how we can use
it to our advantage."

wowpriced.com is a verified-deals wall: a Python pipeline (`dealradar/`) reads deal sources politely,
verifies every price with evidence, ranks deals and publishes a static site (vanilla JS in
`dealradar/publish/templates/*.html|js`) to Cloudflare Pages every ~15 min. 70-80% of visitors are on
phones. Read `CLAUDE.md`, `AGENTS.md`, `docs/HANDOFF.md` first.

## Read these (already cloned, read-only; do not modify them)
- /home/user/burhan-syed/troddit — Reddit client (feeds, infinite scroll, masonry, prefetch, PWA)
- /home/user/sebst/pythonic-news — Hacker News in Django (ranking, Python)
- /home/user/d11z/asperitas — Reddit clone (hot ranking, voting)
- /home/user/th3wall/fakeflix — Netflix (rows, personalized home)
- /home/user/kirkwat/tiktok — TikTok (feed, preloading, engagement loop)
- /home/user/muzam1l/tclone — Twitter (timeline, real-time, notifications)
- /home/user/eknoorpreet/dev.to-clone — Dev.to (reading list, follow tags, notifications)
- /home/user/emmanuelhashy/amazon-clone — Amazon (product pages, cart)
- /home/user/plausible/analytics — privacy analytics (event model, bot filtering, unique visitors)
- /home/user/thedevs-network/kutt — link shortener (redirects, click stats)
- /home/user/unleash/unleash — feature flags (gradual rollout, kill switches, variants)
- /home/user/gorvgoyl/clone-wars/README.md — the full list (more repos named there; only the ones above are local)

## Deliverables (documents only — do NOT change product code in this task)
1. `docs/research/clone-wars-study.md`:
   a. Per repo: 3-6 concrete techniques WITH file paths (and line numbers) in their code, what each does,
      and why it keeps users coming back or makes the system robust. Quote at most a few lines each.
   b. Map each technique to wowpriced: the exact place in OUR code (file:line) it would live, how to do it
      in our stack (static pages + vanilla JS + Python pipeline + Cloudflare Pages + GitHub Actions; no
      server, no login required), and a verdict: ADOPT / ADAPT / SKIP with the reason.
   c. Hard filters for every ADOPT/ADAPT: CLAUDE.md non-negotiables (robots, evidence-verified prices,
      store facts only, math in savings.py, knobs in config.yaml, AI caps, idempotency); Amazon Associates
      policy (NO price tracking/price alerts/price history for Amazon items; prices only with the
      date/time stamp; amazon_history: hide is on); privacy (no new third-party scripts; local-only
      personalization is fine); WCAG 2.2 AA; phone performance (no heavy frameworks, LCP budget).
   d. "Lifelong visitor" plan: the 5-8 habits that make people return daily to a deal site (e.g. "new
      since your last visit", a daily ritual like the drop clock, saved deals, a feed that learns
      categories locally, instant navigation, offline/installable app, share loops), what we already have
      (find it in our code), and what is missing.
   e. Architecture review of OUR codebase against what these projects do well: the 5-10 structural
      weaknesses that most raise the risk of bugs or slow us down (e.g. template marker blocks, published
      byte pins in tests, 3000-line index.html, config switch sprawl, ordering logic duplicated in Python
      and JS), each with a concrete, incremental refactor (never a rewrite), its risk and payoff.
   f. A ranked TOP 10 (impact on retention and free traffic ÷ cost/risk), each a few lines.
2. For each of the top 6: a self-contained build brief `docs/codex-tasks/2NN-<slug>.md` (201..206) in the
   style of `docs/codex-tasks/121-safety-real-numbers.md` (Why / Task / Tests / Constraints), small
   enough for one Codex session, shipping behind a config.yaml knob where behavior changes, with offline
   tests and idempotency.

## Rules
No network. No commits. Write only the files named above. Be concrete and cite files; no generic advice.
Keep the study under ~1,500 lines. Finish with a short reply: the top 10 list and the brief filenames.
