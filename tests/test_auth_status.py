import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Thread

import httpx
import pytest
from typer.testing import CliRunner

from simklcli.cli import create_app
from simklcli.runtime import Runtime
from simklcli.storage import Account, CredentialSource


def make_runtime(tmp_path: Path, *, environ: dict[str, str] | None = None) -> Runtime:
    return Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        environ=environ,
    )


def test_status_reports_that_no_account_is_authenticated(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "status"])

    assert result.exit_code == 1
    assert "No Authenticated Account" in result.stdout
    assert "simkl auth login" in result.stdout


def test_json_status_reports_unauthenticated_as_one_payload(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "status", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "authenticated": False,
        "account": None,
        "credential_source": None,
        "checked_online": False,
    }
    assert result.stderr == ""


def test_status_reports_persisted_account_without_network_access(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.credentials.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "status", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "authenticated": True,
        "account": {"id": 12345, "name": "Javier"},
        "credential_source": "file",
        "checked_online": False,
    }

    human_result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "status"])
    assert human_result.exit_code == 0
    assert "Javier · account 12345" in human_result.stdout
    assert "Access Token not checked online" in human_result.stdout


def test_status_check_validates_token_and_refreshes_display_name(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/settings"
        return httpx.Response(
            200,
            json={"user": {"name": "Javier Ochoa"}, "account": {"id": 12345}},
        )

    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        environ={"SIMKL_CLIENT_ID": "registered-client"},
        transport=httpx.MockTransport(handler),
    )
    runtime.credentials.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "status", "--check", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["account"] == {"id": 12345, "name": "Javier Ochoa"}
    assert json.loads(result.stdout)["checked_online"] is True
    persisted = runtime.credentials.read()
    assert persisted is not None
    assert persisted.account == Account(id=12345, name="Javier Ochoa")


def test_status_check_json_reports_local_refresh_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"user": {"name": "Javier Ochoa"}, "account": {"id": 12345}},
            )
        ),
    )
    runtime.credentials.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )

    def fail_write(path: Path, payload: dict[str, object]) -> None:
        del path, payload
        raise OSError("disk failure")

    monkeypatch.setattr("simklcli.storage.atomic_write_json", fail_write)

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "status", "--check", "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "check_failed"
    assert "disk failure" in result.stderr


def test_status_check_json_reports_transaction_lock_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = make_runtime(tmp_path)

    @contextmanager
    def fail_transaction() -> Iterator[None]:
        raise OSError("lock failure")
        yield

    monkeypatch.setattr(runtime.credentials, "account_transaction", fail_transaction)

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "status", "--check", "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "credential_storage_error"
    assert "lock failure" in result.stderr


def test_status_check_cannot_resurrect_credentials_after_concurrent_logout(
    tmp_path: Path,
) -> None:
    logout_attempted = Event()
    logout_finished = Event()
    logout_threads: list[Thread] = []
    concurrent_runtime = make_runtime(tmp_path)

    def logout() -> None:
        logout_attempted.set()
        with concurrent_runtime.credentials.account_transaction():
            concurrent_runtime.credentials.delete()
        logout_finished.set()

    def handler(request: httpx.Request) -> httpx.Response:
        thread = Thread(target=logout)
        logout_threads.append(thread)
        thread.start()
        assert logout_attempted.wait(timeout=1)
        logout_finished.wait(timeout=0.1)
        return httpx.Response(
            200,
            json={"user": {"name": "Javier Ochoa"}, "account": {"id": 12345}},
        )

    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        transport=httpx.MockTransport(handler),
    )
    runtime.credentials.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "status", "--check", "--json"],
    )

    assert result.exit_code == 0
    logout_threads[0].join(timeout=2)
    assert logout_finished.is_set()
    assert runtime.credentials.read() is None


def test_confirmed_invalid_persisted_token_clears_account_and_snapshot(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": "user_token_failed", "code": 401, "message": "revoked"},
        )

    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        transport=httpx.MockTransport(handler),
    )
    runtime.credentials.write(
        access_token="revoked-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )
    snapshot = runtime.snapshots.path(12345)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text('{"version":1}', encoding="utf-8")

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "status", "--check", "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "authenticated": False,
        "account": None,
        "credential_source": "file",
        "checked_online": True,
        "error": "invalid_or_revoked",
    }
    assert "invalid or revoked" in result.stderr
    assert "simkl auth login" in result.stderr
    assert runtime.credentials.read() is None
    assert not snapshot.exists()


def test_environment_token_takes_precedence_without_exposing_or_persisting_it(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path, environ={"SIMKL_ACCESS_TOKEN": "environment-secret"})

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "status", "--json"])

    assert result.exit_code == 0
    assert "environment-secret" not in result.output
    assert json.loads(result.stdout) == {
        "authenticated": True,
        "account": None,
        "credential_source": "environment",
        "checked_online": False,
    }


def test_environment_override_still_reports_locally_recorded_account(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path, environ={"SIMKL_ACCESS_TOKEN": "environment-secret"})
    runtime.credentials.write(
        access_token="stored-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "status", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["account"] == {"id": 12345, "name": "Javier"}
    assert json.loads(result.stdout)["credential_source"] == "environment"


def test_environment_override_remains_active_when_local_metadata_is_corrupt(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "auth.json").write_text("not-json", encoding="utf-8")
    runtime = make_runtime(tmp_path, environ={"SIMKL_ACCESS_TOKEN": "environment-secret"})

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "status", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["credential_source"] == "environment"
    assert json.loads(result.stdout)["account"] is None


def test_invalid_environment_token_does_not_delete_shadowed_persistent_account(
    tmp_path: Path,
) -> None:
    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        environ={"SIMKL_ACCESS_TOKEN": "revoked-environment-token"},
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"error": "user_token_failed"})
        ),
    )
    runtime.credentials.write(
        access_token="still-stored-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "status", "--check"])

    assert result.exit_code == 1
    assert "environment variable was not" in result.stderr
    assert "removed" in result.stderr
    persisted = runtime.credentials.read()
    assert persisted is not None
    assert persisted.access_token == "still-stored-token"


def test_status_check_rejects_account_identity_change(tmp_path: Path) -> None:
    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"user": {"name": "Someone else"}, "account": {"id": 99999}},
            )
        ),
    )
    runtime.credentials.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "status", "--check", "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "check_failed"
    assert "different account" in result.stderr
    persisted = runtime.credentials.read()
    assert persisted is not None
    assert persisted.account == Account(id=12345, name="Javier")


def test_status_reports_corrupt_local_metadata_without_traceback(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "auth.json").write_text("not-json", encoding="utf-8")
    runtime = make_runtime(tmp_path)

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "status", "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "credential_storage_error"
    assert "Stored account metadata could not be read" in result.stderr
    assert "Traceback" not in result.output
