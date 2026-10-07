from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from simklcli.cli import create_app
from simklcli.rate_control import CrossProcessRateGate
from tests.simkl_fixture import SimklFixture


def test_tokens_for_same_account_share_cross_process_rate_scope(tmp_path: Path) -> None:
    waits: list[float] = []
    first = CrossProcessRateGate(tmp_path, client_id="app", clock=lambda: 100.0, sleep=waits.append)
    second = CrossProcessRateGate(
        tmp_path, client_id="app", clock=lambda: 100.0, sleep=waits.append
    )
    first.bind_account("token-one", 123)
    second.bind_account("token-two", 123)
    with first.limit("POST", "token-one"):
        pass
    with second.limit("POST", "token-two"):
        pass
    assert waits == [1.0]


def test_account_binding_preserves_token_validation_request_cadence(tmp_path: Path) -> None:
    waits: list[float] = []
    gate = CrossProcessRateGate(tmp_path, client_id="app", clock=lambda: 100.0, sleep=waits.append)
    with gate.limit("POST", "token"):
        pass
    gate.bind_account("token", 123)
    with gate.limit("POST", "token"):
        pass
    assert waits == [1.0]


def test_unexpected_episode_preservation_failure_reports_captured_state(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.put(3, status="watching", rating=8)

    def changed_parent(request: httpx.Request) -> httpx.Response:
        response = server.handle(request)
        if request.url.path == "/sync/history/remove":
            server.library[3]["user_rating"] = None
        return response

    server.runtime.transport = httpx.MockTransport(changed_parent)
    result = CliRunner().invoke(
        create_app(lambda: server.runtime),
        ["watched", "unmark", "simkl:3", "--episode", "1", "--json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "partial_or_unknown"
    assert payload["captured_state"]["user_rating"] == 8
    assert "rerunning is safe" not in result.stderr


def test_auth_login_json_keeps_pin_progress_on_stderr_and_result_on_stdout(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.runtime.credentials.delete()
    server.overrides["/oauth/pin"] = {
        "result": "OK",
        "user_code": "ABCDE",
        "verification_url": "https://simkl.com/pin",
        "expires_in": 900,
        "interval": 5,
    }
    server.overrides["/oauth/pin/ABCDE"] = {"result": "OK", "access_token": "test-token"}
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["auth", "login", "--storage", "file", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["authenticated"] is True
    assert "ABCDE" in result.stderr and "test-token" not in result.output


@pytest.mark.parametrize("mode", ["environment", "keyring", "cancel"])
def test_auth_login_json_errors_emit_one_payload(tmp_path: Path, mode: str) -> None:
    server = SimklFixture(tmp_path)
    server.runtime.credentials.delete()
    args = ["auth", "login", "--json"]
    if mode == "environment":
        server.runtime.environ = {"SIMKL_ACCESS_TOKEN": "test-token"}
    elif mode == "cancel":
        server.overrides["/oauth/pin"] = {
            "result": "OK",
            "user_code": "ABCDE",
            "verification_url": "https://simkl.com/pin",
            "expires_in": 900,
            "interval": 5,
        }

        def interrupt(_: float) -> None:
            raise KeyboardInterrupt

        server.runtime.sleep = interrupt
        args += ["--storage", "file"]
    result = CliRunner().invoke(create_app(lambda: server.runtime), args)
    assert result.exit_code == (130 if mode == "cancel" else 1)
    assert json.loads(result.stdout)["error"]
    assert server.runtime.credentials.read() is None


def test_titles_with_colons_are_not_mistaken_for_external_ids(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.catalog[1]["title"] = "Movie: Subtitle"
    result = CliRunner().invoke(
        create_app(lambda: server.runtime),
        ["lookup", "Movie: Subtitle", "--kind", "movie", "--json"],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["item"]["title"] == "Movie: Subtitle"


def test_successful_write_then_revocation_reports_remote_success(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    wrote = False

    def revoked_after_write(request: httpx.Request) -> httpx.Response:
        nonlocal wrote
        if wrote and request.method == "GET":
            return httpx.Response(401, json={"error": "user_token_failed"})
        response = server.handle(request)
        if request.url.path == "/sync/ratings":
            wrote = True
        return response

    server.runtime.transport = httpx.MockTransport(revoked_after_write)
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["rating", "set", "simkl:1", "8", "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["remote_success"] is True
    assert server.runtime.credentials.read() is None


def test_failed_snapshot_write_keeps_remote_success_and_dirty_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = SimklFixture(tmp_path)
    app = create_app(lambda: server.runtime)
    assert CliRunner().invoke(app, ["library", "list", "--json"]).exit_code == 0

    def disk_failure(_: object) -> None:
        raise PermissionError("Snapshot directory is read-only")

    monkeypatch.setattr(server.runtime.snapshots, "save", disk_failure)
    result = CliRunner().invoke(app, ["rating", "set", "simkl:1", "8", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["remote_success"] is True
    snapshot = server.runtime.snapshots.load(100)
    assert snapshot is not None and snapshot.dirty


def test_rewatch_pins_an_existing_active_session(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    row = server.put(2, status="completed", watched=True)

    def active_session(request: httpx.Request) -> httpx.Response:
        if (
            request.url.path == "/sync/all-items/shows"
            and request.url.params.get("allow_rewatch") == "yes"
        ):
            return httpx.Response(
                200,
                json={
                    "shows": [
                        {**row, "is_rewatch": True, "rewatch_status": "active", "rewatch_id": 7}
                    ]
                },
            )
        return server.handle(request)

    server.runtime.transport = httpx.MockTransport(active_session)
    result = CliRunner().invoke(
        create_app(lambda: server.runtime),
        ["watched", "mark", "simkl:2", "--season", "1", "--episode", "1", "--rewatch", "--json"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(server.posts()[-1].content)["shows"][0]["rewatch_id"] == 7
