from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock

from simklcli.filesystem import atomic_write_json


class CrossProcessRateGate:
    """Space Simkl requests across processes by application or account scope."""

    def __init__(
        self,
        root: Path,
        *,
        client_id: str,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._root = root
        self._client_id = client_id
        self._clock = clock
        self._sleep = sleep
        self._accounts: dict[str, int] = {}

    def bind_account(self, access_token: str, account_id: int) -> None:
        # Carry the initial token-validation cadence into the validated account gate.
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        token_key = hashlib.sha256(f"account:{access_token}".encode()).hexdigest()
        account_key = hashlib.sha256(f"account:{account_id}".encode()).hexdigest()
        path = self._root / f"{account_key}.json"
        with FileLock(self._root / f"{account_key}.lock", mode=0o600):
            state = _read_state(path)
            token_state = _read_state(self._root / f"{token_key}.json")
            for field, value in token_state.items():
                state[field] = max(value, state.get(field, value))
            atomic_write_json(path, state)
            atomic_write_json(self._root / f"{token_key}.account.json", {"account_id": account_id})
        self._accounts[access_token] = account_id

    def remembered_account_id(self, access_token: str) -> int | None:
        key = hashlib.sha256(f"account:{access_token}".encode()).hexdigest()
        try:
            payload = json.loads((self._root / f"{key}.account.json").read_text(encoding="utf-8"))
            account_id = payload["account_id"]
            if isinstance(account_id, int) and not isinstance(account_id, bool) and account_id > 0:
                return account_id
        except (OSError, KeyError, TypeError, ValueError):
            pass
        return None

    @contextmanager
    def limit(self, method: str, access_token: str | None) -> Iterator[None]:
        interval = 1.0 if method == "POST" else 0.1
        account_id = (
            None
            if access_token is None
            else (self._accounts.get(access_token) or self.remembered_account_id(access_token))
        )
        scope = (
            f"account:{account_id if account_id is not None else access_token}"
            if access_token is not None
            else f"application:{self._client_id}"
        )
        key = hashlib.sha256(scope.encode()).hexdigest()
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        state_path = self._root / f"{key}.json"
        lock = FileLock(self._root / f"{key}.lock")
        with lock:
            state = _read_state(state_path)
            field = "last_post" if method == "POST" else "last_get"
            previous = state.get(field)
            if previous is not None:
                delay = previous + interval - self._clock()
                if delay > 0:
                    self._sleep(delay)
            state[field] = self._clock()
            atomic_write_json(state_path, state)
            yield


def _read_state(path: Path) -> dict[str, float]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        key: float(value)
        for key, value in payload.items()
        if key in {"last_get", "last_post"} and isinstance(value, int | float)
    }
