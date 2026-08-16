"""Session-wide test fencing.

**Why this file exists (2026-08-16).** `git` searches for a repository by
walking UP from the current directory. Tests that shell out to `git` inside a
pytest `tmp_path` therefore find whatever repository happens to sit above the
system temp directory — and on a machine whose HOME is itself a git repo, that
is the user's personal home history.

That is not hypothetical. It was measured on the machine this pack is developed
on: **1,266 commits of pytest fixture files** had accumulated in
`C:\\Users\\<user>`'s history, mixed into a real project's 52 commits, purely
because the suite ran. It also made a test pass for the wrong reason — a
"non-repo must fail" assertion that actually succeeded by committing into the
ancestor repo, with only the (remote-less) push failing.

`GIT_CEILING_DIRECTORIES` stops git's upward walk at the temp root, so a test
fixture can only ever reach a repository the test itself created. Set once, at
import time, before any test runs. Six of eight git-invoking test modules had no
guard of their own; putting it here means new tests inherit it automatically
rather than each author having to remember.

This is test hygiene, not a substitute for the production guard in
`tg_commands.git_commit_and_push_detailed()`, which independently refuses to
commit unless the path it was handed IS the root of its own work tree.
"""
from __future__ import annotations

import os
import tempfile

_TEMP_ROOT = os.path.realpath(tempfile.gettempdir())
_existing = os.environ.get("GIT_CEILING_DIRECTORIES")
os.environ["GIT_CEILING_DIRECTORIES"] = (
    f"{_TEMP_ROOT}{os.pathsep}{_existing}" if _existing else _TEMP_ROOT
)
