`render.cjs` renders `price-check.png` and `amazon-check.png` from the local
`fixture.html`; rerun after any card change. The updated Amazon fixture shows
an entry without price facts; full cards are supported when the data carries them.
It renders `promo-440x280.png` from `promo.html` with only our mark and message.
Run `node extension/store/render.cjs /tmp/wowpriced-store-render` to regenerate
all three PNGs. Only local examples are rendered; no retailer pages are loaded.
Delete the owned scratch directory afterward.

The two existing 1280×800 PNGs predate this review. Regeneration was attempted
on 2026-10-16, but Chromium launch was denied (`Operation not permitted`). The
promo PNG and refreshed Amazon screenshot still need this renderer run.
