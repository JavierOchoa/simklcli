from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
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
    access_token: str = field(repr=False)
    source: CredentialSource
    account: Account | None
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: float | None = None
    refresh_expires_at: float | None = None
    client_id: str | None = None
    scope: str | None = None
    invalidated: bool = False

    @property
    def legacy(self) -> bool:
        return self.source is not CredentialSource.ENVIRONMENT and self.client_id is None


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
        self._lock = FileLock(self._lock_path, mode=0o600)
        self._keyring = keyring

    @contextmanager
    def account_transaction(self) -> Iterator[None]:
        self._lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            self._lock_path.parent.chmod(0o700)
        with self._lock:
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
        if payload["version"] == 2:
            if source is CredentialSource.KEYRING:
                try:
                    secrets = json.loads(token)
                except ValueError as exc:
                    raise CredentialStorageError("Stored token pair could not be read.") from exc
            else:
                secrets = payload
            if not isinstance(secrets, dict) or any(
                not isinstance(secrets.get(key), str) or not secrets[key]
                for key in ("access_token", "refresh_token")
            ):
                raise CredentialStorageError("Stored token pair could not be read.")
            return Credential(
                access_token=secrets["access_token"],
                refresh_token=secrets["refresh_token"],
                account=account,
                source=source,
                expires_at=cast(float, payload["expires_at"]),
                refresh_expires_at=cast(float, payload["refresh_expires_at"]),
                client_id=cast(str, payload["client_id"]),
                scope=cast(str, payload["scope"]),
                invalidated=bool(payload.get("invalidated", False)),
            )
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
        refresh_token: str | None = None,
        expires_at: float | None = None,
        refresh_expires_at: float | None = None,
        client_id: str | None = None,
        scope: str | None = None,
        invalidated: bool = False,
    ) -> None:
        if not access_token:
            raise CredentialStorageError("Refusing to persist an empty Access Token.")
        payload: dict[str, object] = {
            "version": 1,
            "storage": source.value,
            "account": {"id": account.id, "name": account.name},
        }
        keyring_value = access_token
        if refresh_token is not None:
            if (
                not refresh_token
                or not client_id
                or not scope
                or any(
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or value <= 0
                    or not math.isfinite(value)
                    for value in (expires_at, refresh_expires_at)
                )
            ):
                raise CredentialStorageError("Refusing to persist an incomplete V2 credential.")
            payload.update(
                version=2,
                client_id=client_id,
                scope=scope,
                expires_at=expires_at,
                refresh_expires_at=refresh_expires_at,
                invalidated=invalidated,
            )
            keyring_value = json.dumps(
                {"access_token": access_token, "refresh_token": refresh_token}
            )
        previous_metadata = self._path.read_bytes() if self._path.exists() else None
        replacing_keyring = (
            source is CredentialSource.FILE
            and previous_metadata is not None
            and json.loads(previous_metadata).get("storage") == CredentialSource.KEYRING.value
        )
        previous_keyring_token: str | None = None
        keyring_changed = False
        if source is CredentialSource.FILE:
            payload["access_token"] = access_token
            if refresh_token is not None:
                payload["refresh_token"] = refresh_token
        elif source is not CredentialSource.KEYRING:
            raise CredentialStorageError("Environment credentials cannot be persisted.")
        try:
            if replacing_keyring:
                previous_keyring_token = self._keyring.get_password("simklcli", "access-token")
            if source is CredentialSource.KEYRING:
                try:
                    previous_keyring_token = self._keyring.get_password("simklcli", "access-token")
                    keyring_changed = previous_keyring_token != keyring_value
                    if keyring_changed:
                        self._keyring.set_password("simklcli", "access-token", keyring_value)
                except Exception as keyring_error:
                    raise CredentialStorageError(_KEYRING_UNAVAILABLE_MESSAGE) from keyring_error
            atomic_write_json(self._path, payload)
            if replacing_keyring and previous_keyring_token is not None:
                keyring_changed = True
                self._keyring.delete_password("simklcli", "access-token")
        except BaseException:
            try:
                _restore_file(self._path, previous_metadata)
                if (source is CredentialSource.KEYRING or replacing_keyring) and keyring_changed:
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

    def save(self, credential: Credential) -> None:
        if credential.account is None:
            raise CredentialStorageError("The credential needs a validated account.")
        self.write(
            access_token=credential.access_token,
            account=credential.account,
            source=credential.source,
            refresh_token=credential.refresh_token,
            expires_at=credential.expires_at,
            refresh_expires_at=credential.refresh_expires_at,
            client_id=credential.client_id,
            scope=credential.scope,
            invalidated=credential.invalidated,
        )

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
            if (
                not isinstance(payload, dict)
                or isinstance(payload.get("version"), bool)
                or payload.get("version") not in {1, 2}
            ):
                raise ValueError
            if CredentialSource(payload["storage"]) is CredentialSource.ENVIRONMENT:
                raise ValueError
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
            if payload["version"] == 2 and (
                any(
                    not isinstance(payload.get(key), str) or not payload[key]
                    for key in ("client_id", "scope")
                )
                or any(
                    not isinstance(payload.get(key), (int, float))
                    or isinstance(payload[key], bool)
                    or payload[key] <= 0
                    or not math.isfinite(payload[key])
                    for key in ("expires_at", "refresh_expires_at")
                )
                or not isinstance(payload.get("invalidated", False), bool)
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
