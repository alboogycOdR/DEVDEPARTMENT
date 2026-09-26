"""tests/test_test_env_scrub.py — portable env scrub (oikonomos 3e8c3e79 idea)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import test_env_scrub as tes  # noqa: E402


def test_scrub_removes_matching_and_keeps_others(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("DEVTEAM_TG_TOKEN", "secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "secret")
    monkeypatch.setenv("UNRELATED_FOO", "keep")
    removed = tes.scrub()
    for name in (
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "DEVTEAM_TG_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "SLACK_BOT_TOKEN",
    ):
        assert name in removed
        assert name not in os.environ
    assert os.environ.get("UNRELATED_FOO") == "keep"


def test_liveness_raises_if_matching_var_survives(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    monkeypatch.setattr(
        tes, "matching_names", lambda pattern=None: ["GEMINI_API_KEY"]
    )
    with pytest.raises(RuntimeError, match="env scrub inert"):
        tes.scrub()


def test_custom_pattern(monkeypatch):
    monkeypatch.setenv("FOO_SECRET", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "y")
    removed = tes.scrub(pattern=r"^FOO_")
    assert "FOO_SECRET" in removed
    assert "FOO_SECRET" not in os.environ
    assert os.environ.get("GEMINI_API_KEY") == "y"


def test_conftest_invokes_scrub():
    src = (Path(__file__).resolve().parents[1] / "tests" / "conftest.py").read_text(
        encoding="utf-8"
    )
    assert "test_env_scrub" in src
    assert "scrub(" in src
