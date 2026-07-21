"""tests/test_builder_registry.py — increment 1 of the configurable builder
registry (v4.7). Real temp files, no mocks."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import builder_registry as br  # noqa: E402


def write_cfg(tmp_path: Path, builders) -> Path:
    (tmp_path / "autopilot.json").write_text(
        json.dumps({"builders": builders} if builders is not None else {}),
        encoding="utf-8")
    return tmp_path


S5B_ENTRY = {
    "cli": "claude", "model": "claude-sonnet-5",
    "auth": {"mode": "config_dir", "value": "~/.claude-s5b"},
    "worktree_suffix": "s5b", "branch_suffix": "s5b",
    "briefing": "briefings/S5_BUILD_BRIEFING.md",
    "auto_loads_ambient_context": True, "usage_provider": "claude:s5b",
}


class TestDualShape:
    def test_flat_array_maps_to_legacy_definitions(self, tmp_path):
        reg = br.load_registry(write_cfg(tmp_path, ["GB", "CX", "S5"]))
        assert reg["active"] == ["GB", "CX", "S5"]
        assert reg["defined"]["CX"]["cli"] == "codex"
        assert reg["defined"]["S5"]["auto_loads_ambient_context"] is True

    def test_flat_array_subset(self, tmp_path):
        reg = br.load_registry(write_cfg(tmp_path, ["CX"]))
        assert reg["active"] == ["CX"]
        assert set(reg["defined"]) == {"CX"}

    def test_flat_array_with_unknown_id_kept_active_but_undefined(self, tmp_path):
        reg = br.load_registry(write_cfg(tmp_path, ["GB", "XX"]))
        assert "XX" in reg["active"] and "XX" not in reg["defined"]
        with pytest.raises(br.RegistryError):
            br.resolve("XX", tmp_path)

    def test_registry_object_shape(self, tmp_path):
        builders = {"active": ["S5", "S5B"],
                    "defined": {"S5": dict(br.LEGACY_DEFINITIONS["S5"]), "S5B": S5B_ENTRY}}
        reg = br.load_registry(write_cfg(tmp_path, builders))
        assert reg["active"] == ["S5", "S5B"]
        assert reg["defined"]["S5B"]["auth"]["mode"] == "config_dir"

    def test_defined_but_inactive_is_resolvable_and_in_all_unit_ids(self, tmp_path):
        builders = {"active": ["S5"],
                    "defined": {"S5": dict(br.LEGACY_DEFINITIONS["S5"]), "S5B": S5B_ENTRY}}
        p = write_cfg(tmp_path, builders)
        assert br.active_units(p) == ["S5"]
        assert "S5B" in br.all_unit_ids(p)
        unit, _ = br.resolve("S5B", p)
        assert unit == "S5B"


class TestFailSafe:
    def test_missing_file_falls_back_to_legacy(self, tmp_path):
        reg = br.load_registry(tmp_path)
        assert reg["active"] == ["GB", "CX", "S5"]

    def test_corrupt_json_falls_back(self, tmp_path):
        (tmp_path / "autopilot.json").write_text("{broken", encoding="utf-8")
        assert br.load_registry(tmp_path)["active"] == ["GB", "CX", "S5"]

    def test_absent_builders_key_falls_back(self, tmp_path):
        assert br.load_registry(write_cfg(tmp_path, None))["active"] == ["GB", "CX", "S5"]

    def test_empty_defined_falls_back(self, tmp_path):
        reg = br.load_registry(write_cfg(tmp_path, {"active": [], "defined": {}}))
        assert reg["active"] == ["GB", "CX", "S5"]


class TestFailClosed:
    def test_entry_missing_required_field_raises(self, tmp_path):
        bad = {"active": ["Q1"], "defined": {"Q1": {"cli": "claude"}}}  # no suffixes/briefing
        with pytest.raises(br.RegistryError, match="missing required field"):
            br.load_registry(write_cfg(tmp_path, bad))

    def test_active_listing_undefined_unit_raises(self, tmp_path):
        bad = {"active": ["GB", "GHOST"], "defined": {"GB": dict(br.LEGACY_DEFINITIONS["GB"])}}
        with pytest.raises(br.RegistryError, match="undefined unit"):
            br.load_registry(write_cfg(tmp_path, bad))

    def test_config_dir_auth_without_value_raises(self, tmp_path):
        e = dict(S5B_ENTRY); e["auth"] = {"mode": "config_dir"}
        with pytest.raises(br.RegistryError, match="requires a value"):
            br.load_registry(write_cfg(tmp_path, {"active": ["S5B"], "defined": {"S5B": e}}))

    def test_resolve_unknown_token_raises(self, tmp_path):
        with pytest.raises(br.RegistryError, match="neither a defined unit"):
            br.resolve("mystery", write_cfg(tmp_path, ["GB"]))


class TestResolveShim:
    def test_unit_id_resolves_directly(self, tmp_path):
        unit, e = br.resolve("CX", write_cfg(tmp_path, ["GB", "CX", "S5"]))
        assert (unit, e["cli"]) == ("CX", "codex")

    def test_legacy_cli_name_resolves_to_first_active_match(self, tmp_path):
        """`dispatch.sh claude` keeps meaning S5 even with S5B defined —
        S5 precedes S5B in active order."""
        builders = {"active": ["GB", "CX", "S5", "S5B"],
                    "defined": {**{u: dict(e) for u, e in br.LEGACY_DEFINITIONS.items()},
                                "S5B": S5B_ENTRY}}
        unit, _ = br.resolve("claude", write_cfg(tmp_path, builders))
        assert unit == "S5"

    def test_same_cli_units_resolve_to_distinct_suffixes_and_auth(self, tmp_path):
        """The scenario the whole redesign exists for."""
        builders = {"active": ["S5", "S5B"],
                    "defined": {"S5": dict(br.LEGACY_DEFINITIONS["S5"]), "S5B": S5B_ENTRY}}
        p = write_cfg(tmp_path, builders)
        u1, e1 = br.resolve("S5", p)
        u2, e2 = br.resolve("S5B", p)
        assert e1["worktree_suffix"] != e2["worktree_suffix"]
        assert e1["branch_suffix"] != e2["branch_suffix"]
        assert e1["auth"]["mode"] == "default" and e2["auth"]["mode"] == "config_dir"


class TestDerivedViews:
    def test_all_unit_ids_includes_structural(self, tmp_path):
        ids = br.all_unit_ids(write_cfg(tmp_path, ["GB"]))
        assert {"ORCH", "SV", "GB"} <= ids and "CX" not in ids

    def test_branch_suffixes_map(self, tmp_path):
        suffixes = br.branch_suffixes(write_cfg(tmp_path, ["GB", "CX"]))
        assert suffixes == {"GB": "gb", "CX": "cx"}


class TestCli:
    def test_resolve_prints_kv_block(self, tmp_path, capsys):
        write_cfg(tmp_path, ["GB", "CX", "S5"])
        rc = br._main(["resolve", "grok", "--repo", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "UNIT=GB" in out and "BRANCH_SUFFIX=gb" in out and "AUTH_MODE=default" in out

    def test_resolve_unknown_exits_1(self, tmp_path, capsys):
        write_cfg(tmp_path, ["GB"])
        rc = br._main(["resolve", "nope", "--repo", str(tmp_path)])
        assert rc == 1
        assert "builder_registry:" in capsys.readouterr().err

    def test_active_command(self, tmp_path, capsys):
        write_cfg(tmp_path, ["CX", "GB"])
        assert br._main(["active", "--repo", str(tmp_path)]) == 0
        assert capsys.readouterr().out.strip() == "CX GB"
