# SAVINGS_RULES.md — the dealradar savings rulebook

**Division of labor (non-negotiable):** the LLM extracts **raw values only** —
stated prices, stated percentages, shipping text, stack steps as text in stated
order, and deal-type markers, each backed by verbatim evidence. **All math
lives here**, in `dealradar/savings.py`, as pure, deterministic, tested code.
The engine never guesses a price; when no stated basis exists, it excludes the
deal. Publication floors (`publish.min_pct`, `publish.min_abs_savings`) are
**not** applied here: the engine reports the true numbers and the publish layer
applies the floors.

Terminology: `A` = regular price, `B` = stated/derived final price,
`B_adj` = shipping-adjusted checkout price, `pct = (A - B_adj) / A * 100`.

---

## 1. Gates first

- `currency != 'USD'` **or** `region != 'US'` → `computable=False`,
  `exclude_reason='non_us'`.
- Any condition containing `in_store_only`, `ymmv`, or `clearance_aisle`
  (matched case-insensitively, spaces/hyphens treated as underscores) →
  `computable=False`, `exclude_reason='in_store'`.
- `online_only='unknown'` is **not** handled here; the publish layer decides.

**Worked example:** "Lego set €30 (was €60)" → currency `EUR` →
excluded `non_us`, no math attempted. "Clearance aisle YMMV my store" in
conditions → excluded `in_store`.

## 2. Price basis

- Regular **and** final both stated ("reg $A now $B", "$A value for $B",
  "$A worth for $B") → `pct = (A - B_adj) / A * 100`, `basis='quoted_regular'`.
- Explicit `pct_off` stated with **no** regular price → `pct = pct_off`,
  `basis='list_price_claim'`; `abs_savings` stays `None` (see rule 12 — a
  stated dollar figure with no verified prices is never trusted).
- A rebate alongside stated prices or a stated pct never changes the
  CHECKOUT math: rebate-only "stacks" keep their stated pct at checkout and
  move the rebate into `pct_with_rebates`.
- A non-positive regular price, or a negative final/upper price, is data
  corruption → excluded (`invalid_price`).
- `pct_off` stated **and** regular quoted (with a final) → compute from the
  prices, `basis='quoted_regular'` — **prices win**; the stated pct is kept
  only as sanity info (a >5-point disagreement is logged).
- Regular quoted, `pct_off` stated, **no** final price and no stack → the
  final is derived by arithmetic on the stated numbers
  (`B = A * (1 - pct_off/100)`), `basis='stated_pct'`. This is stated-number
  arithmetic, not a guess.

**Worked example:** "$120 value for $29" → `(120 - 29) / 120 * 100 = 75.8%`,
`basis='quoted_regular'`. "70% off everything" with no prices → `pct = 70.0`,
`basis='list_price_claim'`.

**Used / open-box (e.g. Amazon Warehouse / Amazon Resale):** the used price
is the final and the STATED new-condition price is the regular
(`basis='quoted_regular'` — the same math as any quoted pair). The deal must
carry the `used` (and, when stated, `open_box`) condition so the page and
alerts label it honestly; the condition is extracted from wording, never
assumed. Used supply is plentiful, so used/open-box deals publish only at
`publish.used_min_pct` (70%) or better — an absolute floor that the site's
lower tier never softens. The live price re-check for used deals validates
against the Warehouse offer, not the new price.

## 3. Ranges

`price_is_range=True` ("as low as $X", "from $X", "under $X") → use
`price_upper_bound` as the final price for the pct and append the flag
`'range'`. The conservative upper bound keeps us from advertising a best case
most shoppers will not get. The upper bound is a published price, so it needs
its own verbatim evidence quote (`evidence.price_upper_bound_quote`); a range
with **no verified upper bound is excluded** (`range_no_upper`) — computing
from the teaser "as low as" price would overstate the discount.

**Worked example:** "as low as $29.99 (up to $59.99), reg $100" →
`pct = (100 - 59.99) / 100 * 100 = 40.0%`, flags `['range']`.

