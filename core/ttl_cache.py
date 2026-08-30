"""Generic in-memory TTL cache for read-heavy, slow-to-compute service
functions whose underlying data doesn't change during a demo/session
(analytics aggregates, forecasts, early-warning scans) — each one was
live-measured at ~2.7-3.3s per call, recomputed from scratch on every single
request even though nothing in the underlying CaseMaster data had changed
between calls. Deliberately process-local, not Redis/disk-persisted: this
app runs as a single Catalyst AppSail instance, not a multi-process/
multi-node deployment, so there's no cross-process cache-consistency problem
to solve, and adding an external cache dependency for a single-instance app
would be pure complexity with no real benefit.
"""
import functools
import logging
import threading
import time

logger = logging.getLogger("TTLCache")

_DEFAULT_TTL_SECONDS = 15 * 60

_store: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()


def _make_key(fn, args, kwargs) -> str:
    return f"{fn.__module__}.{fn.__qualname__}:{args!r}:{sorted(kwargs.items())!r}"


def ttl_cached(ttl_seconds: int = _DEFAULT_TTL_SECONDS):
    """Decorator: cache a function's return value in-process for ttl_seconds,
    keyed by its arguments. Safe for functions whose result depends only on
    their arguments (every target here is a pure read over CaseMaster/etc.,
    no side effects)."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = _make_key(fn, args, kwargs)
            with _lock:
                entry = _store.get(key)
                if entry is not None and time.time() < entry[0]:
                    return entry[1]
            result = fn(*args, **kwargs)
            with _lock:
                _store[key] = (time.time() + ttl_seconds, result)
            return result
        wrapper.__wrapped_uncached__ = fn
        return wrapper
    return decorator


def clear(prefix: str | None = None) -> int:
    """Manual cache-bust. With no prefix, clears everything; with one, clears
    only keys for that module/function (e.g. 'services.analytics_service').
    Returns how many entries were removed."""
    with _lock:
        if prefix is None:
            n = len(_store)
            _store.clear()
            return n
        keys = [k for k in _store if k.startswith(prefix)]
        for k in keys:
            del _store[k]
        return len(keys)


def stats() -> dict:
    with _lock:
        now = time.time()
        return {
            "entries": len(_store),
            "keys": [{"key": k, "expires_in_seconds": max(0, round(exp - now))} for k, (exp, _) in _store.items()],
        }
