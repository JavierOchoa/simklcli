import json
from pathlib import Path

from typer.testing import CliRunner

from simklcli.cli import create_app
from tests.simkl_fixture import SimklFixture
from tests.test_tracking_commands import invoke


def test_environment_only_account_can_read_its_snapshot_offline_without_persisting_token(
    tmp_path: Path,
) -> None:
    server = SimklFixture(tmp_path)
    server.runtime.credentials.delete()
    server.runtime.environ = {
        "SIMKL_CLIENT_ID": "test-v2-client",
        "SIMKL_ACCESS_TOKEN": "test-token",
    }
    server.put(1, rating=8)
    invoke(server, "library", "list")
    count = len(server.requests)
    assert invoke(server, "library", "list", "--offline")["items"][0]["user_rating"] == 8
    assert len(server.requests) == count
    assert server.runtime.credentials.read() is None
    assert all(
        "test-token" not in path.read_text() for path in server.runtime.data_dir.rglob("*.json")
    )


def test_unvalidated_environment_token_cannot_borrow_another_accounts_snapshot(
    tmp_path: Path,
) -> None:
    server = SimklFixture(tmp_path)
    invoke(server, "library", "list")
    server.runtime.environ = {
        "SIMKL_CLIENT_ID": "test-v2-client",
        "SIMKL_ACCESS_TOKEN": "different-token",
    }
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["library", "list", "--offline", "--json"]
    )
    assert result.exit_code == 1
    assert "online account validation" in json.loads(result.stdout)["message"]
