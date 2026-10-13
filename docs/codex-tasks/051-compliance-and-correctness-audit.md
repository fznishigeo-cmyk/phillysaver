# Codex task 051: adversarial audit (report only, change no code)

**Read first:** AGENTS.md, CLAUDE.md (every non-negotiable), `sources.yaml`
(every `crawl_permission` and `image_permission` text: those are the
operator's written agreements), `config.yaml`, `docs/HANDOFF.md`, then the
code paths below. Write ONE file: `docs/audits/2026-10-13-audit.md`. Do NOT
modify any other file.

Why (2026-10-13): a shipped change swapped permitted sources' own tracker
links for our affiliate links, breaking their condition "source keeps
affiliate credit". It was caught by reading sources.yaml, not by tests. Find
every other place where the code can break an agreement, a rule, or the
truth on the page, before a visitor or a partner does.

## Check, with file:line evidence for each finding
1. **Permission agreements:** every condition in each `crawl_permission` /
   `image_permission` is honored on every path that publishes (site, deal
   pages, deals.json, member payload, email, Telegram, Pinterest, Facebook,
   Instagram): credit link present, source affiliate credit never replaced
   or stripped (affiliate rewrite, Sovrn wrapping, link overrides,
   redirect resolution, `_resolve_short_links`, canonicalization that drops
   their tracking params), source images only where `image_permission`,
   robots bypass only on the granted host.
2. **Politeness:** robots.txt on every non-granted request (including HEAD
   redirect hops), 1 req/s/host, User-Agent with contact, backoff,
   conditional GETs; no headless browser; no bypass of bot walls.
3. **Evidence and math:** every published price backed by a verbatim quote
   (`verify_evidence`), savings math only in `savings.py`, no LLM number
   reaching the page unchecked; percentage shown matches the math.
4. **Truth on the page:** dead deals hidden promptly, but never killed on one
   ambiguous signal; used/open-box/refurbished never top picks or social
   (operator 2026-10-13); prices never shown where a rule forbids (social
   posts, emails with Amazon links).
5. **Money and secrets:** spend caps cannot be bypassed; no secret in logs,
   site, deals.json or repo; affiliate tags correct; FTC disclosure present
   on every page and post.
6. **Idempotency:** a re-run of the same run key adds no rows and resends
   nothing (social posts and Telegram included).

## Report format
For each finding: severity (blocker / high / medium / low), what breaks and
for whom, file:line, a concrete failing scenario, the smallest fix, and a
pytest test that would fail today (code in the report). Then a section
"checked and fine" listing what you verified with evidence. Be precise; no
speculation without a scenario. End with a short note.
