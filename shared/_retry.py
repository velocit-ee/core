"""Shared retry policy for engine API clients.

Two facts shape this module:

  - Engine workflows are idempotent at the *step* level, not the *request*
    level. Retrying a `POST /reconfigure` is fine because the API treats it
    as "make sure the desired state holds", not as a transactional write.
  - Network and provider APIs hiccup. Proxmox briefly 5xxs during heavy
    bulk operations; OPNsense's reconfigure endpoints occasionally 503 while
    the daemon respawns. Both are transient — retrying within seconds works.

Policy:
  - Up to 3 attempts (initial + 2 retries).
  - Exponential backoff with jitter, capped at 8 s.
  - Retry only on `TransientAPIError` (caller raises this for 5xx + network).
  - Never retry 4xx — those are user/programmer errors and won't change.

Both OPNsense and Proxmox clients import the decorator and the transient
exception class from here so the policy lives in exactly one place.
"""

from __future__ import annotations

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class TransientAPIError(Exception):
    """Marker class for retryable failures.

    Engine API client subclasses raise this (or a subclass thereof) when the
    underlying call hit a 5xx, a connection reset, or a timeout. Anything
    else — 4xx, schema mismatch, authentication failure — must raise the
    non-transient parent class so callers see the failure on the first try.
    """


def transient_retry():
    """Decorator: retry the wrapped callable on TransientAPIError only."""
    return retry(
        retry=retry_if_exception_type(TransientAPIError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
