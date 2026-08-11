from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from filelock import FileLock

from simklcli.filesystem import atomic_write_bytes, atomic_write_json


class CredentialSource(StrEnum):
    ENVIRONMENT = "environment"
    KEYRING = "keyring"
    FILE = "file"


@dataclass(frozen=True)
class Account:
    id: int
    name: str


@dataclass(frozen=True)
class Credential:
    access_token: str
    source: CredentialSource
    account: Account | None


class Keyring(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> None: ...


class EmptyKeyring:
    def get_password(self, service: str, username: str) -> str | None:
        del service, username
        return None

    def set_password(self, service: str, username: str, password: str) -> None:
        del service, username, password
        raise RuntimeError("No keyring is available.")

    def delete_password(self, service: str, username: str) -> None:
        del service, username


class CredentialStorageError(RuntimeError):
    """Persistent credential storage could not be read or updated."""


class CredentialRepository:
    def __init__(self, config_dir: Path, keyring: Keyring) -> None:
        self._path = config_dir / "auth.json"
        self._lock_path = config_dir / "account.lock"
        self._keyring = keyring

    @contextmanager
    def account_transaction(self) -> Iterator[None]:
        self._lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            self._lock_path.parent.chmod(0o700)
        with FileLock(self._lock_path, mode=0o600):
            yield

    def read_account(self) -> Account | None:
        if not self._path.exists():
            return None
        return _account_from_payload(self._read_payload())

    def read(self) -> Credential | None:
        if not self._path.exists():
            return None
        payload = self._read_payload()
        source = CredentialSource(cast(str, payload["storage"]))
        account = _account_from_payload(payload)
        if source is CredentialSource.FILE:
            token = payload.get("access_token")
        else:
            try:
                token = self._keyring.get_password("simklcli", "access-token")
            except Exception as exc:
                raise CredentialStorageError(_KEYRING_UNAVAILABLE_MESSAGE) from exc
        if not isinstance(token, str) or not token:
            return None
        return Credential(access_token=token, source=source, account=account)

    def ensure_available(self, source: CredentialSource) -> None:
        if source is not CredentialSource.KEYRING:
            return
        priority = getattr(self._keyring, "priority", 0)
        try:
            usable = float(priority) > 0
            self._keyring.get_password("simklcli", "access-token")
        except Exception as exc:
            raise CredentialStorageError(_KEYRING_UNAVAILABLE_MESSAGE) from exc
        if not usable:
            raise CredentialStorageError(_KEYRING_UNAVAILABLE_MESSAGE)

    def write(
        self,
        *,
        access_token: str,
        account: Account,
        source: CredentialSource,
    ) -> None:
        if not access_token:
            raise CredentialStorageError("Refusing to persist an empty Access Token.")
        payload: dict[str, object] = {
            "version": 1,
            "storage": source.value,
            "account": {"id": account.id, "name": account.name},
        }
        previous_metadata = self._path.read_bytes() if self._path.exists() else None
        previous_keyring_token: str | None = None
        keyring_changed = False
        if source is CredentialSource.FILE:
            payload["access_token"] = access_token
        elif source is not CredentialSource.KEYRING:
            raise CredentialStorageError("Environment credentials cannot be persisted.")
        try:
            if source is CredentialSource.KEYRING:
                try:
                    previous_keyring_token = self._keyring.get_password("simklcli", "access-token")
                    keyring_changed = previous_keyring_token != access_token
                    if keyring_changed:
                        self._keyring.set_password("simklcli", "access-token", access_token)
                except Exception as keyring_error:
                    raise CredentialStorageError(_KEYRING_UNAVAILABLE_MESSAGE) from keyring_error
            atomic_write_json(self._path, payload)
        except BaseException:
            try:
                _restore_file(self._path, previous_metadata)
                if source is CredentialSource.KEYRING and keyring_changed:
                    if previous_keyring_token is None:
                        self._keyring.delete_password("simklcli", "access-token")
                    else:
                        self._keyring.set_password(
                            "simklcli", "access-token", previous_keyring_token
                        )
            except Exception as rollback_error:
                raise CredentialStorageError(
                    "Credential persistence failed and could not be rolled back completely."
                ) from rollback_error
            raise

    def delete(self) -> Account | None:
        if not self._path.exists():
            return None
        payload = self._read_payload()
        account_payload = payload.get("account")
        account = (
            Account(id=account_payload["id"], name=account_payload["name"])
            if isinstance(account_payload, dict)
            else None
        )
        keyring_token: str | None = None
        keyring_touched = False
        try:
            if payload.get("storage") == CredentialSource.KEYRING.value:
                try:
                    keyring_token = self._keyring.get_password("simklcli", "access-token")
                    if keyring_token is not None:
                        keyring_touched = True
                        self._keyring.delete_password("simklcli", "access-token")
                except Exception as keyring_error:
                    raise CredentialStorageError(
                        "Could not delete the OS keyring credential."
                    ) from keyring_error
            self._path.unlink(missing_ok=True)
        except BaseException:
            if keyring_touched and keyring_token is not None:
                try:
                    self._keyring.set_password("simklcli", "access-token", keyring_token)
                except Exception as rollback_error:
                    raise CredentialStorageError(
                        "Credential deletion failed and could not be rolled back completely."
                    ) from rollback_error
            raise
        return account

    def _read_payload(self) -> dict[str, object]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError
            CredentialSource(payload["storage"])
            account = payload["account"]
            if not isinstance(account, dict):
                raise ValueError
            account_id = account.get("id")
            name = account.get("name")
            if (
                not isinstance(account_id, int)
                or isinstance(account_id, bool)
                or account_id <= 0
                or not isinstance(name, str)
                or not name.strip()
            ):
                raise ValueError
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise CredentialStorageError("Stored account metadata could not be read.") from exc
        return payload


def _restore_file(path: Path, previous_content: bytes | None) -> None:
    if previous_content is None:
        if path.exists():
            path.unlink()
    else:
        atomic_write_bytes(path, previous_content)


_KEYRING_UNAVAILABLE_MESSAGE = (
    "No usable OS keyring is available. Re-run login with --storage file "
    "to explicitly select plaintext-file storage."
)


def _account_from_payload(payload: dict[str, object]) -> Account:
    account_data = payload["account"]
    assert isinstance(account_data, dict)
    return Account(id=account_data["id"], name=account_data["name"])
