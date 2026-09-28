"""Tests for scripts/tg_listener.py — long-polling Telegram listener
(Wave A-remainder). All tests use an injected `fetch` function; nothing here
touches the real network, matching the rest of this codebase's convention of
pure/injectable I/O boundaries."""
import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from tg_listener import TelegramListener  # noqa: E402
import inbox  # noqa: E402


def make_listener(tmp_path, fetch=None, allowlist=None, default_chat="12345", poll=20):
    q = queue.Queue()
    offset_path = tmp_path / ".devteam" / "tg_offset.txt"
    return TelegramListener(
        token="test-token",
        allowlist=allowlist or [],
        default_chat=default_chat,
        out_queue=q,
        offset_path=offset_path,
        poll_interval_seconds=poll,
        fetch=fetch,
        log_fn=lambda msg: None,  # silence during tests
    ), q


def update(update_id, chat_id, text):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


def drained(tmp_path):
    return inbox.drain_inbox(tmp_path, {})


# ---------------------------------------------------------------- basic flow
class TestBasicFlow:
    def test_valid_command_from_allowed_chat_is_durably_queued(self, tmp_path):
        calls = []

        def fetch(method, timeout):
            calls.append(method)
            return {"ok": True, "result": [update(1, 12345, "/status")]}

        listener, q = make_listener(tmp_path, fetch=fetch)
        assert listener.poll_once() is True
        assert len(drained(tmp_path)) == 1
        item = drained(tmp_path)[0]
        assert item["cmd"] == "/status"
        assert item["chat_id"] == "12345"
        assert item["update_id"] == 1

    def test_answer_command_with_args_queued_intact(self, tmp_path):
        def fetch(method, timeout):
            return {"ok": True, "result": [update(1, 12345, "/answer TASK-016 use exponential backoff")]}

        listener, q = make_listener(tmp_path, fetch=fetch)
        listener.poll_once()
        item = drained(tmp_path)[0]
        assert item["cmd"] == "/answer"
        assert item["args"] == "TASK-016 use exponential backoff"

    def test_non_text_update_ignored(self, tmp_path):
        def fetch(method, timeout):
            return {"ok": True, "result": [{"update_id": 1, "message": {"chat": {"id": 12345}, "sticker": {}}}]}

        listener, q = make_listener(tmp_path, fetch=fetch)
        listener.poll_once()
        assert drained(tmp_path) == []

    def test_multiple_updates_in_one_batch(self, tmp_path):
        def fetch(method, timeout):
            return {"ok": True, "result": [
                update(1, 12345, "/status"),
                update(2, 12345, "/board"),
                update(3, 12345, "/digest"),
            ]}

        listener, q = make_listener(tmp_path, fetch=fetch)
        listener.poll_once()
        assert len(drained(tmp_path)) == 3


# ------------------------------------------------------------------ allowlist
class TestAllowlistAtListener:
    def test_unlisted_chat_silently_dropped_no_queue_entry(self, tmp_path):
        def fetch(method, timeout):
            return {"ok": True, "result": [update(1, 99999, "/stop")]}

        listener, q = make_listener(tmp_path, fetch=fetch, default_chat="12345")
        listener.poll_once()
        assert drained(tmp_path) == []
        assert listener.rejected_count == 1

    def test_allowlisted_multi_chat(self, tmp_path):
        def fetch(method, timeout):
            return {"ok": True, "result": [update(1, 222, "/status")]}

        listener, q = make_listener(tmp_path, fetch=fetch, allowlist=["111", "222", "333"], default_chat="999")
        listener.poll_once()
        assert len(drained(tmp_path)) == 1

    def test_mixed_batch_only_allowed_queued(self, tmp_path):
        def fetch(method, timeout):
            return {"ok": True, "result": [
                update(1, 12345, "/status"),   # allowed
                update(2, 99999, "/stop"),     # rejected
                update(3, 12345, "/board"),    # allowed
            ]}

        listener, q = make_listener(tmp_path, fetch=fetch, default_chat="12345")
        listener.poll_once()
        assert len(drained(tmp_path)) == 2
        assert listener.rejected_count == 1

    def test_offset_still_advances_past_rejected_updates(self, tmp_path):
        # A rejected chat must not be able to cause replay by "stalling" the offset.
        def fetch(method, timeout):
            return {"ok": True, "result": [update(5, 99999, "/stop")]}

        listener, q = make_listener(tmp_path, fetch=fetch, default_chat="12345")
        listener.poll_once()
        assert listener.offset == 6


