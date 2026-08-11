from pathlib import Path
from threading import Event, Thread

from filelock import FileLock

from simklcli.snapshots import LibrarySnapshotStore


def test_snapshot_deletion_waits_for_the_account_lock(tmp_path: Path) -> None:
    store = LibrarySnapshotStore(tmp_path)
    snapshot = store.path(12345)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text('{"version":1}', encoding="utf-8")
    lock_dir = tmp_path / "snapshot-locks"
    lock_dir.mkdir()
    started = Event()

    def delete_snapshot() -> None:
        started.set()
        store.delete(12345)

    with FileLock(lock_dir / "12345.lock"):
        thread = Thread(target=delete_snapshot)
        thread.start()
        assert started.wait(timeout=1)
        thread.join(timeout=0.1)
        assert snapshot.exists()

    thread.join(timeout=2)
    assert not snapshot.exists()
