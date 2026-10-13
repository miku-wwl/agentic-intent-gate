"""Trusted adapter budgets and their explicit, versioned binding identity."""

import math

DEFAULT_EXECUTION_TIMEOUT_SECONDS = 5
MAX_EXECUTION_TIMEOUT_SECONDS = 15
PROFILE_MARKER = ".exec-"


def execution_timeout(value):
    """Accept only built-in finite numbers, excluding bool and numeric coercion."""
    if type(value) not in (int, float):
        raise TypeError("execution_timeout_seconds must be an int or float, not bool")
    if not 0 < value <= MAX_EXECUTION_TIMEOUT_SECONDS:
        raise ValueError("execution_timeout_seconds must be finite and in (0, 15]")
    # Range-check integers before conversion so enormous integers cannot overflow.
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("execution_timeout_seconds must be finite and in (0, 15]")
    return value


def profile_suffix(value):
    return PROFILE_MARKER + format(execution_timeout(value), ".17g").replace("+", "") + "s"


def binding_timeout(revision):
    """Decode only the canonical extended profile; historical revisions are not reinterpreted."""
    base, marker, encoded = revision.rpartition(PROFILE_MARKER)
    try:
        value = execution_timeout(float(encoded[:-1]))
    except (TypeError, ValueError):
        raise ValueError(
            "An extended binding must end in its canonical execution profile"
        ) from None
    if (
        not base
        or not marker
        or value <= DEFAULT_EXECUTION_TIMEOUT_SECONDS
        or revision != base + profile_suffix(value)
    ):
        raise ValueError("An extended binding must end in its canonical execution profile")
    return value
