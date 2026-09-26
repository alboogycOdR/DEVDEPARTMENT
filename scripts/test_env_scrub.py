"""Remove operator environment variables from the test process.

Port of the portable idea in oikonomos 3e8c3e79 (which patched a
project-specific `scripts/test-isolated.ps1` that pointed at a Postgres test
DB). That runner is NOT ported — Wave G-C territory. This module is the
pack-generic equivalent: drop matching vars from `os.environ` and fail if
any match survives (liveness check).

Default pattern covers DEVTEAM_*, TELEGRAM_*, SLACK_*, ANTHROPIC_*, OPENAI_*,
GEMINI_*. Tests that need one of these set it themselves after the session
fixture runs.
"""
from __future__ import annotations

import os
import re

DEFAULT_PATTERN = r"^(DEVTEAM_|TELEGRAM_|SLACK_|ANTHROPIC_|OPENAI_|GEMINI_)"


def matching_names(pattern: str | None = None) -> list[str]:
    """Return current os.environ keys that match the scrub pattern."""
    pat = re.compile(pattern or DEFAULT_PATTERN)
    return [name for name in os.environ if pat.search(name)]


def scrub(pattern: str | None = None) -> list[str]:
    """Remove matching vars from this process. Raise if any still match.

    Returns the names that were removed. Process scope only — the parent
    shell and user-scope environment are untouched.
    """
    names = matching_names(pattern)
    for name in names:
        os.environ.pop(name, None)
    survivors = matching_names(pattern)
    if survivors:
        raise RuntimeError(
            "env scrub inert: " + ", ".join(survivors) + " still set"
        )
    return names
