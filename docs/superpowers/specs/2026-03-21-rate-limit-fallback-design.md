# Rate Limit Fallback: Cooldown Timer in LLMClient

**Date:** 2026-03-21
**Status:** Approved
**Scope:** `src/llm_client.py` only

## Problem

Running the full CRMArena benchmark (2,140 tasks) generates 10,000-17,000+ API calls.
The primary model (gemini-3.1-pro-preview) has a 1,000 RPM rate limit. Once hit, the
benchmark stalls on HTTP 429 errors. We need the system to automatically fall back to
the cheap tier model and resume using the primary model after a cooldown period.

## Design

### Approach: Cooldown Timer Fallback

When `call()` receives a 429 from the primary tier, the client enters a timed cooldown
and routes subsequent `call()` requests to the cheap client. After the cooldown expires,
it probes the primary model again.

### State Added to `LLMClient.__init__()`

| Attribute | Type | Purpose |
|-----------|------|---------|
| `_primary_cooldown_until` | `float` | Timestamp when primary becomes available (default `0.0`) |
| `_cooldown_duration` | `float` | Seconds to wait before retrying primary (from env var) |
| `_fallback_count` | `int` | Number of calls routed to fallback (observability) |

### Configuration

| Env Var | Default | Purpose |
|---------|---------|---------|
| `LLM_RATE_LIMIT_COOLDOWN` | `60` | Seconds to wait before retrying primary after a 429 |

### Behavior Change: `call()` Method

During fallback, the caller's `model` parameter override is ignored — always use
`self.cheap_model` against the cheap client. Sending a primary model name to the
cheap provider would likely error (different model catalogs).

If `_cheap_client is None` when a 429 is caught, re-raise the original
`openai.RateLimitError` — there is no fallback available.

```
1. Check if now < _primary_cooldown_until:
   YES → if _cheap_client is None: raise RateLimitError
         route to cheap client using self.cheap_model (ignore caller's model param)
         increment _fallback_count, return response
   NO  → try primary client:
         - On success → return response
         - On openai.RateLimitError (HTTP 429):
           a. Set _primary_cooldown_until = now + _cooldown_duration
           b. Log WARNING: "Primary model rate-limited, falling back to cheap tier for {cooldown}s"
           c. If _cheap_client is None: re-raise the RateLimitError
           d. Retry this single call on cheap client using self.cheap_model
           e. Return cheap response
```

### Unchanged: `call_cheap()` Method

No changes. `call_cheap()` already targets the cheap tier. If the cheap model also
returns 429, that is a hard failure and raises as-is.

### Error Detection

Catch `openai.RateLimitError` — the openai SDK maps HTTP 429 to this typed exception.
No manual status code parsing needed. HTTP 503 is NOT treated as a rate limit (Gemini
returns 503 for operational issues unrelated to rate limiting).

### Logging

| Level | When | Message |
|-------|------|---------|
| WARNING | Entering cooldown | `"Primary model rate-limited, falling back to cheap tier for {cooldown}s"` |
| INFO | Cooldown expired, probing primary | `"Primary model cooldown expired, resuming primary tier"` |
| INFO | Metrics reporting | `"Fallback was used {n} times during this session"` |

### Files Changed

- `src/llm_client.py` — add cooldown state, wrap `call()` with fallback logic

No changes to `agent.py`, `executor.py`, `context_filter.py`, or any other file.

### Metrics & Reset

`_fallback_count` is exposed as a property alongside `total_tokens`, `tool_calls`, and
`queries` so benchmark results can report how many calls were degraded to the cheap tier.

`reset_metrics()` must also reset `_fallback_count` to `0`. However,
`_primary_cooldown_until` is NOT reset — the rate limit is account-wide, not
task-specific, so the cooldown should persist across tasks.

### Async Safety

`_primary_cooldown_until` is a plain float read/written across `await` points. This is
safe under asyncio's single-threaded cooperative model — float assignment is atomic within
one thread and there is no preemption between coroutines except at `await`. No lock
required.

## Constraints

- Only HTTP 429 triggers fallback (not 503 or other errors)
- Cooldown is time-based (not request-count-based)
- Automatic switch-back after cooldown expires
- No changes outside `llm_client.py`
