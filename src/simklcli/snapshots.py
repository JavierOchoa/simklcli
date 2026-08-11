from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

from simklcli.filesystem import atomic_write_bytes


class LibrarySnapshotStore:
    """Own per-account Library Snapshot paths and locking."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    def path(self, account_id: int) -> Path:
        return self._data_dir / "accounts" / str(account_id) / "library.json"

    def delete(self, account_id: int) -> None:
        lock_dir = self._data_dir / "snapshot-locks"
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with FileLock(lock_dir / f"{account_id}.lock"):
            self.path(account_id).unlink(missing_ok=True)

    @contextmanager
    def reversible_delete(self, account_id: int) -> Iterator[None]:
        """Delete a snapshot, restoring it if the surrounding update fails."""
        lock_dir = self._data_dir / "snapshot-locks"
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with FileLock(lock_dir / f"{account_id}.lock"):
            path = self.path(account_id)
            previous_content = path.read_bytes() if path.exists() else None
            try:
                path.unlink(missing_ok=True)
                yield
            except BaseException:
                if previous_content is not None:
                    atomic_write_bytes(path, previous_content)
                raise
