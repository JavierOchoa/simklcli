from __future__ import annotations

from typing import Any

import httpx
import pytest

from simklcli.api import SimklClient, SimklResponseError
from simklcli.catalog import AmbiguousReferenceError, Catalog
from simklcli.media import MediaKind


def catalog_with(handler: Any) -> Catalog:
    return Catalog(
        SimklClient(client_id="test-app", transport=httpx.MockTransport(handler)),
        access_token="test-token",
    )


def test_redirect_resolution_does_not_follow_to_web_or_use_search_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/redirect":
            assert request.url.params["tmdb"] == "5"
            assert request.url.params["type"] == "tv"
            return httpx.Response(
                302, headers={"Location": "https://simkl.com/tv/20/response-slug"}
            )
        assert request.url.path == "/tv/20"
        return httpx.Response(200, json={"title": "Show", "year": None, "ids": {"simkl_id": 20}})

    item = catalog_with(handler).resolve("tmdb:5", kind=MediaKind.SHOW)
    assert item.simkl_id == 20 and item.kind is MediaKind.SHOW
    assert len(seen) == 2


@pytest.mark.parametrize(
    "location,kind",
    [
        ("https://elsewhere.test/movies/1/title", None),
        ("https://simkl.com/movies/not-an-id/title", None),
        ("https://simkl.com/movies/0/title", None),
        ("https://simkl.com/slug", None),
        ("https://simkl.com/anime/1/title", MediaKind.MOVIE),
        ("https://simkl.com/unknown/1/title", None),
        ("https://simkl.com/tv/20/title/season-1/episode-1", None),
    ],
)
def test_bad_redirects_fail_without_fetching_destination(
    location: str, kind: MediaKind | None
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(301, headers={"Location": location})

    with pytest.raises(SimklResponseError, match="redirect"):
        catalog_with(handler).resolve("imdb:tt123", kind=kind)
    assert len(requests) == 1


def test_simkl_reference_cannot_silently_resolve_to_a_different_parent_id() -> None:
    catalog = catalog_with(
        lambda request: httpx.Response(301, headers={"Location": "https://simkl.com/tv/20/parent"})
    )
    with pytest.raises(SimklResponseError, match="redirect"):
        catalog.resolve("simkl:21")


@pytest.mark.parametrize("reference", ["slug:hello", "imdb:", "simkl:-1", "simkl:no", "tmdb:3"])
def test_invalid_references_fail_before_network(reference: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Unexpected network request")

    with pytest.raises(ValueError):
        catalog_with(handler).resolve(reference)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"ids": {"simkl": 2}, "title": "wrong"},
        {"ids": {"simkl": 1}, "title": "", "year": 1999},
    ],
)
def test_missing_or_malformed_details_fail(payload: object) -> None:
    with pytest.raises((ValueError, SimklResponseError)):
        catalog_with(lambda request: httpx.Response(200, json=payload)).details(1, MediaKind.MOVIE)


def test_search_pages_and_remaining_candidates_prevent_arbitrary_resolution() -> None:
    row = {"title": "Movie", "year": 1999, "ids": {"simkl_id": 1}, "endpoint_type": "movies"}
    catalog = catalog_with(
        lambda request: httpx.Response(200, json=[row], headers={"X-Pagination-Page-Count": "3"})
    )
    results = catalog.search("Movie", kind=MediaKind.MOVIE, page=1)
    assert results.next_page == 2
    assert results.items[0].kind is MediaKind.MOVIE
    with pytest.raises(AmbiguousReferenceError):
        catalog.resolve("Movie", kind=MediaKind.MOVIE)
    assert catalog.search("Movie", kind=MediaKind.MOVIE, page=20).next_page is None
    assert catalog.search("Movie", kind=MediaKind.MOVIE, year=2000).items == []


@pytest.mark.parametrize("payload", [{}, [None], [{"title": "a", "ids": {}}]])
def test_malformed_search_is_not_an_empty_result(payload: object) -> None:
    with pytest.raises(SimklResponseError):
        catalog_with(lambda request: httpx.Response(200, json=payload)).search("Movie")


def test_empty_title_and_no_matching_reference_are_distinct() -> None:
    catalog = catalog_with(lambda request: httpx.Response(200, json=[]))
    with pytest.raises(ValueError, match="empty"):
        catalog.search(" ")
    with pytest.raises(ValueError, match="No Media"):
        catalog.resolve("Missing")
    with pytest.raises(ValueError, match="could not resolve"):
        catalog.resolve("imdb:tt1")


@pytest.mark.parametrize("payload", [None, {}, [None]])
def test_malformed_episode_lists_fail(payload: object) -> None:
    from simklcli.media import Media

    with pytest.raises(SimklResponseError):
        catalog_with(lambda request: httpx.Response(200, json=payload)).episodes(
            Media(1, "Show", None, MediaKind.SHOW)
        )
