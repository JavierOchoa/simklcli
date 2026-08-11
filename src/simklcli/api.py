from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from simklcli import __version__
from simklcli.storage import Account

API_BASE_URL = "https://api.simkl.com"
APP_NAME = "simklcli"


class SimklResponseError(RuntimeError):
    """Simkl returned a response that cannot fulfill the requested operation."""


class InvalidAccessTokenError(SimklResponseError):
    """Simkl confirmed that an Access Token is invalid or revoked."""


class RateGate(Protocol):
    def limit(
        self,
        method: str,
        access_token: str | None,
    ) -> AbstractContextManager[None]: ...


class NoWaitRateGate:
    @contextmanager
    def limit(self, method: str, access_token: str | None) -> Iterator[None]:
        del method, access_token
        yield


@dataclass(frozen=True)
class PinAuthorization:
    user_code: str
    verification_url: str
    expires_in: int
    interval: int


class SimklClient:
    def __init__(
        self,
        *,
        client_id: str,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float], float] | None = None,
        rate_gate: RateGate | None = None,
    ) -> None:
        self._params = {
            "client_id": client_id,
            "app-name": APP_NAME,
            "app-version": __version__,
        }
        self._client = httpx.Client(
            base_url=API_BASE_URL,
            headers={"User-Agent": f"{APP_NAME}/{__version__}"},
            transport=transport,
        )
        self._sleep = sleep
        self._jitter = jitter or (lambda delay: random.uniform(delay * 0.8, delay * 1.2))
        self._rate_gate = rate_gate or NoWaitRateGate()

    def start_pin_authorization(self) -> PinAuthorization:
        response = self._get("/oauth/pin")
        response.raise_for_status()
        payload = _json_payload(response, "Simkl returned a malformed PIN response.")
        if not isinstance(payload, dict) or payload.get("result") != "OK":
            raise SimklResponseError("Simkl did not issue a PIN Authorization code.")
        try:
            return PinAuthorization(
                user_code=_nonempty_string(payload["user_code"]),
                verification_url=_nonempty_string(
                    payload.get("verification_url") or payload["verification_uri"]
                ),
                expires_in=_positive_int(payload["expires_in"]),
                interval=_positive_int(payload["interval"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SimklResponseError("Simkl returned a malformed PIN response.") from exc

    def poll_pin_authorization(self, user_code: str) -> str | None:
        response = self._get(f"/oauth/pin/{user_code}")
        response.raise_for_status()
        payload = _json_payload(response, "Simkl returned a malformed PIN poll response.")
        if not isinstance(payload, dict):
            raise SimklResponseError("Simkl returned a malformed PIN poll response.")
        if "device_code" in payload:
            raise SimklResponseError("The PIN Authorization code expired or is no longer valid.")
        if payload.get("result") == "KO":
            return None
        if payload.get("result") != "OK":
            raise SimklResponseError("Simkl returned a malformed PIN poll response.")
        try:
            return _nonempty_string(payload["access_token"])
        except (KeyError, ValueError) as exc:
            raise SimklResponseError("Simkl returned a malformed PIN poll response.") from exc

    def get_authenticated_account(self, access_token: str) -> Account:
        response = self._request("POST", "/users/settings", access_token=access_token)
        if response.status_code == 401 and _error_code(response) == "user_token_failed":
            raise InvalidAccessTokenError("The Access Token is invalid or revoked.")
        response.raise_for_status()
        payload = _json_payload(
            response,
            "Simkl returned account settings without a stable account ID and display name.",
        )
        try:
            if not isinstance(payload, dict):
                raise TypeError
            account_payload = payload["account"]
            user_payload = payload["user"]
            if not isinstance(account_payload, dict) or not isinstance(user_payload, dict):
                raise TypeError
            account_id = _positive_int(account_payload["id"])
            name = _nonempty_string(user_payload["name"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SimklResponseError(
                "Simkl returned account settings without a stable account ID and display name."
            ) from exc
        return Account(id=account_id, name=name)

    def _get(self, path: str) -> httpx.Response:
        retry_delays = (1.0, 2.0, 4.0, 8.0, 16.0)
        for retry_delay in retry_delays:
            response = self._request("GET", path)
            if response.status_code not in {429, 500, 502, 503}:
                return response
            delay = self._jitter(retry_delay)
            retry_after = _retry_after_seconds(response)
            if retry_after is not None:
                delay = max(delay, retry_after)
            self._sleep(delay)
        return self._request("GET", path)

    def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"
        if method == "POST":
            headers["Content-Type"] = "application/json"
        with self._rate_gate.limit(method, access_token):
            return self._client.request(method, path, params=self._params, headers=headers)


def _nonempty_string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected a nonempty string")
    return value


def _positive_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("expected a positive integer")
    return value


def _error_code(response: httpx.Response) -> str | None:
    try:
        payload: Any = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    return error if isinstance(error, str) else None


def _json_payload(response: httpx.Response, message: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise SimklResponseError(message) from exc


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        return None
    return max(0.0, delay)
