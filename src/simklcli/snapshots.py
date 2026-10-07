import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filelock import FileLock

from simklcli.filesystem import atomic_write_bytes, atomic_write_json
from simklcli.media import LibraryEntry, ListStatus, Media, MediaKind, WatchedEpisode, positive_int


@dataclass
class LibrarySnapshot:
    account_id: int
    entries: dict[int, LibraryEntry] = field(default_factory=dict)
    activities: dict[str, Any] = field(default_factory=dict)
    complete: bool = False
    dirty: bool = False
    reconciled_at: float = 0.0

    def payload(self) -> dict[str, Any]:
        return {
            "version": 1,
            "account_id": self.account_id,
            "entries": [entry.payload() for entry in self.entries.values()],
            "activities": self.activities,
            "complete": self.complete,
            "dirty": self.dirty,
            "reconciled_at": self.reconciled_at,
        }


class LibrarySnapshotStore:
    """Own per-account Library Snapshot paths and locking."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._locks: dict[int, FileLock] = {}

    def lock(self, account_id: int) -> FileLock:
        lock_dir = self._data_dir / "snapshot-locks"
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if account_id not in self._locks:
            self._locks[account_id] = FileLock(lock_dir / f"{account_id}.lock", mode=0o600)
        return self._locks[account_id]

    def load(self, account_id: int) -> LibrarySnapshot | None:
        with self.lock(account_id):
            try:
                payload = json.loads(self.path(account_id).read_text(encoding="utf-8"))
                if payload["version"] != 1 or payload["account_id"] != account_id:
                    return None
                if (
                    not isinstance(payload["complete"], bool)
                    or not isinstance(payload["dirty"], bool)
                    or not isinstance(payload["activities"], dict)
                    or not isinstance(payload["reconciled_at"], int | float)
                ):
                    return None
                entries = {}
                for row in payload["entries"]:
                    kind = MediaKind(row["kind"])
                    item = Media.from_catalog(
                        {
                            "ids": {"simkl": row["simkl_id"]},
                            "title": row["title"],
                            "year": row["year"],
                        },
                        kind,
                    )
                    # Reuse the API validators and strip any unexpected catalog fields.
                    parsed = LibraryEntry.from_server(
                        {
                            "movie" if kind is MediaKind.MOVIE else "show": {
                                "ids": {"simkl": item.simkl_id},
                                "title": item.title,
                                "year": item.year,
                            },
                            "status": ListStatus(row["list_status"]).api_value,
                            "user_rating": row["user_rating"],
                            "last_watched_at": row["last_watched_at"],
                            "watched_episodes_count": row["watched_episodes_count"],
                            "seasons": [
                                {
                                    "number": e["season"] if e["season"] is not None else 1,
                                    "episodes": [
                                        {"number": e["number"], "watched_at": e["watched_at"]}
                                    ],
                                }
                                for e in row["episodes"]
                            ],
                        },
                        kind,
                    )
                    if not isinstance(row["watched"], bool):
                        return None
                    entries[item.simkl_id] = LibraryEntry(
                        item,
                        parsed.list_status,
                        row["watched"],
                        parsed.last_watched_at,
                        parsed.user_rating,
                        tuple(
                            WatchedEpisode(positive_int(e["number"]), e["season"], e["watched_at"])
                            for e in row["episodes"]
                        ),
                        parsed.watched_episodes_count,
                    )
                if payload["complete"] and "all" not in payload["activities"]:
                    return None
                return LibrarySnapshot(
                    account_id,
                    entries,
                    payload["activities"],
                    payload["complete"],
                    payload["dirty"] or self.dirty_path(account_id).exists(),
                    payload["reconciled_at"],
                )
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                return None

    def save(self, snapshot: LibrarySnapshot) -> None:
        with self.lock(snapshot.account_id):
            atomic_write_json(self.path(snapshot.account_id), snapshot.payload())
            if not snapshot.dirty:
                self.dirty_path(snapshot.account_id).unlink(missing_ok=True)

    def path(self, account_id: int) -> Path:
        return self._data_dir / "accounts" / str(account_id) / "library.json"

    def dirty_path(self, account_id: int) -> Path:
        return self._data_dir / "snapshot-locks" / f"{account_id}.dirty"

    def mark_dirty(self, account_id: int) -> None:
        with self.lock(account_id):
            atomic_write_bytes(self.dirty_path(account_id), b"1")

    def delete(self, account_id: int) -> None:
        with self.lock(account_id):
            self.path(account_id).unlink(missing_ok=True)
            self.dirty_path(account_id).unlink(missing_ok=True)

    @contextmanager
    def reversible_delete(self, account_id: int) -> Iterator[None]:
        """Delete a snapshot, restoring it if the surrounding update fails."""
        with self.lock(account_id):
            path = self.path(account_id)
            previous_content = path.read_bytes() if path.exists() else None
            dirty_path = self.dirty_path(account_id)
            was_dirty = dirty_path.exists()
            try:
                path.unlink(missing_ok=True)
                dirty_path.unlink(missing_ok=True)
                yield
            except BaseException:
                if previous_content is not None:
                    atomic_write_bytes(path, previous_content)
                if was_dirty:
                    atomic_write_bytes(dirty_path, b"1")
                raise
