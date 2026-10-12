# Codex task 040: simple code deals send "Get deal" to the store (with our link)

**Read first:** AGENTS.md, CLAUDE.md (evidence rules unchanged; affiliate
links only through `rewrite_affiliate`). Files: `dealradar/publish/site.py`
(`_needs_steps`, `_serialize_deal`: the `ours` rule near "A deal that needs
steps links the post that explains them"), `dealradar/verify.py`
(`_price_hidden_by_promo`), `scripts/site_check.js` (link mix), tests.

Operator (2026-10-12, approved): about 400 live deals (link_mix
`source_post`) send "Get deal" to the source's post because they need a
code or steps, so they earn nothing. The card already shows the code
("How to get this price: Code SAVE10"). Amazon code deals already go to
Amazon with our tag; extend that to every store now that Sovrn is on.

## Rule
- A deal is **simple** when the only reason it needs steps is a coupon
  code: it has at least one code, no rebates, no stack steps other than
  "use/enter/apply code X" wording, and none of `_STEP_CONDITIONS`.
- A simple deal whose store link is earning (Amazon with our tag, a Sovrn
  wrap, or a `link_overrides` hit) sends "Get deal" to that store link.
  The card keeps the code, its copy button and the "via" credit link.
- Everything else is unchanged: complex deals (rebates, multi-step,
  in-cart, Subscribe & Save, targeted, in-store, multi-buy) keep linking
  the source post; a store link that would not earn keeps the post.

## Tests
Simple code deal at Walmart -> Sovrn link; at Amazon -> tagged link (as
today); simple deal at a store Sovrn does not wrap -> post; each complex
reason -> post; Sovrn switched off -> post (Amazon unchanged); emails and
deals.json stubs unchanged; link_mix counts move from source_post to
sovrn/amazon_tagged accordingly. One short HANDOFF bullet under "In flight".

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push. End
with a short note of what changed and anything unsure.
