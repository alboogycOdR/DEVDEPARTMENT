"""v4.8 tests — instinct lifecycle: prune / evolve / promote / status.

Companion to test_instincts.py (which covers parse, render, confidence and
injection). Kept in a separate file so the Wave C suite stays readable.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import instincts as im  # noqa: E402


def mk(inst_id="INST-001", territory=None, confidence=0.6, status="active",
       rule="Always write tests first.", source=None, retired_on=""):
    return im.Instinct(inst_id=inst_id, rule=rule,
                       territory=territory or ["python/**"],
                       confidence=confidence,
                       source=source or ["TASK-001 rework"],
                       status=status, retired_on=retired_on)


def write(repo, instincts, filename=im.INSTINCTS_FILENAME):
    assert im.save_atomic(repo, instincts, filename)


# ------------------------------------------------------ schema compatibility --
class TestRetiredField:
    def test_absent_when_unset_so_old_files_round_trip(self):
        text = mk().render()
        assert "**Retired:**" not in text
        assert im.parse_instincts(text)[0].retired_on == ""

    def test_present_and_parsed_when_set(self):
        text = mk(status="retired", retired_on="2026-01-15").render()
        assert "**Retired:** 2026-01-15" in text
        assert im.parse_instincts(text)[0].retired_on == "2026-01-15"

    def test_malformed_date_is_dropped_not_guessed(self):
        """A hand-edited garbage date must not become 'today' — that would
        silently restart the grace period on an instinct due for archiving."""
        text = mk(status="retired", retired_on="2026-01-15").render()
        text = text.replace("2026-01-15", "last tuesday")
        assert im.parse_instincts(text)[0].retired_on == ""

    def test_save_atomic_round_trips_the_new_field(self, tmp_path):
        write(tmp_path, [mk(status="retired", retired_on="2026-02-02")])
        assert im.load(tmp_path)[0].retired_on == "2026-02-02"


# -------------------------------------------------------------------- prune --
class TestPrune:
    def test_dry_run_writes_nothing(self, tmp_path):
        write(tmp_path, [mk(status="retired")])
        before = (tmp_path / im.INSTINCTS_FILENAME).read_text(encoding="utf-8")
        res = im.prune(tmp_path, today="2026-03-01", apply=False)
        assert res.ok and res.stamped == ["INST-001"]
        assert (tmp_path / im.INSTINCTS_FILENAME).read_text(encoding="utf-8") == before

    def test_stamps_undated_retired_instinct(self, tmp_path):
        write(tmp_path, [mk(status="retired")])
        res = im.prune(tmp_path, today="2026-03-01", apply=True)
        assert res.stamped == ["INST-001"] and res.archived == []
        assert im.load(tmp_path)[0].retired_on == "2026-03-01"

    def test_stamping_does_not_archive_in_the_same_run(self, tmp_path):
        """The grace clock must start from an observation we recorded, or the
        first prune after shipping would archive all history at once."""
        write(tmp_path, [mk(status="retired")])
        im.prune(tmp_path, today="2026-03-01", apply=True)
        assert len(im.load(tmp_path)) == 1
        assert not (tmp_path / im.ARCHIVE_FILENAME).exists()

    def test_archives_after_grace(self, tmp_path):
        write(tmp_path, [mk(status="retired", retired_on="2026-01-01")])
        res = im.prune(tmp_path, today="2026-03-01", apply=True)
        assert res.archived == ["INST-001"]
        assert im.load(tmp_path) == []
        assert [i.inst_id for i in im.load(tmp_path, im.ARCHIVE_FILENAME)] == ["INST-001"]

    def test_waits_inside_grace(self, tmp_path):
        write(tmp_path, [mk(status="retired", retired_on="2026-02-25")])
        res = im.prune(tmp_path, today="2026-03-01", apply=True)
        assert res.waiting == ["INST-001"] and res.archived == []
        assert len(im.load(tmp_path)) == 1

    def test_boundary_day_archives(self, tmp_path):
        write(tmp_path, [mk(status="retired", retired_on="2026-01-01")])
        res = im.prune(tmp_path, today="2026-01-31", apply=True, grace_days=30)
        assert res.archived == ["INST-001"]

    def test_day_before_boundary_waits(self, tmp_path):
        write(tmp_path, [mk(status="retired", retired_on="2026-01-01")])
        res = im.prune(tmp_path, today="2026-01-30", apply=True, grace_days=30)
        assert res.waiting == ["INST-001"]

    def test_never_touches_active_or_probation(self, tmp_path):
        write(tmp_path, [mk("INST-001", status="active"),
                         mk("INST-002", status="probation", retired_on="2020-01-01")])
        res = im.prune(tmp_path, today="2026-03-01", apply=True)
        assert res.archived == [] and res.stamped == []
        assert len(im.load(tmp_path)) == 2

    def test_appends_to_existing_archive(self, tmp_path):
        write(tmp_path, [mk("INST-009", status="retired", retired_on="2020-01-01")],
              im.ARCHIVE_FILENAME)
        write(tmp_path, [mk("INST-010", status="retired", retired_on="2026-01-01")])
        im.prune(tmp_path, today="2026-03-01", apply=True)
        assert sorted(i.inst_id for i in im.load(tmp_path, im.ARCHIVE_FILENAME)) == \
            ["INST-009", "INST-010"]

    def test_ids_are_never_reused_after_archiving(self, tmp_path):
        """The core risk of pruning: load() alone would hand INST-005 out twice."""
        write(tmp_path, [mk("INST-005", status="retired", retired_on="2026-01-01")])
        im.prune(tmp_path, today="2026-03-01", apply=True)
        assert im.load(tmp_path) == []
        assert im.next_id(im.load_all(tmp_path)) == "INST-006"

    def test_fail_open_on_unreadable_store(self, tmp_path):
        (tmp_path / im.INSTINCTS_FILENAME).mkdir()  # a directory, not a file
        res = im.prune(tmp_path, today="2026-03-01", apply=True)
        assert res.ok is True and res.archived == []  # load() already fails soft


# --------------------------------------------------------------- promotion --
class TestPromotionCandidates:
    def test_requires_high_confidence(self):
        low = mk(confidence=0.8, source=["TASK-001 rework", "TASK-002 rework"])
        assert im.promotion_candidates([low]) == []

    def test_requires_multiple_distinct_source_tasks(self):
        """Confidence alone is gameable: one task can bump one instinct to 0.9."""
        single = mk(confidence=1.0, source=["TASK-001 rework"])
        assert im.promotion_candidates([single]) == []

    def test_accepts_high_confidence_multi_source(self):
        good = mk(confidence=0.9, source=["TASK-001 rework", "TASK-002 rework"])
        assert [i.inst_id for i in im.promotion_candidates([good])] == ["INST-001"]

    def test_ignores_probation_and_retired(self):
        srcs = ["TASK-001 rework", "TASK-002 rework"]
        pool = [mk("INST-001", confidence=1.0, source=srcs, status="probation"),
                mk("INST-002", confidence=1.0, source=srcs, status="retired")]
        assert im.promotion_candidates(pool) == []


class TestClustering:
    def test_overlapping_territories_cluster(self):
        a = mk("INST-001", territory=["python/orb/**"])
        b = mk("INST-002", territory=["python/orb/api.py"])
        clusters = im.cluster_by_territory([a, b])
        assert len(clusters) == 1 and len(clusters[0]) == 2

    def test_disjoint_territories_do_not_cluster(self):
        a = mk("INST-001", territory=["python/**"])
        b = mk("INST-002", territory=["web/**"])
        assert len(im.cluster_by_territory([a, b])) == 2

    def test_clusters_transitively(self):
        a = mk("INST-001", territory=["python/**"])
        b = mk("INST-002", territory=["python/orb/**", "web/**"])
        c = mk("INST-003", territory=["web/ui.js"])
        clusters = im.cluster_by_territory([a, b, c])
        assert len(clusters) == 1 and len(clusters[0]) == 3

    def test_territoryless_instincts_are_excluded(self):
        # Built directly: mk()'s `territory or [...]` default would swallow [].
        bare = im.Instinct(inst_id="INST-001", rule="r", territory=[])
        assert im.cluster_by_territory([bare]) == []


class TestAmendmentProposal:
    def test_body_names_every_instinct_and_offers_rejection(self):
        srcs = ["TASK-001 rework", "TASK-002 rework"]
        cluster = [mk("INST-001", confidence=0.9, source=srcs),
                   mk("INST-002", confidence=1.0, source=srcs)]
        body = im.render_promotion_amendment(cluster)
        assert body.startswith("## PROPOSED AMENDMENT")
        assert "INST-001" in body and "INST-002" in body
        assert "**Reject:**" in body

    def test_write_amendment_touches_only_the_pending_dir(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("original\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("original\n", encoding="utf-8")
        amend_id = im.write_amendment(tmp_path, "## PROPOSED AMENDMENT\n\nbody")
        assert amend_id == "AMEND-001"
        assert (tmp_path / im.AMEND_DIR_REL / "AMEND-001.md").is_file()
        assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "original\n"
        assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "original\n"

    def test_amend_ids_increment_alongside_distiller_written_files(self, tmp_path):
        d = tmp_path / im.AMEND_DIR_REL
        d.mkdir(parents=True)
        (d / "AMEND-007.md").write_text("existing\n", encoding="utf-8")
        assert im.write_amendment(tmp_path, "body") == "AMEND-008"

    def test_format_matches_distiller(self, tmp_path):
        """Both writers must produce the same header, or the review tooling
        that reads these files has to special-case their origin."""
        import distiller  # noqa: F401  (import here: heavy autopilot stack)
        im.write_amendment(tmp_path, "body-a")
        distiller.write_amendment(tmp_path, "body-b")
        a = (tmp_path / im.AMEND_DIR_REL / "AMEND-001.md").read_text(encoding="utf-8")
        b = (tmp_path / im.AMEND_DIR_REL / "AMEND-002.md").read_text(encoding="utf-8")
        assert a.splitlines()[2] == b.splitlines()[2] == "**Status:** pending"
        assert im.AMEND_DIR_REL == distiller.AMEND_DIR_REL


# -------------------------------------------------------------------- CLI ----
class TestCli:
    def test_status_reports_counts(self, tmp_path, capsys):
        write(tmp_path, [mk("INST-001", status="active"),
                         mk("INST-002", status="retired")])
        assert im.main(["status", "--repo", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "live: 2" in out and "unstamped" in out

    def test_prune_dry_run_says_so(self, tmp_path, capsys):
        write(tmp_path, [mk(status="retired", retired_on="2020-01-01")])
        assert im.main(["prune", "--repo", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "would archive: INST-001" in out and "dry run" in out
        assert len(im.load(tmp_path)) == 1

    def test_prune_apply_writes(self, tmp_path, capsys):
        write(tmp_path, [mk(status="retired", retired_on="2020-01-01")])
        assert im.main(["prune", "--repo", str(tmp_path), "--apply"]) == 0
        assert "archived: INST-001" in capsys.readouterr().out
        assert im.load(tmp_path) == []

    def test_evolve_reports_when_nothing_qualifies(self, tmp_path, capsys):
        write(tmp_path, [mk(confidence=0.5)])
        assert im.main(["evolve", "--repo", str(tmp_path)]) == 0
        assert "no instinct clears the promotion bar" in capsys.readouterr().out

    def test_evolve_lists_clusters(self, tmp_path, capsys):
        srcs = ["TASK-001 rework", "TASK-002 rework"]
        write(tmp_path, [mk("INST-001", confidence=0.9, source=srcs, territory=["python/**"]),
                         mk("INST-002", confidence=0.9, source=srcs, territory=["python/orb/**"])])
        assert im.main(["evolve", "--repo", str(tmp_path)]) == 0
        out = capsys.readouterr().out
        assert "cluster (2): INST-001, INST-002" in out

    def test_promote_dry_run_prints_without_writing(self, tmp_path, capsys):
        srcs = ["TASK-001 rework", "TASK-002 rework"]
        write(tmp_path, [mk("INST-001", confidence=0.9, source=srcs)])
        assert im.main(["promote", "--repo", str(tmp_path), "--ids", "INST-001"]) == 0
        assert "PROPOSED AMENDMENT" in capsys.readouterr().out
        assert not (tmp_path / im.AMEND_DIR_REL).exists()

    def test_promote_apply_writes_amendment(self, tmp_path, capsys):
        srcs = ["TASK-001 rework", "TASK-002 rework"]
        write(tmp_path, [mk("INST-001", confidence=0.9, source=srcs)])
        assert im.main(["promote", "--repo", str(tmp_path),
                        "--ids", "INST-001", "--apply"]) == 0
        assert "AGENTS.md is UNCHANGED" in capsys.readouterr().out
        assert (tmp_path / im.AMEND_DIR_REL / "AMEND-001.md").is_file()

    def test_promote_unknown_id_is_an_error(self, tmp_path, capsys):
        write(tmp_path, [mk("INST-001")])
        assert im.main(["promote", "--repo", str(tmp_path), "--ids", "INST-999"]) == 1
        assert "unknown instinct id" in capsys.readouterr().err

    def test_inject_still_ignores_archived_instincts(self, tmp_path):
        """Archived == retired, and retired is never injected."""
        write(tmp_path, [mk("INST-001", status="retired", retired_on="2020-01-01")])
        im.prune(tmp_path, today="2026-03-01", apply=True)
        assert im.render_injection(
            im.top_matching(["python/**"], im.load_all(tmp_path))) == ""
