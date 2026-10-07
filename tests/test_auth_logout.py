from pathlib import Path

import pytest
from typer.testing import CliRunner

from simklcli.cli import create_app
from simklcli.runtime import Runtime
from simklcli.storage import Account, CredentialSource


def test_logout_deletes_local_credential_account_metadata_and_snapshot(tmp_path: Path) -> None:
    runtime = Runtime.for_testing(config_dir=tmp_path / "config", data_dir=tmp_path / "data")
    runtime.credentials.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )
    snapshot = runtime.snapshots.path(12345)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text('{"version":1}', encoding="utf-8")

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "logout", "--yes"])

    assert result.exit_code == 0
    assert runtime.credentials.read() is None
    assert not snapshot.exists()
    assert "remote Access Token remains active or unverified" in result.stdout
    assert "Connected Apps" in " ".join(result.stdout.split())


def test_logout_explains_that_environment_override_cannot_be_removed(tmp_path: Path) -> None:
    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        environ={"SIMKL_ACCESS_TOKEN": "environment-secret"},
    )

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "logout", "--yes"])

    assert result.exit_code == 1
    assert "SIMKL_ACCESS_TOKEN is active" in result.stderr
    assert "cannot be removed by logout" in result.stderr
    assert "environment-secret" not in result.output


def test_logout_can_cancel_without_deleting_local_state(tmp_path: Path) -> None:
    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config", data_dir=tmp_path / "data", interactive=True
    )
    runtime.credentials.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "logout"], input="n\n")

    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert runtime.credentials.read() is not None


def test_logout_removes_persisted_state_but_not_environment_override(tmp_path: Path) -> None:
    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        environ={"SIMKL_ACCESS_TOKEN": "environment-secret"},
    )
    runtime.credentials.write(
        access_token="stored-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "logout", "--yes"])

    assert result.exit_code == 0
    assert runtime.credentials.read() is None
    assert "SIMKL_ACCESS_TOKEN is still active" in result.stderr
    assert "environment-secret" not in result.output


def test_logout_keeps_credentials_when_snapshot_deletion_fails(tmp_path: Path) -> None:
    runtime = Runtime.for_testing(config_dir=tmp_path / "config", data_dir=tmp_path / "data")
    runtime.credentials.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )
    snapshot = runtime.snapshots.path(12345)
    snapshot.mkdir(parents=True)

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "logout", "--yes"])

    assert result.exit_code == 1
    assert runtime.credentials.read() is not None


def test_logout_clears_metadata_and_snapshot_when_keyring_token_is_missing(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "auth.json").write_text(
        '{"version":1,"storage":"keyring","account":{"id":12345,"name":"Javier"}}',
        encoding="utf-8",
    )
    runtime = Runtime.for_testing(config_dir=config_dir, data_dir=tmp_path / "data")
    snapshot = runtime.snapshots.path(12345)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text('{"version":1}', encoding="utf-8")

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "logout", "--yes"])

    assert result.exit_code == 0
    assert not (config_dir / "auth.json").exists()
    assert not snapshot.exists()


def test_logout_interrupt_restores_snapshot_and_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = Runtime.for_testing(config_dir=tmp_path / "config", data_dir=tmp_path / "data")
    runtime.credentials.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )
    snapshot = runtime.snapshots.path(12345)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text('{"version":1}', encoding="utf-8")

    def interrupt_delete() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(runtime.credentials, "delete", interrupt_delete)

    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "logout", "--yes"])

    assert result.exit_code == 130
    assert runtime.credentials.read() is not None
    assert snapshot.read_text(encoding="utf-8") == '{"version":1}'