## 4. Shipping

`(cost, threshold) = parse_shipping(d.shipping_cost_or_threshold)`:

- Stated cost → `B_adj = B + cost` **before** computing pct.
- Threshold stated **and** final below it **and** no stated cost → flag
  `'unknown_shipping'` and compute pct **without** shipping.
- Threshold stated and final at/above it → shipping is free; nothing is added.
- Both stated ("$6.99 or free over $50") → add the cost only when the final is
  below the threshold.
- Empty string → nothing.

Accepted inputs (case-insensitive): `''`, `'5.99'`, `'$5.99'`, `'free'`,
`'free shipping'`, `'free over $35'`, `'free on $25+'`,
`'$6.99 or free over $50'` (both values).

**Worked example:** "reg $100 now $35 + $6 shipping" →
`B_adj = 35 + 6 = 41` → `pct = 59.0%` (the shipping pushed it below a 60%
floor it would otherwise have cleared).

## 5. Stacks

Used when `final_price` is **not** stated but `regular_price` is. Start from
the regular price and apply `parse_stack_step` results sequentially in the
**stated order**:

- `sale_price` **sets** the running total,
- `abs_off` **subtracts** from the running total,
- `pct_off` **multiplies** the RUNNING total (never the regular price),
- `rebate_abs` / `rebate_pct` are collected separately and **never** reduce
  the checkout price,
- `unknown` steps are ignored for checkout math and logged.

`pct_at_checkout` comes from the running total (shipping-adjusted);
`pct_with_rebates` additionally subtracts the rebates (percent rebates are
taken on the pre-shipping checkout price). When `final_price` **is** stated,
it is trusted for checkout and rebate steps / `d.rebates` feed only
`pct_with_rebates`. `basis='stack'` when stack math produced the final.

**Worked example:** "reg $80, sale $40, code SAVE25 (25% off), $5 rebate" →
running: 80 → sale 40 → ×0.75 = **$30 checkout** → `pct_at_checkout = 62.5%`;
with the $5 rebate: effective $25 → `pct_with_rebates = 68.8%` (68.75 before
rounding); flag `rebate_dependent`.

## 6. Rebates in general

`pct_at_checkout` **excludes all rebates** (it is what you pay today);
`pct_with_rebates` includes them. When the two differ, append the flag
`'rebate_dependent'`.

**Worked example:** "reg $100, now $50, $10 mail-in rebate" → checkout 50.0%,
with rebates `(100 - 40) / 100 = 60.0%`, flags `['rebate_dependent']`.

## 7. Deal types (structural percentages)

| deal_type   | wording            | structural pct | basis       |
|-------------|--------------------|----------------|-------------|
| `bogo`      | buy 1 get 1 free   | 50.0           | `bogo`      |
| `b2g1`      | buy 2 get 1 free   | 33.3           | `b2g1`      |
| `b2g3`      | buy 2 get 3 free   | 60.0           | `b2g3`      |
| `b1g1_50`   | buy 1 get 1 50% off| 25.0           | `b1g1_50`   |

Percent-off codes in `stack_steps` compound **multiplicatively** on top:
`1 - p_total = (1 - p_type) × (1 - p_code) × …`. These deals qualify for
publication only if the total reaches the floor — the engine just reports the
true pct; floors are applied at publish time.

**Worked example:** "BOGO + extra 50% off code" →
`1 - 0.5 × 0.5 = 0.75` → `pct = 75.0%`.

## 8. Free after rebate / moneymaker / penny

- `free_after_rebate` → `pct_with_rebates = 100.0`,
  `pct_at_checkout = 100.0` **per product spec**,
  flags include `'rebate_dependent'`, `basis='free_after_rebate'`.
- `moneymaker` → same, except `pct_with_rebates` goes **above 100** when the
  stated numbers allow it (regular, final, and rebate amounts all known),
  else 100.0.
