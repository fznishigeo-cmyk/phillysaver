# Codex task 028: A+ category sorting, and more Beauty / Health deals

**Read first:** AGENTS.md, CLAUDE.md (politeness; Keepa is the paid
official API, its token budget is shared: `keepa_budget`; LLM spend caps;
knobs in config.yaml; idempotency). Files: `dealradar/fetch/keepa.py`
(`_selection`, `_map_row`, `_map_warehouse_row`, `_product_row`,
`EXCLUDED_CATEGORIES`, `structured`), `dealradar/parse/extractor.py`
(category reuse + `categorize_titles` after task 020),
`dealradar/parse/claude_client.py` (`categorize_titles`),
`dealradar/parse/schema.py` (`CATEGORIES`, the category rule in
`SYSTEM_PROMPT`), `dealradar/publish/seo.py` (`CATGROUPS`: the tabs),
tests.

## The live audit (2026-10-12, 1,641 live deals)
fashion 534, home 303, auto 184, electronics 174, kitchen 90, **other 89**,
sports 40, outdoors 39, office 37, toys 31, health 22, **beauty 21**,
kids_baby 16, books 14, grocery 12, pets 11, gaming 8, computers 7.
Sampled "other" (most are wrong): faux wood blind, bralette, CV axle shaft,
floor lamp, OtterBox case, moccasin slipper, Kenneth Cole / Samsonite /
U.S. Traveler luggage, woven wood bowl, air shocks, men's jeans, radiator,
pool float, AC compressor, Converse kids' shoes, Woot sunglasses titles
("100% Speedtrap - Mat Wht", "State California 2-garnet Spice 140").
Elsewhere: luggage in home, hearing-aid batteries and binoculars in
electronics. Likely cause: since task 020, Keepa/Woot structured deals get
their category from one Haiku title batch, and **any count mismatch turns
the whole batch into "other"**.

## 1. Amazon's own category first (Keepa, deterministic)
- Keepa deal rows and product objects carry Amazon's root category id
  (`rootCat` / `rootCategory`). Map it to our category by Amazon's
  category NAME, looked up through Keepa's category endpoint once per id
  and cached forever in db meta (`keepa_category_names`), batched, inside
  the shared token budget (never spend reserved tokens). Do not hardcode
  ids from memory; map names (case-insensitive):
  Beauty & Personal Care → beauty; Health & Household → health; Grocery &
  Gourmet Food → grocery; Home & Kitchen → home (keep "kitchen" when the
  title-based category already says kitchen); Tools & Home Improvement →
  home; Appliances → kitchen; Clothing, Shoes & Jewelry → fashion (luggage
  lives here); Electronics, Cell Phones & Accessories, Camera & Photo,
  Musical Instruments → electronics; Computers / Computers & Accessories →
  computers; Video Games → gaming; Toys & Games → toys_games; Baby / Baby
  Products → kids_baby; Sports & Outdoors → sports; Patio, Lawn & Garden →
  outdoors; Automotive → auto; Pet Supplies → pets; Office Products →
  office; Books, Kindle Store, Movies & TV, CDs & Vinyl → books_media;
  anything else → no opinion (fall through).
- Put the mapped category into `structured` for Keepa posts; it beats the
  title batch and the ASIN reuse.

## 2. The title batch never dumps a whole batch into "other"
Ask for `[{"i": <index>, "category": <name>}]`; accept each valid index
individually; one retry for the missing indexes; only those still missing
become "other". Better category rules in BOTH the title batch and
`SYSTEM_PROMPT`: luggage, bags, wallets, sunglasses/eyewear, watches →
fashion; car parts and accessories → auto; personal care (skin care,
makeup, hair care, fragrance, shaving and grooming, bath and body, nails)
→ beauty; vitamins, supplements, medicine, first aid, oral care, medical
devices, hearing-aid batteries, household supplies (cleaning, paper,
laundry) → health; lamps, blinds, furniture, bedding, home improvement →
home; outdoor gear, pools, lawn → outdoors; binoculars and optics →
sports. "other" only when nothing fits.

## 3. Re-sort the live deals once
A one-time pass (db meta `recategorized_v2`): every alive deal gets the
best category available: Keepa root name (ASIN deals, cached names only;
no extra product calls beyond the budgeted name lookups) else the new
title batch (60 per call, `kind="category"`, inside the spend caps; if a
cap stops it, the next run continues). Change only `category`. Log the
before/after counts per category (counts only).

## 4. More Beauty / Health supply (Keepa, same politeness)
The Amazon-price pass gets one extra page per pull for Amazon's Beauty &
Personal Care and Health & Household roots (`includeCategories` with the
ids found by NAME through the category lookup, cached), same floors, same
`MIN_RATING`, same sales-rank rule, only while ≥ 300 tokens stay reserved
(as the other extra pages). Config `keepa.focus_categories: ["Beauty &
Personal Care", "Health & Household"]` (empty = off).

## 5. Tests (offline, fake Keepa/Claude)
Name mapping table; cached lookups (no second call); budget respected;
structured category from rootCat; title batch: partial answers keep the
valid ones, retry the rest, never all-other on a mismatch; the prompt rules
present; the one-time pass (idempotent, resumes after a cap, only
`category` changes); the focus page (selection has includeCategories,
skipped when tokens are short or config empty);
tests/test_e2e_offline.py::test_rerun_is_idempotent green. One short
bullet in docs/HANDOFF.md under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure (especially the exact
Keepa field names and the category endpoint's response shape: read the
existing Keepa code and the official Keepa API docs if available offline;
never guess silently: say what you assumed).
