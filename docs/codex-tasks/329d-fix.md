# Task 329d — the timer-ring browser test is still non-deterministic (read 329*-fix.md; your work is in this tree)

Native Chromium runs of tests/header_polish_browser.cjs fail at a DIFFERENT case each time, always in your helper `timerRingAfterTick`:
```
ring-1100-light-no-preference-ringOff: digits and ring did not reflect the advanced clock
ring-390-light-reduce-ringOff: digits and ring did not reflect the advanced clock
ring-430-light-reduce-on: digits and ring did not reflect the advanced clock
ring-390-dark-no-preference-ringOff: digits and ring did not reflect the advanced clock
```
It fails even with the ring OFF and on phones, so the problem is the test's clock handling, not the ring. The page clock is installed, paused at +123 s, then `page.clock.runFor(2000)` and a 5 s real-time poll waits for `countdown` text to change. Find how the homepage countdown actually updates (setInterval / setTimeout chain / requestAnimationFrame / visibility checks) and drive Playwright's clock so that update is guaranteed to run (e.g. `page.clock.runFor` in 250 ms steps until the second changes, or `fastForward` + `runFor(0)`; Playwright's fake clock also controls rAF only while timers are advanced). Make the helper deterministic, keep every assertion. Validate by reasoning from the countdown code; `node --check`; targeted pytest (basetemp /tmp/b329d, delete after). Reply <= 4 lines.
