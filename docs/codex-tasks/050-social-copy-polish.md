# Codex task 050: social posts read clean and human

**Read first:** AGENTS.md, CLAUDE.md (math is code; no prices on social;
`#ad` last). Files: `dealradar/publish/social.py` (`_word_trim`,
`_without_prices`), `dealradar/publish/pinterest.py` (`pin_payload`),
`dealradar/publish/meta.py` (`caption`), `dealradar/publish/telegram_pub.py`
(`format_message` channel branch), their tests.

Operator (2026-10-13) on the first Facebook/Instagram drafts: correct but
plain; model numbers look messy. Real drafts:
- "82% off KOBE Range Hoods RAX2130SQB-WM-2 Wall Mount Range Hood, Stainless Steel at Amazon"
- "85% off Kenneth Cole REACTION Women's Card Wedge, Soft Gold PU, 8 at Amazon"
- "76% off HIKVISION DS-2DP3236ZIXS-D/440 32MP 360 Pan Camera at Woot"
- "86% off Vantage Apparel Heather Jersey Men's Team 1/4 Zip, Tcu Horned Frogs, XL at Amazon"

## 1. One shared `social_title(item, brand)` in social.py (deterministic, no LLM)
- Keep the text before the first comma (variant, color and size come after).
- Drop model/part-number tokens: a token of 5+ characters mixing letters and
  digits (e.g. `RAX2130SQB-WM-2`, `DS-2DP3236ZIXS-D/440`), or a pure SKU-like
  run; keep normal words and specs a shopper reads (`32MP`, `360`, `1/4`,
  `4K`, `65"`, `2-Pack` stay).
- Collapse repeated brand words ("KOBE Range Hoods ... Range Hood" -> keep
  once), fix ALL-CAPS brand words to Title Case only when longer than 4
  letters and not an acronym list (`HIKVISION` -> `Hikvision`; `KOBE`,
  `LEGO`, `HP` stay).
- Trim at a word to 70 characters; never return empty (fall back to the
  original item trimmed).
Expected: "KOBE Wall Mount Range Hood", "Kenneth Cole REACTION Women's Card
Wedge", "Hikvision 32MP 360 Pan Camera", "Vantage Apparel Heather Jersey Men's
Team 1/4 Zip".

## 2. Use it everywhere social, with a hook line
- Facebook: `🔥 {pct}% OFF: {title}\nAt {store}, verified by wowpriced: {deal page URL}\n#ad`
- Instagram: `🔥 {pct}% OFF: {title}\nAt {store}, verified by wowpriced.\nTap the link in our bio for today's deals.\n{hashtags}\n#ad`
- Telegram channel: `🔥 {pct}% OFF: {title}\nAt {store}, verified by wowpriced: {deal page URL}\n#ad`
- Pinterest: title `{pct}% off {title}` (≤100), description
  `{title}. {pct}% off at {store}, verified by wowpriced. {disclosure}` (≤500).
- Still no "$", no price digits, disclosure last; `alt_text` keeps the full
  original item (accessibility).

## 3. Tests
The four real examples above (exact expected titles), ASIN-like and SKU
tokens removed, specs kept, empty fallback, length limits, no price in any
network's text, emoji hook present on Facebook/Instagram/Telegram, existing
caption tests updated. One short HANDOFF bullet under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
