"""Claude API client wrapper: model selection, structured extraction, spend cap.

Model choice ("cheapest current model"): the models endpoint carries no pricing,
so the documented heuristic is used — among ids containing 'haiku' pick the
newest by created_at; none => newest 'sonnet'; none => first listed. A hardcoded
model id is only ever a LAST-RESORT fallback when the endpoint call itself
fails, and it is logged loudly.

Spend cap: before EVERY API call the accumulated per-run spend is checked
against cfg.spend_cap_usd_per_run and SpendCapReached is raised when hit.
After every response, cost is accumulated from usage tokens times
cfg.model_pricing_per_mtok (per-model key, else 'default') / 1e6.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

import anthropic
from pydantic import ValidationError

from ..config import AppConfig, get_secret
from ..errors import ParserNotConfigured, SpendCapReached
from .schema import SYSTEM_PROMPT, LLMDeal, LLMDeals, build_user_prompt

log = logging.getLogger("dealradar.parse.claude_client")

# LAST RESORT ONLY: used when GET /v1/models itself fails. Never the primary path.
FALLBACK_MODEL = "claude-haiku-5-5"

MAX_TOKENS = 4096

_JSON_ONLY_SUFFIX = (
    "\n\nRespond with ONLY a single JSON object, no prose and no markdown fences, "
    'matching exactly this shape: {"deals": [ ... ]} where each entry has the '
    "fields item, store, brand, product_url, online_only, regular_price, "
    "final_price, pct_off, abs_savings, shipping_cost_or_threshold, stack_steps, "
    "codes, rebates, deferred_value, conditions, expires_at_raw, quantity_or_unit, "
    "regular_price_unit, final_price_unit, currency, evidence "
    "(regular_price_quote, final_price_quote, pct_quote), confidence, deal_type, "
    "price_is_range, price_upper_bound."
)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _strip_fences(text: str) -> str:
    """Tolerate a model that wraps its JSON in markdown fences despite orders."""
    m = _FENCE_RE.match(text)
    return m.group(1) if m else text.strip()


class ClaudeParser:
    """Thin wrapper around the anthropic client for deal extraction.

    ``client`` is injectable for tests; the real one is built from the
    DEALS_CLAUDE_API_KEY environment secret (NOT ANTHROPIC_API_KEY).
    ``sleep`` is injectable so retry backoff never really sleeps in tests.
    """

    def __init__(self, cfg: AppConfig, *, client: Any = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cfg = cfg
        self._sleep = sleep
        if client is None:
            api_key = get_secret("DEALS_CLAUDE_API_KEY")
            if not api_key:
                raise ParserNotConfigured("DEALS_CLAUDE_API_KEY is not set")
            client = anthropic.Anthropic(api_key=api_key)
        self.client = client
        self.spend_usd: float = 0.0
        self.model: str = self._choose_model()

    # ------------------------------------------------------------- model pick

    def _choose_model(self) -> str:
        try:
            listed = list(self.client.models.list())
        except Exception as e:  # endpoint failure only -> last-resort constant
            log.error(
                "models.list() failed (%s: %s); FALLING BACK to hardcoded "
                "last-resort model %s — fix API access, this is not the normal path",
                type(e).__name__, e, FALLBACK_MODEL,
            )
            return FALLBACK_MODEL
        if not listed:
            log.error(
                "models.list() returned no models; FALLING BACK to hardcoded "
                "last-resort model %s", FALLBACK_MODEL,
            )
            return FALLBACK_MODEL

        for family in ("haiku", "sonnet"):
            candidates = [m for m in listed if family in m.id]
            if candidates:
                chosen = max(candidates, key=lambda m: m.created_at)
                log.info(
                    "model selected: %s (newest '%s' of %d listed; endpoint "
                    "carries no pricing, documented cheapest-model heuristic)",
                    chosen.id, family, len(listed),
                )
                return chosen.id
        chosen = listed[0]
        log.info(
            "model selected: %s (no haiku/sonnet ids among %d listed; "
            "using first listed model)", chosen.id, len(listed),
        )
        return chosen.id

    # ---------------------------------------------------------- spend / cost

    def _check_spend_cap(self) -> None:
        cap = self.cfg.spend_cap_usd_per_run
        if self.spend_usd >= cap:
            raise SpendCapReached(
                f"LLM spend ${self.spend_usd:.4f} >= cap ${cap:.4f} for this run"
            )

    def _account(self, resp: Any) -> None:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        pricing_table = self.cfg.model_pricing_per_mtok or {}
        pricing = pricing_table.get(self.model) or pricing_table.get("default") or {}
        in_rate = float(pricing.get("input", 0.0))
        out_rate = float(pricing.get("output", 0.0))
        cost = (usage.input_tokens * in_rate + usage.output_tokens * out_rate) / 1e6
        self.spend_usd += cost

    # -------------------------------------------------------------- requests

    def _request(self, fn: Callable[[], Any]) -> Any | None:
        """Run one API call with the spend-cap gate and one transport retry.

        Returns the response, or None when the call failed and the post should
        be skipped. 429/5xx/connection errors get exactly one retry after an
        injectable 2 s sleep. anthropic.BadRequestError and AttributeError
        propagate so the caller can fall back from messages.parse.
        SpendCapReached always propagates.
        """
        for attempt in (0, 1):
            self._check_spend_cap()
            try:
                return fn()
            except anthropic.BadRequestError:
                raise
            except anthropic.APIConnectionError as e:
                err: Exception = e
                retryable = True
            except anthropic.APIStatusError as e:
                err = e
                retryable = e.status_code == 429 or e.status_code >= 500
            except anthropic.APIError as e:
                err = e
                retryable = False
            if not retryable or attempt == 1:
                log.warning("Claude API call failed, skipping post: %s: %s",
                            type(err).__name__, err)
                return None
            log.warning("Claude API call failed (%s: %s); retrying in 2 s",
                        type(err).__name__, err)
            self._sleep(2.0)
        return None  # unreachable; keeps type-checkers happy

    # -------------------------------------------------------------- extract

    def extract(self, text: str, meta: dict) -> list[LLMDeal]:
        """Extract deals from one cleaned post text. Never crashes the run:
        API failures are logged and yield []; only SpendCapReached propagates.
        """
        user_prompt = build_user_prompt(text, meta)
        try:
            resp = self._request(lambda: self.client.messages.parse(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
                output_format=LLMDeals,
            ))
        except AttributeError:
            log.info("messages.parse unavailable; using JSON fallback path")
            return self._extract_via_create(user_prompt)
        except anthropic.BadRequestError as e:
            log.info("messages.parse rejected (%s); using JSON fallback path", e)
            return self._extract_via_create(user_prompt)

        if resp is None:
            return []
        self._account(resp)
        if getattr(resp, "stop_reason", None) == "refusal":
            log.warning("model refused extraction for %s; zero deals",
                        meta.get("source_url", ""))
            return []
        parsed = getattr(resp, "parsed_output", None)
        if parsed is None:
            log.warning("messages.parse returned no parsed_output; zero deals")
            return []
        return list(parsed.deals)

    def _extract_via_create(self, user_prompt: str) -> list[LLMDeal]:
        """JSON-only fallback via messages.create with bounded re-asks.

        Invalid JSON / schema validation failures are retried up to
        cfg.retries_on_invalid_json times (after the initial attempt), then the
        post is skipped.
        """
        attempts = 1 + max(0, int(self.cfg.retries_on_invalid_json))
        system = SYSTEM_PROMPT + _JSON_ONLY_SUFFIX
        for attempt in range(attempts):
            try:
                resp = self._request(lambda: self.client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=system,
                    messages=[{"role": "user", "content": user_prompt}],
                ))
            except anthropic.BadRequestError as e:
                log.warning("messages.create rejected, skipping post: %s", e)
                return []
            if resp is None:
                return []
            self._account(resp)
            if getattr(resp, "stop_reason", None) == "refusal":
                log.warning("model refused extraction (fallback path); zero deals")
                return []
            raw = "".join(
                getattr(block, "text", "")
                for block in getattr(resp, "content", []) or []
                if getattr(block, "type", "") == "text"
            )
            try:
                data = json.loads(_strip_fences(raw))
                return list(LLMDeals.model_validate(data).deals)
            except (ValueError, ValidationError) as e:
                log.warning("invalid JSON/schema on fallback attempt %d/%d: %s",
                            attempt + 1, attempts, e)
        log.warning("fallback JSON invalid after %d attempts; skipping post", attempts)
        return []
