"""The session store: what a session must remember, in memory and on disk."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from pricetime.fix import tags
from pricetime.fix.store import FileStore, InMemoryStore, SessionStore, StoredMessage
from pricetime.fix.tags import MsgType

REPORT = StoredMessage(
    sequence=2,
    msg_type=MsgType.EXECUTION_REPORT,
    body=((tags.EXEC_ID, "E1"),),
    sending_time="20260921-09:15:01.000",
    admin=False,
)
HEARTBEAT = StoredMessage(
    sequence=3,
    msg_type=MsgType.HEARTBEAT,
    body=(),
    sending_time="20260921-09:15:31.000",
    admin=True,
)


@pytest.fixture(params=["memory", "file"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[SessionStore]:
    """Both stores must behave the same way, so every test here runs against both."""
    if request.param == "memory":
        yield InMemoryStore()
        return
    with FileStore(tmp_path / "session.jsonl") as file_store:
        yield file_store


def test_a_new_store_starts_at_one(store: SessionStore) -> None:
    assert store.next_outbound == 1
    assert store.next_inbound == 1


def test_recording_moves_the_numbers_on(store: SessionStore) -> None:
    store.record_outbound(REPORT)
    store.record_inbound(4)

    assert store.next_outbound == 3
    assert store.next_inbound == 5


def test_stored_messages_come_back_in_order(store: SessionStore) -> None:
    store.record_outbound(REPORT)
    store.record_outbound(HEARTBEAT)

    assert store.between(1, 9) == [REPORT, HEARTBEAT]
    assert store.between(3, 3) == [HEARTBEAT]


def test_the_expected_number_can_be_moved_forward(store: SessionStore) -> None:
    store.expect_inbound(7)

    assert store.next_inbound == 7


def test_a_reset_starts_again_from_one(store: SessionStore) -> None:
    store.record_outbound(REPORT)
    store.record_inbound(4)

    store.reset()

    assert store.next_outbound == 1
    assert store.next_inbound == 1
    assert store.between(1, 9) == []


def test_syncing_is_always_allowed(store: SessionStore) -> None:
    store.record_outbound(REPORT)

    store.sync()

    assert store.next_outbound == 3


def test_a_file_store_reads_back_everything_it_recorded(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    with FileStore(path) as store:
        store.record_outbound(REPORT)
        store.record_outbound(HEARTBEAT)
        store.record_inbound(5)
        store.expect_inbound(9)

    with FileStore(path) as reopened:
        assert reopened.next_outbound == 4
        assert reopened.next_inbound == 9
        assert reopened.between(1, 9) == [REPORT, HEARTBEAT]


def test_a_file_store_reads_back_a_reset(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    with FileStore(path) as store:
        store.record_outbound(REPORT)
        store.reset()
        store.record_outbound(REPORT)

    with FileStore(path) as reopened:
        assert reopened.next_outbound == 3
        assert reopened.between(1, 9) == [REPORT]


def test_closing_a_file_store_twice_is_harmless(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "session.jsonl")
    store.record_outbound(REPORT)

    store.close()
    store.close()

    with FileStore(tmp_path / "session.jsonl") as reopened:
        assert reopened.next_outbound == 3