# -------------------------------------------------------------------- offset
class TestOffsetPersistence:
    def test_offset_persisted_to_disk(self, tmp_path):
        def fetch(method, timeout):
            return {"ok": True, "result": [update(100, 12345, "/status"), update(101, 12345, "/board")]}

        listener, q = make_listener(tmp_path, fetch=fetch)
        listener.poll_once()
        assert listener.offset == 102
        offset_file = tmp_path / ".devteam" / "tg_offset.txt"
        assert offset_file.exists()
        assert offset_file.read_text(encoding="utf-8").strip() == "102"

    def test_restart_does_not_replay_old_commands(self, tmp_path):
        def fetch1(method, timeout):
            return {"ok": True, "result": [update(100, 12345, "/status"), update(101, 12345, "/board")]}

        listener1, q1 = make_listener(tmp_path, fetch=fetch1)
        listener1.poll_once()
        assert listener1.offset == 102

        # Simulate a full process restart: brand-new listener instance reading
        # the same offset file. Its fetch should reflect the persisted offset,
        # and if the (simulated) Telegram server only has NEW updates from 102
        # onward, nothing from the old batch is replayed.
        def fetch2(method, timeout):
            return {"ok": True, "result": [update(102, 12345, "/wave")]}

        listener2, q2 = make_listener(tmp_path, fetch=fetch2)
        assert listener2.offset == 102  # picked up from disk, did not reset to 0
        listener2.poll_once()
        assert len(drained(tmp_path)) == 3
        assert [item["cmd"] for item in drained(tmp_path)][-1] == "/wave"
        assert listener2.offset == 103

    def test_missing_offset_file_starts_at_zero(self, tmp_path):
        listener, _ = make_listener(tmp_path, fetch=lambda m, t: {"ok": True, "result": []})
        assert listener.offset == 0

    def test_corrupted_offset_file_falls_back_to_zero(self, tmp_path):
        offset_path = tmp_path / ".devteam" / "tg_offset.txt"
        offset_path.parent.mkdir(parents=True)
        offset_path.write_text("not-a-number", encoding="utf-8")
        listener, _ = make_listener(tmp_path, fetch=lambda m, t: {"ok": True, "result": []})
        assert listener.offset == 0

    def test_offset_not_advanced_when_persist_fails(self, tmp_path, monkeypatch):
        """If the durable inbox write fails, the Telegram offset must not
        advance -- otherwise the next poll would never re-fetch the update
        and the command is lost for good (E-K.1: the offset only advances
        after the inbox file exists)."""
        def fetch(method, timeout):
            return {"ok": True, "result": [update(100, 12345, "/status")]}

        monkeypatch.setattr(inbox, "enqueue", lambda *a, **k: None)
        listener, _ = make_listener(tmp_path, fetch=fetch)
        assert listener.poll_once() is True
        assert listener.offset == 0
        offset_file = tmp_path / ".devteam" / "tg_offset.txt"
        assert not offset_file.exists()
        assert drained(tmp_path) == []

        # The next poll must re-fetch and re-attempt the same update_id -- the
        # unadvanced offset is the mechanism that guarantees this.
        monkeypatch.undo()
        listener.poll_once()
        assert listener.offset == 101
        assert len(drained(tmp_path)) == 1


