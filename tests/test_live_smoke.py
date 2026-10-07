from pathlib import Path

import pytest

from scripts.live_smoke import run_smoke
from tests.simkl_fixture import SimklFixture


@pytest.mark.parametrize("initial", [False, True])
def test_live_smoke_restores_initial_state_using_offline_transport(
    tmp_path: Path, initial: bool
) -> None:
    server = SimklFixture(tmp_path)
    if initial:
        server.put(1, status="dropped", watched=True, rating=9)
    before = server.library.get(1)
    original = (
        None
        if before is None
        else (before["status"], before["user_rating"], before["last_watched_at"])
    )
    receipt = tmp_path / "receipt.json"
    run_smoke(server.runtime, "simkl:1", 100, receipt)
    after = server.library.get(1)
    assert (
        None if after is None else (after["status"], after["user_rating"], after["last_watched_at"])
    ) == original
    assert receipt.exists() and receipt.with_name("smoke-backup.json").exists()


def test_live_smoke_refuses_wrong_account_before_mutation(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    with pytest.raises(ValueError, match="dedicated smoke account"):
        run_smoke(server.runtime, "simkl:1", 999, tmp_path / "receipt.json")
    assert not server.posts()


def test_live_smoke_restores_after_a_failed_step(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.put(1, status="dropped", rating=9)
    server.fail_post = "lost_absent"
    receipt = tmp_path / "receipt.json"
    with pytest.raises(Exception, match="rerunning is safe"):
        run_smoke(server.runtime, "simkl:1", 100, receipt)
    assert server.library[1]["status"] == "dropped" and server.library[1]["user_rating"] == 9
    assert not receipt.exists()