- `penny` ($0.01 outright) → regular known → compute truly; else
  `pct = 100.0`. `basis='penny'`.

**Worked example (moneymaker):** "$5 item, $8 mail-in rebate, reg $5" →
effective `5 - 8 = -$3` → `pct_with_rebates = (5 - (-3)) / 5 * 100 = 160.0%`,
`pct_at_checkout = 100.0`, flag `rebate_dependent`.
**Worked example (penny):** "$0.01, reg $4.99" → `(4.99 - 0.01)/4.99 = 99.8%`.

## 9. Gift-card-back / portal cashback / store credit

`deferred_value` **never enters any pct**; when present, append the flag
`'deferred_value'`. You cannot spend a gift card on rent — it is store-locked
future value, not savings on this purchase. Stack steps worded as cashback /
gift-card-back / store or statement credit / portal offers are classified as
deferred too ("10% cash back" is NOT a 10%-off code) and only set the flag.

**Worked example:** "$50 item + $40 gift card back" → pct is **0.0%**
(not 80%), flags `['deferred_value']`.

## 10. Multi-pack units

- `regular_price_unit` and `final_price_unit` both set and **different** →
  `computable=False`, `exclude_reason='unit_mismatch'` — never compare a
  per-unit regular to a pack final.
- Both `'per_unit'` → compute normally and append flag `'per_unit'`.

**Worked example:** "reg $12.99/bar, 4-pack now $39.99" → per_unit vs pack →
excluded `unit_mismatch` (naive math would claim fake savings).

## 11. Missing basis

Missing regular price **and** no stated pct **and** no `deal_type` →
`computable=False`, `exclude_reason='no_basis'`. **Never guess** a list price.

**Worked example:** "Gadget only $25 today!" (no reg, no pct) → excluded
`no_basis`.

## 12. Absolute savings, result fields, rounding

- `abs_savings = regular - final_adjusted` when both are known, else `None` —
  **derived or nothing**. The LLM's own `abs_savings` field is deliberately
  ignored: unlike the price fields it carries no verbatim-quote evidence, and
  it feeds a publish gate (`min_abs_savings`) and the ranking score.
- `pct_with_rebates` is capped at 100% except on the free-after-rebate /
  moneymaker basis (rule 8), the only deals allowed to report >100%.
- `final_price` in the result is the **shipping-adjusted** checkout price;
  `regular_price` passes through unchanged.
- All pcts are rounded to **1 decimal** at the very end.

**Worked example:** "reg $100 now $35 + $6 shipping" → result
`final_price=41.0`, `regular_price=100.0`, `abs_savings=59.0`, `pct=59.0`.

---

## Precedence summary

**gates → units → basis → range → stack → shipping → rebates → rounding**

1. Gates (non-US, in-store) exclude before any math.
2. Unit mismatch excludes before any comparison.
3. Pick the basis (quoted prices win over claims; deal_type is structural).
4. A range substitutes the upper bound as the final price.
5. Stack steps run in stated order on the running total.
6. Stated shipping joins the checkout price before the pct.
7. Rebates only ever affect `pct_with_rebates`.
8. Round pcts to 1 decimal last.

## What is EXCLUDED from pct, and why

- **Rebates are excluded from `pct_at_checkout`**: they are money you may get
  back weeks later, not money you keep at the register. They appear only in
  `pct_with_rebates`, which the product shows as a bonus figure.
- **Deferred value (gift cards back, portal cashback, store credit) is
  excluded from every pct**: it is store-locked or conditional future value,
  not a price reduction. It is surfaced via the `'deferred_value'` flag only.
- **Unknown-basis deals are excluded entirely** (`no_basis`): with no stated
  regular, pct, or deal type, any percentage would be an invented number.
- **Free-after-rebate counts as 100%** because the product promises "you end
  up paying nothing", which is the deal's real economics — but it is **always
  flagged `rebate_dependent`** because the buyer must float the cash and file
  the rebate, and a missed rebate makes the real pct 0%.
