"""Transient rule-hit counts, scoped to one pipeline run (never stored on deals)."""

from contextvars import ContextVar


_current: ContextVar[dict[str, int] | None] = ContextVar("accuracy_lite_counts", default=None)


def start():
    counts = dict(minimum_spend=0, subscribe_save_unknown=0, rewards_deferred=0,
                  woot_condition_skips=0, renewed_title_lane=0)
    return counts, _current.set(counts)


def finish(token):
    _current.reset(token)


def increment(name: str) -> None:
    counts = _current.get()
    if counts is not None:
        counts[name] += 1
