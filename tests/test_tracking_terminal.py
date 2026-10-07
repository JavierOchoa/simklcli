from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from simklcli.cli import create_app
from simklcli.storage import Account, Credential, CredentialSource
from tests.simkl_fixture import SimklFixture
from tests.test_tracking_commands import invoke


def test_human_output_and_interactive_candidate_selection(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.runtime.interactive = True
    app = create_app(lambda: server.runtime)
    runner = CliRunner()
    for arguments in [
        ["search", "Movie"],
        ["lookup", "simkl:1"],
        ["library", "list"],
        ["library", "repair"],
        ["rating", "set", "simkl:1", "8"],
    ]:
        result = runner.invoke(app, arguments)
        assert result.exit_code == 0, result.output
    choice = runner.invoke(app, ["lookup", "Anime", "--kind", "anime"], input="2\n")
    assert choice.exit_code == 0 and "Anime Movie" in choice.stdout
    bad = runner.invoke(app, ["lookup", "Anime", "--kind", "anime"], input="99\n")
    assert bad.exit_code == 1 and "Invalid candidate" in bad.stderr
    cancelled = runner.invoke(app, ["library", "remove", "simkl:1"], input="n\n")
    assert cancelled.exit_code == 1 and 1 in server.library
    confirmed = runner.invoke(app, ["library", "remove", "simkl:1"], input="y\n")
    assert confirmed.exit_code == 0 and 1 not in server.library


def test_environment_account_is_validated_and_cannot_borrow_shadowed_identity(
    tmp_path: Path,
) -> None:
    server = SimklFixture(tmp_path)
    server.runtime.environ = {"SIMKL_ACCESS_TOKEN": "test-token"}
    server.runtime.credentials.write(
        access_token="other-stored-token",
        account=Account(200, "Other"),
        source=CredentialSource.FILE,
    )
    assert invoke(server, "library", "list")["items"] == []
    assert server.runtime.snapshots.path(100).exists()
    assert not server.runtime.snapshots.path(200).exists()
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["library", "list", "--offline", "--json"]
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["items"] == []


def test_environment_offline_reads_can_use_the_same_persisted_credential(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    invoke(server, "library", "list")
    server.runtime.environ = {"SIMKL_ACCESS_TOKEN": "test-token"}
    before = len(server.requests)
    invoke(server, "library", "list", "--offline")
    assert len(server.requests) == before


def test_missing_credentials_never_start_pin_authorization(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.runtime.credentials.delete()
    result = CliRunner().invoke(create_app(lambda: server.runtime), ["library", "list", "--json"])
    assert result.exit_code == 1 and "auth login explicitly" in result.stderr
    assert not server.requests


def test_invalid_environment_token_keeps_shadowed_credentials(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.runtime.environ = {"SIMKL_ACCESS_TOKEN": "test-token"}
    server.runtime.transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": "user_token_failed"})
    )
    result = CliRunner().invoke(create_app(lambda: server.runtime), ["library", "list", "--json"])
    assert result.exit_code == 1
    assert server.runtime.credentials.read() is not None


def test_credentials_without_account_fail_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = SimklFixture(tmp_path)
    monkeypatch.setattr(
        server.runtime,
        "active_credential",
        lambda: Credential("test-token", CredentialSource.FILE, None),
    )
    result = CliRunner().invoke(create_app(lambda: server.runtime), ["library", "list", "--json"])
    assert result.exit_code == 1
    assert "validated Authenticated Account" in result.stderr


def test_tracking_cancellation_reports_unknown_and_marks_snapshot_dirty(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)

    def cancelled(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            raise KeyboardInterrupt
        return server.handle(request)

    server.runtime.transport = httpx.MockTransport(cancelled)
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["rating", "set", "simkl:1", "8", "--json"]
    )
    assert result.exit_code == 130
    assert json.loads(result.stdout)["outcome"] == "unknown"
    saved = server.runtime.snapshots.load(100)
    assert saved is not None and saved.dirty


def test_show_season_bulk_and_external_episode_payloads(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    invoke(server, "watched", "mark", "simkl:2", "--season", "1", "--all", "--yes")
    payload = json.loads(server.posts()[-1].content)["shows"][0]
    assert payload["seasons"][0]["number"] == 1
    invoke(server, "watched", "unmark", "simkl:2", "--all", "--yes")
    invoke(server, "watched", "mark", "simkl:2", "--episode-id", "tvdb:123")
    assert json.loads(server.posts()[-1].content)["shows"][0]["episodes"] == [
        {"ids": {"tvdb": "123"}}
    ]
    invoke(server, "watched", "unmark", "simkl:2", "--episode-id", "tvdb:123")
    server.fail_post = "lost_absent"
    result = CliRunner().invoke(
        create_app(lambda: server.runtime),
        ["watched", "mark", "simkl:2", "--episode-id", "tvdb:123", "--json"],
    )
    assert result.exit_code == 1 and json.loads(result.stdout)["outcome"] == "unknown"


def test_rewatch_explicit_plan_gate_and_session_confirmation(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    payload = invoke(server, "watched", "mark", "simkl:1", "--rewatch", "--rewatch-id", "7")
    assert payload["remote_success"]
    post = server.posts()[-1]
    assert post.url.params["allow_rewatch"] == "yes"
    assert json.loads(post.content)["movies"][0]["rewatch_id"] == 7
    server.plan = "free"
    before = len(server.posts())
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["watched", "mark", "simkl:1", "--rewatch", "--json"]
    )
    assert result.exit_code == 1 and "PRO or VIP" in result.stderr
    assert len(server.posts()) == before
    result = CliRunner().invoke(
        create_app(lambda: server.runtime),
        ["watched", "mark", "simkl:1", "--rewatch-id", "7", "--json"],
    )
    assert result.exit_code == 1 and "requires --rewatch" in result.stderr
    server.plan = "pro"
    server.rewatch_confirm = False
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["watched", "mark", "simkl:1", "--rewatch", "--json"]
    )
    assert result.exit_code == 1 and json.loads(result.stdout)["outcome"] == "unknown"


def test_json_argument_errors_emit_one_payload(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    for arguments in [
        ["rating", "set", "simkl:1", "11"],
        ["lookup", "simkl:1", "--kind", "episode"],
        ["unknown"],
        ["search"],
    ]:
        result = CliRunner().invoke(create_app(lambda: server.runtime), [*arguments, "--json"])
        assert result.exit_code == 2
        assert json.loads(result.stdout)["error"] == "invalid_arguments"
        assert result.stderr


def test_auth_logout_json_and_noninteractive_confirmation(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["auth", "logout", "--json"], input="y\n"
    )
    assert result.exit_code == 1 and "--yes" in result.stderr
    assert server.runtime.credentials.read() is not None
    assert invoke(server, "auth", "logout", "--yes")["remote_token_revoked"] is False
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["auth", "logout", "--yes", "--json"]
    )
    assert result.exit_code == 1 and json.loads(result.stdout)["error"] == "no_local_credentials"
