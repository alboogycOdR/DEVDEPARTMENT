"""circuit_breaker.py tests — pure stagnation-decision logic.

Companion to test_supervisor.py, which covers the decide()/execute() wiring
with synthetic stagnation_signal dicts. This file is only the arithmetic:
no PLAN.md, no RuntimeState, no git.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import circuit_breaker as cb  # noqa: E402


def sample(changed=False, denials=0):
    return cb.StagnationSample(changed=changed, denials=denials)


class TestUpdateStreak:
    def test_progress_resets_to_zero(self):
        assert cb.update_streak(5, sample(changed=True)) == 0

    def test_no_progress_increments(self):
        assert cb.update_streak(2, sample(changed=False)) == 3

    def test_starts_from_zero(self):
        assert cb.update_streak(0, sample(changed=False)) == 1

    def test_denials_alone_do_not_affect_the_streak_arithmetic(self):
        """The streak only counts ticks; denials feed a separate threshold
        in is_stagnant, not the streak itself."""
        assert cb.update_streak(1, sample(changed=False, denials=50)) == 2


class TestIsStagnant:
    def test_progress_is_never_stagnant_regardless_of_streak(self):
        assert cb.is_stagnant(streak=99, sample=sample(changed=True)) is False

    def test_below_both_thresholds_is_not_stagnant(self):
        assert cb.is_stagnant(streak=1, sample=sample(changed=False, denials=0)) is False

    def test_streak_threshold_trips_alone(self):
        assert cb.is_stagnant(streak=3, sample=sample(changed=False, denials=0)) is True

    def test_one_below_streak_threshold_does_not_trip(self):
        assert cb.is_stagnant(streak=2, sample=sample(changed=False, denials=0)) is False

    def test_denial_threshold_trips_alone_even_with_a_short_streak(self):
        """A stuck permission loop is a faster signal than mere silence —
        it can escalate before the generic no-progress streak would."""
        assert cb.is_stagnant(streak=1, sample=sample(changed=False, denials=2)) is True

    def test_one_below_denial_threshold_does_not_trip_on_denials_alone(self):
        assert cb.is_stagnant(streak=1, sample=sample(changed=False, denials=1)) is False

    def test_custom_thresholds_are_respected(self):
        cfg = {"no_progress_ticks": 10, "denial_ticks": 10}
        assert cb.is_stagnant(streak=3, sample=sample(changed=False, denials=2), cfg=cfg) is False
        assert cb.is_stagnant(streak=10, sample=sample(changed=False, denials=0), cfg=cfg) is True

    def test_missing_cfg_keys_fall_back_to_defaults(self):
        """A caller-supplied cfg dict from autopilot.json might only override
        one key; the other must still use DEFAULT_CIRCUIT_BREAKER_CFG."""
        assert cb.is_stagnant(streak=3, sample=sample(changed=False, denials=0),
                              cfg={"denial_ticks": 999}) is True


class TestRemedialKind:
    def test_first_stagnation_redispatches(self):
        assert cb.remedial_kind(0) == "REDISPATCH_STAGNANT"

    def test_stays_redispatch_below_ceiling(self):
        assert cb.remedial_kind(1) == "REDISPATCH_STAGNANT"

    def test_escalates_at_ceiling(self):
        assert cb.remedial_kind(2) == "ESCALATE_P2"

    def test_escalates_beyond_ceiling(self):
        assert cb.remedial_kind(5) == "ESCALATE_P2"

    def test_custom_ceiling_is_respected(self):
        assert cb.remedial_kind(0, cfg={"max_stagnation_resets": 0}) == "ESCALATE_P2"
