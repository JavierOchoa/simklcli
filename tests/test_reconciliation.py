from __future__ import annotations

from pathlib import Path

import pytest

from simklcli.api import SimklResponseError
from simklcli.media import LibraryEntry, ListStatus
from simklcli.snapshots import LibrarySnapshot
from simklcli.tracking import Tracking
from tests.simkl_fixture import SimklFixture


def tracking(server: SimklFixture) -> Tracking:
    return Tracking(
        server.runtime.api_client(),
        server.runtime.snapshots,
        account_id=100,
        access_token="test-token",
        clock=lambda: 100.0,
    )


def test_activities_gate_deltas_ratings_and_removal_comparison(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.put(1, rating=5)
    server.put(3, status="watching")
    service = tracking(server)
    first = service.read()
    paths = [r.url.path for r in server.requests]
    assert paths == [
        "/sync/activities",
        "/sync/all-items/movies",
        "/sync/all-items/shows",
        "/sync/all-items/anime",
    ]
    start = len(server.requests)
    assert service.read().entries == first.entries
    assert [r.url.path for r in server.requests[start:]] == ["/sync/activities"]
    server.library[1]["user_rating"] = None
    server.moved(1, "rated_at")
    start = len(server.requests)
    assert service.read().entries[1].user_rating is None
    assert [r.url.path for r in server.requests[start:]] == [
        "/sync/activities",
        "/sync/ratings/movies",
    ]
    assert server.requests[-1].url.params["date_from"] == first.activities["all"]
    server.library[3]["status"] = "hold"
    server.moved(3, "hold")
    assert service.read().entries[3].list_status is ListStatus.ON_HOLD
    server.library.pop(1)
    server.moved(1, "removed_from_list")
    assert set(service.read().entries) == {3}
    assert server.requests[-1].url.params["extended"] == "simkl_ids_only"


def test_failed_delta_does_not_advance_activity_positions_or_partial_entries(
    tmp_path: Path,
) -> None:
    server = SimklFixture(tmp_path)
    server.put(1)
    service = tracking(server)
    service.read()
    before = server.runtime.snapshots.path(100).read_bytes()
    server.moved(1, "completed")
    server.overrides["/sync/all-items/movies"] = {"movies": ["invalid"]}
    with pytest.raises(SimklResponseError, match="Malformed"):
        service.read()
    assert server.runtime.snapshots.path(100).read_bytes() == before
    server.overrides.clear()
    service.read()
    assert server.runtime.snapshots.path(100).read_bytes() != before


def test_dirty_complete_snapshot_is_reconciled_even_if_activity_clock_unchanged(
    tmp_path: Path,
) -> None:
    server = SimklFixture(tmp_path)
    server.put(1)
    service = tracking(server)
    saved = service.read()
    saved.dirty = True
    server.runtime.snapshots.save(saved)
    server.library[1]["user_rating"] = 9
    assert service.read().entries[1].user_rating == 9
    assert not service.read().dirty


@pytest.mark.parametrize(
    "payload",
    [
        "{bad-json",
        '{"version":2}',
        '{"version":1,"account_id":999}',
        '{"version":1,"account_id":100,"complete":"yes"}',
    ],
)
def test_corrupt_and_incompatible_snapshots_are_rebuilt(tmp_path: Path, payload: str) -> None:
    server = SimklFixture(tmp_path)
    path = server.runtime.snapshots.path(100)
    path.parent.mkdir(parents=True)
    path.write_text(payload)
    assert server.runtime.snapshots.load(100) is None
    assert tracking(server).read().complete


def test_snapshot_restores_anime_state_without_catalog_metadata(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    row = server.put(3, status="watching")
    row["episodes"] = [{"number": 1, "watched_at": None}, {"number": 2, "watched": False}]
    row["watched_episodes_count"] = 1
    entry = LibraryEntry.from_server(row, server.kinds[3])
    saved = LibrarySnapshot(100, {3: entry})
    server.runtime.snapshots.save(saved)
    loaded = server.runtime.snapshots.load(100)
    assert loaded is not None and loaded.entries[3].episodes[0].season is None
    assert loaded.entries[3].item.metadata.keys() == {"ids", "title", "year"}


@pytest.mark.parametrize("bad", [{}, {"all": []}])
def test_invalid_activities_cannot_establish_freshness(tmp_path: Path, bad: object) -> None:
    server = SimklFixture(tmp_path)
    server.overrides["/sync/activities"] = bad
    with pytest.raises(SimklResponseError):
        tracking(server).read()


def test_ids_only_malformed_response_never_deletes_local_entries(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.put(1)
    service = tracking(server)
    saved = service.read()
    server.moved(1, "removed_from_list")
    server.overrides["/sync/all-items"] = {"movies": [{"ids": {"simkl": "not-an-int"}}]}
    with pytest.raises(SimklResponseError, match="IDs-only"):
        service.read()
    assert service.read(offline=True).entries == saved.entries
