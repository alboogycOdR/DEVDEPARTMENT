"""Tests for scripts/notify.py's Wave A-remainder amendment: P2 escalations
get an actionable "Reply: /answer TASK-NNN <your decision>" line appended."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from notify import append_reply_hint  # noqa: E402


class TestAppendReplyHint:
    def test_p2_gets_reply_line(self):
        msg = "TASK-016 blocked: SPEC_AMBIGUITY — human answer needed"
        out = append_reply_hint("P2", msg)
        assert out.startswith(msg)
        assert "Reply: /answer TASK-016 <your decision>" in out

    def test_p1_untouched(self):
        msg = "TASK-016 reached max_rework"
        assert append_reply_hint("P1", msg) == msg

    def test_p0_untouched(self):
        msg = "WAVE COMPLETE — all 5 tasks done."
        assert append_reply_hint("P0", msg) == msg

    def test_p2_without_task_id_untouched(self):
        # Defensive: not every conceivable P2 message references a task.
        msg = "Something went wrong, no task ID here"
        assert append_reply_hint("P2", msg) == msg

    def test_p2_uses_first_task_id_found(self):
        msg = "TASK-005 and TASK-006 both blocked: OWNERSHIP_CONFLICT"
        out = append_reply_hint("P2", msg)
        assert "Reply: /answer TASK-005 <your decision>" in out
        assert "TASK-006" not in out.split("Reply:")[1]

    def test_reply_line_is_appended_not_prepended(self):
        msg = "TASK-009 blocked: repeated OWNERSHIP_CONFLICT"
        out = append_reply_hint("P2", msg)
        lines = out.splitlines()
        assert lines[0] == msg
        assert lines[1].startswith("Reply:")
