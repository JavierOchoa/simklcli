"""Canonical media identity and validated Library state, independent of API vocabulary."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class MediaKind(StrEnum):
    MOVIE = "movie"
    SHOW = "show"
    ANIME = "anime"

    @property
    def endpoint(self) -> str:
        return {self.MOVIE: "movies", self.SHOW: "tv", self.ANIME: "anime"}[self]

    @property
    def bucket(self) -> str:
        return {self.MOVIE: "movies", self.SHOW: "shows", self.ANIME: "anime"}[self]

    @property
    def activity(self) -> str:
        return "tv_shows" if self is self.SHOW else self.bucket


class ListStatus(StrEnum):
    WATCHING = "watching"
    PLAN_TO_WATCH = "plan-to-watch"
    ON_HOLD = "on-hold"
    DROPPED = "dropped"
    COMPLETED = "completed"

    @property
    def api_value(self) -> str:
        return {self.PLAN_TO_WATCH: "plantowatch", self.ON_HOLD: "hold"}.get(self, self.value)

    @classmethod
    def from_api(cls, value: object) -> ListStatus:
        return cls({"plantowatch": "plan-to-watch", "hold": "on-hold"}.get(str(value), str(value)))


def validate_status(kind: MediaKind, status: ListStatus) -> None:
    if kind is MediaKind.MOVIE and status in {ListStatus.WATCHING, ListStatus.ON_HOLD}:
        raise ValueError(f"Movies cannot have List Status {status.value}.")


def positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("Expected a positive integer.")
    return value


def simkl_id(payload: dict[str, Any]) -> int:
    ids = payload.get("ids", {})
    return positive_int(ids.get("simkl", ids.get("simkl_id")))


@dataclass(frozen=True)
class Media:
    simkl_id: int
    title: str
    year: int | None
    kind: MediaKind
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def from_catalog(cls, payload: dict[str, Any], kind: MediaKind) -> Media:
        title = payload.get("title")
        year = payload.get("year")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Catalog response is missing a title.")
        if year is not None:
            positive_int(year)
        return cls(simkl_id(payload), title, year, kind, payload)

    def identity(self) -> dict[str, Any]:
        return {
            "simkl_id": self.simkl_id,
            "title": self.title,
            "year": self.year,
            "kind": self.kind.value,
        }

    @property
    def standalone(self) -> bool:
        return self.kind is MediaKind.MOVIE or (
            self.kind is MediaKind.ANIME
            and (
                self.metadata.get("anime_type") == "movie"
                or self.metadata.get("ep_count") == 1
                or self.metadata.get("total_episodes") == 1
            )
        )


@dataclass(frozen=True)
class WatchedEpisode:
    number: int
    season: int | None
    watched_at: str | None = None


@dataclass(frozen=True)
class LibraryEntry:
    item: Media
    list_status: ListStatus
    watched: bool
    last_watched_at: str | None
    user_rating: int | None
    episodes: tuple[WatchedEpisode, ...] = ()
    watched_episodes_count: int = 0

    def payload(self) -> dict[str, Any]:
        return {
            **self.item.identity(),
            "list_status": self.list_status.value,
            "watched": self.watched,
            "last_watched_at": self.last_watched_at,
            "user_rating": self.user_rating,
            "episodes": [asdict(e) for e in self.episodes],
            "watched_episodes_count": self.watched_episodes_count,
        }

    @classmethod
    def from_server(cls, row: dict[str, Any], kind: MediaKind) -> LibraryEntry:
        item_data = row.get("movie") or row.get("show") or row.get("anime")
        if not isinstance(item_data, dict):
            raise ValueError("Library entry is missing media identity.")
        if kind is MediaKind.SHOW and ("anime_type" in row or item_data.get("type") == "anime"):
            kind = MediaKind.ANIME
        item = Media.from_catalog(item_data, kind)
        status = ListStatus.from_api(row.get("status"))
        validate_status(kind, status)
        rating = row.get("user_rating")
        if rating is not None and not 1 <= positive_int(rating) <= 10:
            raise ValueError("Invalid User Rating in Library response.")
        last_watched = row.get("last_watched_at")
        if last_watched is not None and not isinstance(last_watched, str):
            raise ValueError("Invalid Last Watched At.")
        episodes: list[WatchedEpisode] = []
        seasons = row.get("seasons", [])
        if "episodes" in row:
            seasons = [*seasons, {"number": 1, "episodes": row["episodes"]}]
        for season in seasons:
            season_number = season["number"]
            if (
                not isinstance(season_number, int)
                or isinstance(season_number, bool)
                or season_number < 0
            ):
                raise ValueError("Invalid Season in Library response.")
            for episode in season.get("episodes", []):
                if episode.get("watched", True):
                    episodes.append(
                        WatchedEpisode(
                            positive_int(episode["number"]),
                            season_number if kind is MediaKind.SHOW else None,
                            episode.get("watched_at", episode.get("last_watched_at")),
                        )
                    )
        count = row.get("watched_episodes_count", len(episodes))
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("Invalid Watched State count.")
        return cls(
            item,
            status,
            bool(last_watched or count or episodes),
            last_watched,
            rating,
            tuple(episodes),
            count,
        )


def library_entries(payload: dict[str, Any]) -> dict[int, LibraryEntry]:
    if set(payload) - {kind.bucket for kind in MediaKind}:
        raise ValueError("Unrecognized Library response shape.")
    entries = {}
    for kind in MediaKind:
        rows = payload.get(kind.bucket, [])
        if not isinstance(rows, list):
            raise ValueError("Invalid Library response bucket.")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid Library entry.")
            entry = LibraryEntry.from_server(row, kind)
            entries[entry.item.simkl_id] = entry
    return entries
