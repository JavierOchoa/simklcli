from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from simklcli.api import PartialWriteError, SimklClient, SimklResponseError, _retry_after_seconds
from simklcli.catalog import Catalog
from simklcli.media import LibraryEntry, Media, MediaKind, library_entries
from simklcli.targets import select_target
from tests.simkl_fixture import SimklFixture


@pytest.mark.parametrize(
    "changes",
    [
        {"movie": None},
        {"status": "watching"},
        {"user_rating": 11},
        {"last_watched_at": 5},
        {"seasons": [{"number": -1}]},
        {"watched_episodes_count": -1},
    ],
)
def test_malformed_library_state_is_rejected(tmp_path: Path, changes: dict[str, Any]) -> None:
    server = SimklFixture(tmp_path)
    row = {**server.put(1), **changes}
    with pytest.raises(ValueError):
        LibraryEntry.from_server(row, MediaKind.MOVIE)


def test_library_bucket_and_display_identity_validation() -> None:
    with pytest.raises(ValueError):
        library_entries({"movies": {}})
    with pytest.raises(ValueError):
        Media.from_catalog({"title": "Title", "year": True, "ids": {"simkl": 1}}, MediaKind.MOVIE)


@pytest.mark.parametrize(
    "payload", [[], {}, {"added": {}, "not_found": None}, {"added": {}, "not_found": {"movies": 3}}]
)
def test_malformed_post_success_is_never_claimed_as_success(payload: object) -> None:
    api = SimklClient(
        client_id="test",
        transport=httpx.MockTransport(lambda request: httpx.Response(201, json=payload)),
    )
    with pytest.raises(PartialWriteError):
        api.write_state("/sync/ratings", {"movies": []}, access_token="test")


def test_error_body_is_checked_even_when_http_status_is_success() -> None:
    api = SimklClient(
        client_id="test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"error": "wrong_parameter"})
        ),
    )
    with pytest.raises(SimklResponseError, match="wrong_parameter"):
        api.get_json("/movies/1")


def test_http_date_retry_after_is_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("simklcli.api.time.time", lambda: 100.0)
    assert (
        _retry_after_seconds(
            httpx.Response(503, headers={"Retry-After": "Thu, 01 Jan 1970 00:02:00 GMT"})
        )
        == 20.0
    )


@pytest.mark.parametrize(
    "row",
    [
        {"aired": "1970-01-01T00:00:00Z"},
        {"date": "1970-01-01"},
        {},
        {"aired": "2999-01-01"},
    ],
)
def test_bulk_mark_uses_only_aired_episodes(row: dict[str, Any]) -> None:
    api = SimklClient(
        client_id="test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[{"episode": 1, **row}])
        ),
    )
    catalog = Catalog(api)
    item = Media(1, "Anime", 1999, MediaKind.ANIME)
    if row.get("aired", row.get("date", "")).startswith("1970"):
        assert select_target(
            catalog, item, season=None, episode=None, all_episodes=True
        ).coordinates == ((None, 1),)
    else:
        with pytest.raises(ValueError, match="No applicable"):
            select_target(catalog, item, season=None, episode=None, all_episodes=True)
