"""Validate selectors and build explicit Episode targets before any write."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from simklcli.catalog import Catalog
from simklcli.media import LibraryEntry, Media, MediaKind, positive_int


def viewing_time(value: str | None) -> str:
    time = (
        datetime.now(UTC) if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if time.tzinfo is None:
        raise ValueError("--at requires an ISO-8601 timestamp with a timezone.")
    return time.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class WatchedTarget:
    coordinates: tuple[tuple[int | None, int], ...] = ()
    bulk: bool = False
    external_id: tuple[str, str] | None = None

    def item_payload(self, item: Media, *, watched_at: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"ids": {"simkl": item.simkl_id}}
        if watched_at is not None:
            payload["watched_at"] = watched_at
        if self.external_id is not None:
            provider, value = self.external_id
            payload["episodes"] = [{"ids": {provider: value}}]
            return payload
        if self.coordinates:
            episodes_by_season: dict[int, list[dict[str, Any]]] = {}
            for season, number in self.coordinates:
                episode: dict[str, Any] = {"number": number}
                if watched_at is not None:
                    episode["watched_at"] = watched_at
                episodes_by_season.setdefault(season if season is not None else 1, []).append(
                    episode
                )
            payload["seasons"] = [
                {"number": season, "episodes": episodes}
                for season, episodes in episodes_by_season.items()
            ]
        return payload

    def matches(self, entry: LibraryEntry | None, *, watched: bool) -> bool | None:
        if self.external_id is not None:
            # The catalog API does not guarantee external episode IDs in its response.
            # A lost write response cannot be verified from a parent-level boolean.
            return None
        if not self.coordinates:
            return (entry is not None and entry.watched) == watched
        actual = set() if entry is None else {(e.season, e.number) for e in entry.episodes}
        return all((coordinate in actual) == watched for coordinate in self.coordinates)


def select_target(
    catalog: Catalog,
    item: Media,
    *,
    season: int | None,
    episode: int | None,
    all_episodes: bool,
    episode_id: str | None = None,
    marking: bool = True,
) -> WatchedTarget:
    if episode_id is not None and (season is not None or episode is not None or all_episodes):
        raise ValueError("--episode-id cannot be combined with other target selectors.")
    if episode is not None and all_episodes:
        raise ValueError("--episode and --all cannot be combined.")
    if item.standalone:
        if season is not None or episode is not None or all_episodes or episode_id is not None:
            raise ValueError("Standalone Media Items do not accept Episode selectors.")
        return WatchedTarget()
    if item.kind is MediaKind.ANIME and season is not None:
        raise ValueError("Anime uses canonical Episode Numbers; omit --season.")
    if not all_episodes and episode is None and episode_id is None:
        raise ValueError("Episodic Media Items require --episode, --season … --all, or --all.")
    if item.kind is MediaKind.SHOW and episode is not None and season is None:
        raise ValueError("A Show Episode requires --season and --episode.")
    if episode_id is not None:
        provider, separator, value = episode_id.partition(":")
        if not separator or provider not in {"tvdb", "anidb"} or not value:
            raise ValueError("--episode-id must be a qualified TVDB or AniDB Episode ID.")
        return WatchedTarget(external_id=(provider, value))
    rows = catalog.episodes(item)
    coordinates: list[tuple[int | None, int]] = []
    for row in rows:
        row_season = row.get("season") if item.kind is MediaKind.SHOW else None
        number = positive_int(row.get("episode", row.get("number")))
        if season is not None and season != row_season:
            continue
        if episode is not None and episode != number:
            continue
        if all_episodes and marking and not _has_aired(row):
            continue
        coordinates.append((row_season, number))
    if not coordinates:
        raise ValueError("No applicable Episodes matched this parent and the selected coordinates.")
    return WatchedTarget(tuple(coordinates), all_episodes)


def _has_aired(row: dict[str, Any]) -> bool:
    if isinstance(row.get("aired"), bool):
        return bool(row["aired"])
    date = row.get("date", row.get("aired"))
    if not isinstance(date, str):
        return False
    aired = datetime.fromisoformat(date.replace("Z", "+00:00"))
    if aired.tzinfo is None:
        aired = aired.replace(tzinfo=UTC)
    return aired <= datetime.now(UTC)