# -------------------------------------------------------------------- errors
class TestProcessKilledBetweenPersistAndOffsetSave:
    def test_killed_after_persist_before_offset_save_still_executes_exactly_once(self, tmp_path, monkeypatch):
        """A process killed after the durable envelope is written but before
        the offset is saved leaves Telegram's own offset unchanged, so the
        NEXT process re-fetches and re-persists the same update_id -- two
        envelope files for one command, at different filenames (issued_at is
        part of the filename). drain_inbox must still hand it to the
        supervisor exactly once and clean up the duplicate (E-K.1)."""
        def fetch(method, timeout):
            return {"ok": True, "result": [update(100, 12345, "/status")]}

        real_save_offset = TelegramListener._save_offset
        monkeypatch.setattr(TelegramListener, "_save_offset",
                           lambda self, offset: None)  # simulates death before this line
        listener, _ = make_listener(tmp_path, fetch=fetch)
        assert listener.poll_once() is True
        assert listener.offset == 0  # never advanced -- the crash
        first_files = list((tmp_path / ".devteam" / "inbox").glob("*-telegram-100.json"))
        assert len(first_files) == 1

        # Next process: offset file still says 0 (or absent), so Telegram
        # redelivers update 100. A brand-new listener instance re-persists it
        # under a fresh filename (fresh issued_at) -- restore real behaviour.
        monkeypatch.setattr(TelegramListener, "_save_offset", real_save_offset)
        listener2, _ = make_listener(tmp_path, fetch=fetch)
        assert listener2.offset == 0
        assert listener2.poll_once() is True
        assert listener2.offset == 101
        # enqueue's filename is issued_at + command_id: within the same
        # second this collides with the first file and is idempotent (the
        # existing file wins, per inbox.enqueue). Force the slower-but-real
        # path -- two DISTINCT files for the same command id, as a genuine
        # multi-second gap between processes would produce -- by persisting
        # a second envelope under an explicit later issued_at.
        inbox.enqueue(tmp_path, command_id="telegram-100", source="telegram", actor="12345",
                      command="/status", args="", issued_at="2026-01-01T00:00:01Z")
        second_files = list((tmp_path / ".devteam" / "inbox").glob("*-telegram-100.json"))
        assert len(second_files) == 2  # both envelopes on disk, same command id

        accepted = inbox.drain_inbox(tmp_path)
        assert len(accepted) == 1
        assert accepted[0]["id"] == "telegram-100"
        assert inbox.ack(tmp_path, accepted[0])
        # Only one command reaches the supervisor even with two files present.
        assert inbox.drain_inbox(tmp_path) == []
        # The duplicate file was rejected, not silently dropped or executed.
        rejected = list((tmp_path / ".devteam" / "inbox" / "rejected").glob("*-telegram-100.json"))
        assert len(rejected) == 1


class TestFailureHandling:
    def test_network_exception_returns_false_does_not_raise(self, tmp_path):
        def fetch(method, timeout):
            raise OSError("connection refused")

        listener, q = make_listener(tmp_path, fetch=fetch)
        assert listener.poll_once() is False
        assert drained(tmp_path) == []

    def test_api_not_ok_response_returns_false(self, tmp_path):
        def fetch(method, timeout):
            return {"ok": False, "description": "Unauthorized"}

        listener, q = make_listener(tmp_path, fetch=fetch)
        assert listener.poll_once() is False

    def test_malformed_single_update_does_not_stop_batch(self, tmp_path):
        def fetch(method, timeout):
            return {"ok": True, "result": [
                {"update_id": 1, "message": None},  # malformed — message is None
                update(2, 12345, "/status"),          # must still be processed
            ]}

        listener, q = make_listener(tmp_path, fetch=fetch)
        assert listener.poll_once() is True
        assert len(drained(tmp_path)) == 1
        assert drained(tmp_path)[0]["cmd"] == "/status"

    def test_offset_persist_failure_does_not_crash(self, tmp_path, monkeypatch):
        def fetch(method, timeout):
            return {"ok": True, "result": [update(1, 12345, "/status")]}

        listener, q = make_listener(tmp_path, fetch=fetch)

        def boom(*a, **kw):
            raise OSError("disk full")
        monkeypatch.setattr(Path, "write_text", boom)
        # Must not raise; the durable-write boundary also keeps the offset at
        # zero so Telegram can redeliver rather than silently losing it.
        assert listener.poll_once() is True
        assert drained(tmp_path) == []
        assert listener.offset == 0


# ---------------------------------------------------------------- lifecycle
class TestLifecycle:
    def test_stop_sets_event(self, tmp_path):
        listener, _ = make_listener(tmp_path, fetch=lambda m, t: {"ok": True, "result": []})
        assert not listener._stop_event.is_set()
        listener.stop()
        assert listener._stop_event.is_set()

    def test_is_daemon_thread(self, tmp_path):
        listener, _ = make_listener(tmp_path, fetch=lambda m, t: {"ok": True, "result": []})
        assert listener.daemon is True
