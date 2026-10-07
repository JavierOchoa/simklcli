from __future__ import annotations

import os
import time
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import keyring
from platformdirs import user_config_path, user_data_path

from simklcli.rate_control import CrossProcessRateGate
from simklcli.snapshots import LibrarySnapshotStore
from simklcli.storage import (
    Credential,
    CredentialRepository,
    CredentialStorageError,
    EmptyKeyring,
    Keyring,
)

if TYPE_CHECKING:
    from simklcli.api import SimklClient


DEFAULT_CLIENT_ID = ""


@dataclass
class Runtime:
    credentials: CredentialRepository
    snapshots: LibrarySnapshotStore
    environ: Mapping[str, str]
    data_dir: Path
    transport: httpx.BaseTransport | None
    rate_gate: CrossProcessRateGate | None
    sleep: Callable[[float], None]
    monotonic: Callable[[], float]
    open_browser: Callable[[str], bool]
    interactive: bool
    clock: Callable[[], float] = time.time

    def client_id(self) -> str:
        configured = self.environ.get("SIMKL_CLIENT_ID", "").strip()
        if configured:
            return configured
        if not self.environ.get("SIMKL_ACCESS_TOKEN", "").strip():
            try:
                stored = self.credentials.read()
            except CredentialStorageError:
                stored = None
            if stored is not None and stored.client_id:
                return stored.client_id
        return DEFAULT_CLIENT_ID

    def identity_gate(self) -> CrossProcessRateGate:
        return self.rate_gate or CrossProcessRateGate(
            self.data_dir / "rate-gates",
            client_id=self.client_id(),
        )

    def api_client(self) -> SimklClient:
        from simklcli.api import SimklClient

        if self.rate_gate is not None and not self.environ.get("SIMKL_ACCESS_TOKEN", "").strip():
            try:
                stored = self.credentials.read()
            except CredentialStorageError:
                stored = None
            if stored is not None and stored.account is not None:
                self.rate_gate.bind_account(stored.access_token, stored.account.id)
        return SimklClient(
            client_id=self.client_id(),
            transport=self.transport,
            sleep=self.sleep,
            rate_gate=self.rate_gate,
            clock=self.clock,
        )

    def authenticated_client(self) -> tuple[Credential, SimklClient]:
        from simklcli.api import SimklResponseError
        from simklcli.storage import CredentialSource

        credential = self.active_credential()
        if credential is None:
            raise SimklResponseError(
                "No Access Token is available. Run simkl auth login explicitly."
            )
        if credential.source is not CredentialSource.ENVIRONMENT and credential.account is None:
            raise SimklResponseError(
                "The active credential has no validated Authenticated Account."
            )
        if credential.legacy or credential.invalidated:
            raise SimklResponseError(
                "This account requires AUTH V2 authorization. Set SIMKL_CLIENT_ID to your "
                "V2 app and run simkl auth login; the Library Snapshot will be retained."
            )
        if credential.source is CredentialSource.ENVIRONMENT:
            # Recognize genuine V1 credentials without accepting them on a V2 registration.
            token = credential.access_token
            if len(token) == 64 and all(c in "0123456789abcdef" for c in token):
                raise SimklResponseError("SIMKL_ACCESS_TOKEN is a V1 token; authorize your V2 app.")
        elif credential.client_id != self.client_id():
            raise SimklResponseError(
                "SIMKL_CLIENT_ID differs from the stored token's app; restore it or log out "
                "before authorizing a different app."
            )
        api = self.api_client()
        refresh = None
        if credential.source is not CredentialSource.ENVIRONMENT:
            refresh = self._refresh_credential
            if credential.expires_at is not None and credential.expires_at <= self.clock() + 60:
                self._refresh_credential(credential.access_token)
                credential = self.credentials.read() or credential
        api.configure_auth(credential.access_token, refresh)
        if self.rate_gate is not None and credential.account is not None:
            self.rate_gate.bind_account(credential.access_token, credential.account.id)
        return credential, api

    def _refresh_credential(self, failed_token: str) -> str:
        from simklcli.api import InvalidAccessTokenError, OAuthGrantError, SimklResponseError

        with self.credentials.account_transaction():
            credential = self.credentials.read()
            if credential is None or credential.invalidated or credential.refresh_token is None:
                raise InvalidAccessTokenError(
                    "Refresh credentials are unavailable; authorize again."
                )
            if credential.client_id != self.client_id():
                raise SimklResponseError(
                    "The stored token belongs to a different client registration."
                )
            # Another invocation may already have refreshed while this one waited for the lock.
            if credential.access_token != failed_token:
                return credential.access_token
            if (
                credential.refresh_expires_at is None
                or credential.refresh_expires_at <= self.clock()
            ):
                raise InvalidAccessTokenError("The Refresh Token expired; authorize again.")
            api = self.api_client()
            try:
                pair = api.refresh_tokens(credential.refresh_token)
            except OAuthGrantError as exc:
                if exc.error == "invalid_grant":
                    raise InvalidAccessTokenError(
                        "The Refresh Token is invalid or revoked."
                    ) from exc
                raise
            account = api.get_authenticated_account(pair.access_token)
            if credential.account is None or account.id != credential.account.id:
                raise SimklResponseError(
                    "Refreshed credentials did not match the Authenticated Account."
                )
            updated = replace(
                credential,
                access_token=pair.access_token,
                refresh_token=pair.refresh_token,
                expires_at=pair.expires_at,
                refresh_expires_at=pair.refresh_expires_at,
                scope=pair.scope,
                account=account,
            )
            self.credentials.save(updated)
            if self.rate_gate is not None:
                self.rate_gate.bind_account(pair.access_token, account.id)
            return pair.access_token

    def active_credential(self) -> Credential | None:
        token = self.environ.get("SIMKL_ACCESS_TOKEN", "").strip()
        if token:
            from simklcli.storage import CredentialSource

            return Credential(
                access_token=token,
                source=CredentialSource.ENVIRONMENT,
                account=None,
            )
        return self.credentials.read()

    @classmethod
    def for_testing(
        cls,
        *,
        config_dir: Path,
        data_dir: Path,
        environ: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        rate_gate: CrossProcessRateGate | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        open_browser: Callable[[str], bool] = webbrowser.open,
        interactive: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> Runtime:
        return cls(
            credentials=CredentialRepository(config_dir, EmptyKeyring()),
            snapshots=LibrarySnapshotStore(data_dir),
            environ={"SIMKL_CLIENT_ID": "test-v2-client", **(environ or {})},
            data_dir=data_dir,
            transport=transport or httpx.MockTransport(_unexpected_test_request),
            rate_gate=rate_gate,
            sleep=sleep,
            monotonic=monotonic,
            open_browser=open_browser,
            interactive=interactive,
            clock=clock,
        )


def default_runtime() -> Runtime:
    config_dir = Path(user_config_path("simklcli", appauthor=False))
    data_dir = Path(user_data_path("simklcli", appauthor=False))
    backend: Keyring = keyring.get_keyring()
    client_id = os.environ.get("SIMKL_CLIENT_ID", DEFAULT_CLIENT_ID)
    return Runtime(
        credentials=CredentialRepository(config_dir, backend),
        snapshots=LibrarySnapshotStore(data_dir),
        environ=os.environ,
        data_dir=data_dir,
        transport=None,
        rate_gate=CrossProcessRateGate(data_dir / "rate-gates", client_id=client_id),
        sleep=time.sleep,
        monotonic=time.monotonic,
        open_browser=webbrowser.open,
        interactive=_is_interactive(),
    )


def _unexpected_test_request(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"No authored response exists for {request.method} {request.url.path}.")


def _is_interactive() -> bool:
    import sys

    return sys.stdin.isatty() and sys.stdout.isatty()
