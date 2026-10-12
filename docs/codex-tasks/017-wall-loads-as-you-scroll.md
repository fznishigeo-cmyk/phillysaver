# Codex task 017: the wall shows 60 cards and adds more as you scroll

**Read first:** AGENTS.md, CLAUDE.md. Page:
`dealradar/publish/templates/index.html` (`render()`, `grid()`,
`gridHtml()`, `reorganize()`, the sort/filter/search/category handlers).

Operator (2026-10-12): the home page builds every live deal (~1,500 cards,
a 150,000 px page on desktop and 300,000 px on a phone) the moment it
opens: a huge scroll bar, heavy on phones. Show a page of cards and add
more as the visitor nears the bottom, like Reddit.

1. `var PAGE_CARDS = 60;` and `state.shown = PAGE_CARDS`. `render()` keeps
   building the same sections in the same order (headliners, the in-wall
   band after 8 cards, "Strong deals", then "Just missed"), but renders
   at most `state.shown` cards in total, in that order. "Just missed"
   appears only once every live card is rendered.
2. While cards remain, end the wall with `<div class="more">` holding a
   `<button type="button" id="moremore" class="pwbtn alt">Show more deals</button>`
   (works with keyboard and without IntersectionObserver). An
   IntersectionObserver on it (rootMargin ~1500px) and the button both add
   PAGE_CARDS to `state.shown` and render. The scroll position must not
   jump (the cards above stay identical); test it in your head against
   reorganize()'s FLIP: adding cards is NOT a drop (no FLIP, no toast,
   no re-deal: same seed).
3. `state.shown` goes back to PAGE_CARDS when the visitor changes the
   search, store, source, min %, sort or category tab; a drop or a late
   publish keeps it (never shrinks the wall under the reader).
4. The CTA panel (`panel`) still goes after the live grids, ahead of
   "Just missed"; with cards still to come it goes after the last rendered
   live grid, before the "more" box.
5. The tagline count, the ticker, "Deals found across" and deals.json are
   unchanged (they read state.deals, not the DOM).
6. `scripts/site_check.js` prints "cards on the page": rename it "cards
   rendered" and add "of N live" so the hourly check still makes sense.
7. Tests in tests/test_publish_site.py for the markers above (PAGE_CARDS,
   the cap in render, the reset in the handlers, the observer + button,
   no FLIP on "more"). Do not loosen existing tests; adjust only those that
   assumed every card is rendered, and say which.

Run `.venv/bin/python -m pytest -q`; all green. Do NOT commit or push.
End your reply with a short note of what changed and anything unsure.
