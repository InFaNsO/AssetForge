"""Shared platform → poly-count target mapping used by retopo backends."""

_TARGETS = {
    "mobile":  5_000,
    "indie":   12_000,
    "console": 30_000,
}
_DEFAULT = "indie"


def platform_target(platform: str) -> int:
    return _TARGETS.get(platform, _TARGETS[_DEFAULT])
