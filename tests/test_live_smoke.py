from pathlib import Path

import httpx
import pytest

from scripts.live_smoke import run_smoke
from tests.simkl_fixture import SimklFixture


@pytest.mark.parametrize("initial", ["absent", "planned", "completed"])
def test_live_smoke_restores_initial_state_using_offline_transport(
    tmp_path: Path, initial: str
) -> None:
    server = SimklFixture(tmp_path)
    if initial != "absent":
        server.put(
            1,
            status="completed" if initial == "completed" else "plantowatch",
            watched=initial == "completed",
            rating=9,
        )
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
    receipt.write_text('{"passed": true, "restored": true}')
    with pytest.raises(Exception, match="rerunning is safe"):
        run_smoke(server.runtime, "simkl:1", 100, receipt)
    assert server.library[1]["status"] == "dropped" and server.library[1]["user_rating"] == 9
    assert not receipt.exists()


def test_live_smoke_rejects_status_move_that_does_not_clear_watched(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)

    def keep_watched_on_move(request: httpx.Request) -> httpx.Response:
        watched_at = server.library.get(1, {}).get("last_watched_at")
        response = server.handle(request)
        if request.url.path == "/sync/add-to-list" and watched_at:
            server.library[1]["last_watched_at"] = watched_at
        return response

    server.runtime.transport = httpx.MockTransport(keep_watched_on_move)
    receipt = tmp_path / "receipt.json"
    with pytest.raises(RuntimeError, match="did not clear Watched State"):
        run_smoke(server.runtime, "simkl:1", 100, receipt)
    assert 1 not in server.library
    assert not receipt.exists()
