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
import shutil
import subprocess
import tempfile
from pathlib import Path

_TEMP_ROOT = os.path.realpath(tempfile.gettempdir())
_existing = os.environ.get("GIT_CEILING_DIRECTORIES")
os.environ["GIT_CEILING_DIRECTORIES"] = (
    f"{_TEMP_ROOT}{os.pathsep}{_existing}" if _existing else _TEMP_ROOT
)

# Port of oikonomos d3f5fc08. Windows: a bare `bash` handed to CreateProcess
# resolves to C:\\Windows\\System32\\bash.exe (the WSL launcher) BEFORE any
# PATH entry, and that launcher fails with "execvpe(/bin/bash) failed" unless
# a WSL distro is installed. Tests that shell out to scripts/*.sh need a real
# bash, so on Windows every subprocess started with the bare name "bash" is
# redirected to Git for Windows' bash by absolute path. Changing PATH cannot
# fix this, because System32 wins first.
def _git_bash() -> str | None:
    if os.name != "nt":
        return None
    candidates = []
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parent.parent  # ...\Git\cmd\git.exe -> ...\Git
        candidates += [root / "usr" / "bin" / "bash.exe", root / "bin" / "bash.exe"]
    candidates += [
        Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        Path(r"C:\Program Files\Git\bin\bash.exe"),
    ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return None


_GIT_BASH = _git_bash()

if _GIT_BASH is not None:
    _real_popen_init = subprocess.Popen.__init__
    _GIT_BASH_BIN = str(Path(_GIT_BASH).parent)  # ...\Git\usr\bin

    def _popen_init(self, args, *a, **kw):
        if isinstance(args, (list, tuple)) and args and args[0] == "bash":
            args = [_GIT_BASH, *args[1:]]
            # Absolute-path Git Bash is not a login shell, so usr/bin (dirname,
            # basename, cygpath) is missing unless we put it on PATH. Pack
            # adaptation of oikonomos d3f5fc08: the redirect is otherwise inert.
            env = kw.get("env")
            env = os.environ.copy() if env is None else dict(env)
            path = env.get("PATH") or env.get("Path") or ""
            if _GIT_BASH_BIN not in path.split(os.pathsep):
                env["PATH"] = _GIT_BASH_BIN + os.pathsep + path
            kw["env"] = env
        _real_popen_init(self, args, *a, **kw)

    subprocess.Popen.__init__ = _popen_init
