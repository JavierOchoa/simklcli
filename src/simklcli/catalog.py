"""Catalog reads and Media Reference resolution. Nothing here persists metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from simklcli.api import SimklClient, SimklResponseError
from simklcli.media import Media, MediaKind, positive_int

PROVIDERS = {
    "simkl",
    "imdb",
    "tmdb",
    "tvdb",
    "mal",
    "anidb",
    "anilist",
    "kitsu",
    "livechart",
    "anisearch",
    "animeplanet",
    "crunchyroll",
    "netflix",
    "hulu",
    "letterboxd",
    "trakt",
}


class AmbiguousReferenceError(ValueError):
    def __init__(self, candidates: list[Media]) -> None:
        super().__init__("Media Reference is ambiguous; use a qualified Simkl ID or --kind/--year.")
        self.candidates = candidates


@dataclass(frozen=True)
class SearchResults:
    items: list[Media]
    next_page: int | None


class Catalog:
    def __init__(self, api: SimklClient, access_token: str | None = None) -> None:
        self.api = api
        self.access_token = access_token or api.access_token

    def search(
        self,
        query: str,
        *,
        kind: MediaKind | None = None,
        year: int | None = None,
        limit: int = 10,
        page: int = 1,
    ) -> SearchResults:
        if not query.strip():
            raise ValueError("Search query cannot be empty.")
        if self.access_token is None:
            raise SimklResponseError(
                "AUTH V2 search requires sign-in. Run simkl auth login explicitly."
            )
        items = []
        more = False
        for selected in [kind] if kind is not None else list(MediaKind):
            response = self.api._get(
                f"/search/{'tv' if selected is MediaKind.SHOW else selected.value}",
                params={"q": query, "limit": limit, "page": page, "extended": "full"},
                access_token=self.access_token,
            )
            rows = self.api.checked_json(response)
            if not isinstance(rows, list):
                raise SimklResponseError("Malformed catalog search response.")
            try:
                for row in rows:
                    item = Media.from_catalog(row, selected)
                    if year is None or item.year == year:
                        items.append(item)
                more |= page < int(response.headers.get("X-Pagination-Page-Count", "1"))
            except (AttributeError, TypeError, ValueError) as exc:
                raise SimklResponseError("Malformed catalog search response.") from exc
        # Limit applies per media kind, preserving candidates across all three namespaces.
        return SearchResults(items, page + 1 if more and page < 20 else None)

    def details(self, media_id: int, kind: MediaKind) -> Media:
        payload = self.api.get_json(
            f"/{kind.endpoint}/{media_id}",
            params={"extended": "full"},
            access_token=self.access_token,
        )
        if not payload:
            raise ValueError(f"No {kind.value} found for Simkl ID {media_id}.")
        try:
            item = Media.from_catalog(payload, kind)
            if item.simkl_id != media_id:
                raise ValueError("Catalog returned a different Simkl ID.")
            return item
        except (AttributeError, TypeError, ValueError) as exc:
            raise SimklResponseError("Malformed catalog detail response.") from exc

    def resolve(
        self, reference: str, *, kind: MediaKind | None = None, year: int | None = None
    ) -> Media:
        prefix = reference.split(":", 1)[0]
        if prefix == "slug" and ":" in reference:
            raise ValueError("A Slug is a URL hint, not a Media Reference identity.")
        if ":" not in reference or prefix not in PROVIDERS:
            matches = self.search(reference, kind=kind, year=year, limit=50)
            if not matches.items:
                raise ValueError("No Media Item matched this Media Reference.")
            if len(matches.items) != 1 or matches.next_page is not None:
                raise AmbiguousReferenceError(matches.items)
            chosen = matches.items[0]
            return self.details(chosen.simkl_id, chosen.kind)
        provider, value = reference.split(":", 1)
        if provider not in PROVIDERS or not value.strip():
            raise ValueError("Use a supported provider-qualified External ID or a title.")
        if provider == "tmdb" and kind not in {MediaKind.MOVIE, MediaKind.SHOW}:
            raise ValueError("TMDB references require --kind movie or --kind show.")
        if provider == "simkl":
            try:
                media_id = positive_int(int(value))
            except ValueError as exc:
                raise ValueError("Simkl IDs must be positive integers.") from exc
            if kind is not None:
                return self.details(media_id, kind)
        params: dict[str, str | int] = {provider: value}
        if kind is not None:
            params["type"] = "tv" if kind is MediaKind.SHOW else kind.value
        if self.access_token is None:
            raise SimklResponseError(
                "AUTH V2 reference resolution requires sign-in; use a Simkl ID with --kind "
                "for an anonymous lookup, or run simkl auth login explicitly."
            )
        response = self.api._get("/redirect", params=params, access_token=self.access_token)
        if response.status_code not in {301, 302}:
            self.api.checked_json(response)
            raise ValueError("Simkl could not resolve this Media Reference.")
        target = urlparse(response.headers.get("Location", ""))
        parts = target.path.strip("/").split("/")
        try:
            if target.hostname != "simkl.com" or len(parts) < 2:
                raise ValueError
            resolved_kind = {
                "movies": MediaKind.MOVIE,
                "tv": MediaKind.SHOW,
                "anime": MediaKind.ANIME,
            }[parts[0]]
            media_id = positive_int(int(parts[1]))
            if any(part.startswith("episode-") for part in parts[2:]):
                raise ValueError
            if provider == "simkl" and media_id != int(value):
                raise ValueError
            if kind is not None and kind is not resolved_kind:
                raise ValueError
        except (KeyError, ValueError) as exc:
            raise SimklResponseError("Invalid or mismatched Simkl redirect target.") from exc
        return self.details(media_id, resolved_kind)

    def episodes(self, item: Media) -> list[dict[str, Any]]:
        rows = self.api.get_json(
            f"/{item.kind.endpoint}/episodes/{item.simkl_id}", access_token=self.access_token
        )
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise SimklResponseError("Malformed catalog episode response.")
        return rows
