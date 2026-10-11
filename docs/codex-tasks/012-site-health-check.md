# Codex task 012: a daily site-health check in the operator's report email

**Read first:** AGENTS.md, CLAUDE.md. The operator keeps finding missing
photos by hand; the daily growth report (dealradar/growth/report.py, emailed
privately; its Actions log shows section status only) must catch it instead.

Add a report section `site_health`, needing no secret, that reads the PUBLIC
site the way a visitor does (through the report's existing httpx client,
our descriptive User-Agent):
1. GET `https://wowpriced.com/deals.json` (site URL from config
   `publish.custom_domain`). Report: live deals; how many have `image_url`
   (count and %); the 10 stores and the 10 source sites (`src`) with the most
   live deals WITHOUT a photo; minutes since `meta.published_at` (stale if
   more than 3x `meta.cadence_minutes`); `meta.locked_count`.
2. Photos that do not load: take up to 60 live deals with `image_url`
   (spread across stores: at most 6 per store), request each image (HEAD; on
   405/403 a GET with `Range: bytes=0-1023`), at most 1 request per second per
   host, 6 s timeout. Count broken = non-2xx or a non-image content type.
   Report broken count and the stores they belong to (no URLs in logs).
3. GET `https://wowpriced.com/` → status 200 and the page contains
   `id="content"`.
4. Put it in the email text (a short "Site health" block near the top: e.g.
   "Photos: 1,180 of 1,404 live deals (84%); missing most at: Woot 40, ...;
   broken: 2 of 60 checked (Revolve)"; "Last publish: 6 min ago"). Flag
   problems with a leading "⚠" line: photo coverage under 85%, any broken
   share over 5%, stale publish, home page not 200.
5. Tests with httpx.MockTransport: coverage math, per-store cap, broken
   detection, stale flag, and that the Actions log line stays `site_health: ok`
   / `error <Class>` only.

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push.
