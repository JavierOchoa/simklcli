import json
import os
import stat
from pathlib import Path
from threading import Event, Thread

import pytest

import simklcli.filesystem as filesystem_module
import simklcli.storage as storage_module
from simklcli.storage import (
    Account,
    CredentialRepository,
    CredentialSource,
    CredentialStorageError,
    EmptyKeyring,
)


class MemoryKeyring:
    priority = 1

    def __init__(self) -> None:
        self.password: str | None = None

    def get_password(self, service: str, username: str) -> str | None:
        assert (service, username) == ("simklcli", "access-token")
        return self.password

    def set_password(self, service: str, username: str, password: str) -> None:
        assert (service, username) == ("simklcli", "access-token")
        self.password = password

    def delete_password(self, service: str, username: str) -> None:
        assert (service, username) == ("simklcli", "access-token")
        self.password = None


class BrokenKeyring(MemoryKeyring):
    def get_password(self, service: str, username: str) -> str | None:
        raise RuntimeError("backend failed")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("backend failed")


class InterruptingKeyring(MemoryKeyring):
    def set_password(self, service: str, username: str, password: str) -> None:
        super().set_password(service, username, password)
        raise KeyboardInterrupt


def test_plaintext_credentials_round_trip_in_an_owner_only_file(tmp_path: Path) -> None:
    repository = CredentialRepository(tmp_path / "config", EmptyKeyring())

    repository.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )

    credential = repository.read()
    assert credential is not None
    assert credential.access_token == "secret-token"
    assert credential.account == Account(id=12345, name="Javier")
    assert credential.source is CredentialSource.FILE
    if os.name != "nt":
        mode = stat.S_IMODE((tmp_path / "config" / "auth.json").stat().st_mode)
        assert mode == 0o600


def test_keyring_credentials_keep_token_out_of_account_metadata(tmp_path: Path) -> None:
    keyring = MemoryKeyring()
    repository = CredentialRepository(tmp_path / "config", keyring)
    repository.ensure_available(CredentialSource.KEYRING)

    repository.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.KEYRING,
    )

    metadata = json.loads((tmp_path / "config" / "auth.json").read_text(encoding="utf-8"))
    assert "access_token" not in metadata
    assert repository.read() is not None
    assert repository.delete() == Account(id=12345, name="Javier")
    assert keyring.password is None


def test_corrupt_account_metadata_is_reported_as_storage_error(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "auth.json").write_text("not-json", encoding="utf-8")
    repository = CredentialRepository(config_dir, EmptyKeyring())

    with pytest.raises(CredentialStorageError, match="could not be read"):
        repository.read()


def test_repository_refuses_empty_or_environment_credentials(tmp_path: Path) -> None:
    repository = CredentialRepository(tmp_path / "config", EmptyKeyring())

    with pytest.raises(CredentialStorageError, match="empty Access Token"):
        repository.write(
            access_token="",
            account=Account(id=12345, name="Javier"),
            source=CredentialSource.FILE,
        )
    with pytest.raises(CredentialStorageError, match="cannot be persisted"):
        repository.write(
            access_token="environment-token",
            account=Account(id=12345, name="Javier"),
            source=CredentialSource.ENVIRONMENT,
        )


def test_keyring_backend_failures_never_trigger_plaintext_fallback(tmp_path: Path) -> None:
    repository = CredentialRepository(tmp_path / "config", BrokenKeyring())

    with pytest.raises(CredentialStorageError, match="No usable OS keyring"):
        repository.ensure_available(CredentialSource.KEYRING)
    with pytest.raises(CredentialStorageError, match="No usable OS keyring"):
        repository.write(
            access_token="secret-token",
            account=Account(id=12345, name="Javier"),
            source=CredentialSource.KEYRING,
        )
    assert not (tmp_path / "config" / "auth.json").exists()


def test_keyring_write_rolls_back_when_metadata_cannot_be_persisted(tmp_path: Path) -> None:
    config_path = tmp_path / "not-a-directory"
    config_path.write_text("occupied", encoding="utf-8")
    keyring = MemoryKeyring()
    repository = CredentialRepository(config_path, keyring)

    with pytest.raises(OSError):
        repository.write(
            access_token="secret-token",
            account=Account(id=12345, name="Javier"),
            source=CredentialSource.KEYRING,
        )

    assert keyring.password is None


