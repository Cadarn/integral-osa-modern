"""
Small HTTP helpers shared by the archive downloaders: bounded retries for transient failures.
"""

import asyncio

import httpx

from integral.core.transfer_stats import record_retry

RETRY_ATTEMPTS = 3
RETRY_STATUSES = frozenset({429, 502, 503, 504})

_sleep = asyncio.sleep  # indirection so tests can skip real backoff delays


def describe_error(exc: BaseException) -> str:
    """httpx timeouts stringify to '' - fall back to the exception class name."""
    return str(exc) or type(exc).__name__


async def backoff(attempt: int) -> None:
    record_retry()
    await _sleep(min(2**attempt, 8))


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = 30.0,
    attempts: int = RETRY_ATTEMPTS,
    **kwargs,
) -> httpx.Response:
    """GET with up to `attempts` tries on connection errors/timeouts and 429/5xx gateway errors.
    The last attempt's response is returned (or its exception raised) unchanged."""
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            resp = await client.get(url, timeout=timeout, **kwargs)
        except httpx.TransportError:
            if last:
                raise
            await backoff(attempt)
            continue
        if resp.status_code in RETRY_STATUSES and not last:
            await backoff(attempt)
            continue
        return resp
    raise AssertionError("unreachable")
