"""dealradar: polite online-deal aggregator.

Pipeline: fetch (RSS/API, robots-respecting) -> parse (Claude API, evidence-verified)
-> savings math (pure code) -> dedupe/verify/rank -> publish (static site, Telegram,
daily email digest). State lives in SQLite (state.db).
"""

__version__ = "0.1.0"