def test_missing_keyring_token_and_missing_metadata_are_not_authenticated(tmp_path: Path) -> None:
    repository = CredentialRepository(tmp_path / "config", MemoryKeyring())
    assert repository.read() is None
    assert repository.delete() is None

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "auth.json").write_text(
        json.dumps(
            {
                "version": 1,
                "storage": "keyring",
                "account": {"id": 12345, "name": "Javier"},
            }
        ),
        encoding="utf-8",
    )
    assert repository.read() is None


def test_keyboard_interrupt_rolls_back_new_keyring_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keyring = MemoryKeyring()
    repository = CredentialRepository(tmp_path / "config", keyring)
    original_write = filesystem_module.atomic_write_json

    def interrupt_after_write(path: Path, payload: dict[str, object]) -> None:
        original_write(path, payload)
        raise KeyboardInterrupt

    monkeypatch.setattr(storage_module, "atomic_write_json", interrupt_after_write)

    with pytest.raises(KeyboardInterrupt):
        repository.write(
            access_token="secret-token",
            account=Account(id=12345, name="Javier"),
            source=CredentialSource.KEYRING,
        )

    assert keyring.password is None
    assert not (tmp_path / "config" / "auth.json").exists()


def test_keyboard_interrupt_inside_keyring_write_is_rolled_back(tmp_path: Path) -> None:
    keyring = InterruptingKeyring()
    repository = CredentialRepository(tmp_path / "config", keyring)

    with pytest.raises(KeyboardInterrupt):
        repository.write(
            access_token="secret-token",
            account=Account(id=12345, name="Javier"),
            source=CredentialSource.KEYRING,
        )

    assert keyring.password is None
    assert not (tmp_path / "config" / "auth.json").exists()


def test_keyboard_interrupt_rolls_back_plaintext_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = CredentialRepository(tmp_path / "config", EmptyKeyring())
    original_write = filesystem_module.atomic_write_json

    def interrupt_after_write(path: Path, payload: dict[str, object]) -> None:
        original_write(path, payload)
        raise KeyboardInterrupt

    monkeypatch.setattr(storage_module, "atomic_write_json", interrupt_after_write)

    with pytest.raises(KeyboardInterrupt):
        repository.write(
            access_token="secret-token",
            account=Account(id=12345, name="Javier"),
            source=CredentialSource.FILE,
        )

    assert repository.read() is None


def test_failed_display_name_refresh_preserves_existing_keyring_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keyring = MemoryKeyring()
    repository = CredentialRepository(tmp_path / "config", keyring)
    repository.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.KEYRING,
    )

    def fail_write(path: Path, payload: dict[str, object]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(storage_module, "atomic_write_json", fail_write)

    with pytest.raises(OSError, match="disk full"):
        repository.write(
            access_token="secret-token",
            account=Account(id=12345, name="Javier Ochoa"),
            source=CredentialSource.KEYRING,
        )

    assert keyring.password == "secret-token"
    assert repository.read() is not None
    assert repository.read().account == Account(id=12345, name="Javier")  # type: ignore[union-attr]


def test_account_transaction_is_cross_process(tmp_path: Path) -> None:
    first = CredentialRepository(tmp_path / "config", MemoryKeyring())
    second = CredentialRepository(tmp_path / "config", MemoryKeyring())
    attempted = Event()
    entered = Event()

    def enter_second_transaction() -> None:
        attempted.set()
        with second.account_transaction():
            entered.set()

    with first.account_transaction():
        thread = Thread(target=enter_second_transaction)
        thread.start()
        assert attempted.wait(timeout=1)
        assert not entered.wait(timeout=0.1)

    thread.join(timeout=2)
    assert entered.is_set()


def test_keyring_delete_failure_restores_token_and_keeps_logout_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keyring = MemoryKeyring()
    repository = CredentialRepository(tmp_path / "config", keyring)
    repository.write(
        access_token="secret-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.KEYRING,
    )
    original_unlink = Path.unlink

    def fail_metadata_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name == "auth.json":
            raise OSError("disk failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_metadata_unlink)

    with pytest.raises(OSError, match="disk failure"):
        repository.delete()

    assert keyring.password == "secret-token"
    assert repository.read() is not None
