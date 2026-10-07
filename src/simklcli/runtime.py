from __future__ import annotations

import os
import time
import webbrowser
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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


DEFAULT_CLIENT_ID = "a1b4dc7914dbbfabdef8d42e4312d8c89726dc0ce3e7e89e3d83c0ba89fb73ac"


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

    def identity_gate(self) -> CrossProcessRateGate:
        return self.rate_gate or CrossProcessRateGate(
            self.data_dir / "rate-gates",
            client_id=self.environ.get("SIMKL_CLIENT_ID", DEFAULT_CLIENT_ID),
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
            client_id=self.environ.get("SIMKL_CLIENT_ID", DEFAULT_CLIENT_ID),
            transport=self.transport,
            sleep=self.sleep,
            rate_gate=self.rate_gate,
        )

    def active_credential(self) -> Credential | None:
        token = self.environ.get("SIMKL_ACCESS_TOKEN", "").strip()
        if token:
            from simklcli.storage import CredentialSource

            try:
                account = self.credentials.read_account()
            except CredentialStorageError:
                account = None
            return Credential(
                access_token=token,
                source=CredentialSource.ENVIRONMENT,
                account=account,
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
    ) -> Runtime:
        return cls(
            credentials=CredentialRepository(config_dir, EmptyKeyring()),
            snapshots=LibrarySnapshotStore(data_dir),
            environ={} if environ is None else environ,
            data_dir=data_dir,
            transport=transport,
            rate_gate=rate_gate,
            sleep=sleep,
            monotonic=monotonic,
            open_browser=open_browser,
            interactive=interactive,
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


def _is_interactive() -> bool:
    import sys

    return sys.stdin.isatty() and sys.stdout.isatty()
