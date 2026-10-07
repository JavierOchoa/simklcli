from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest

from simklcli import __version__
from simklcli.api import InvalidAccessTokenError, PinAuthorization, SimklClient, SimklResponseError
from simklcli.storage import Account


def test_pin_authorization_request_includes_required_application_identity() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "result": "OK",
                "device_code": "DEVICE_CODE",
                "user_code": "ABCDE",
                "verification_uri": "https://simkl.com/pin",
                "expires_in": 900,
                "interval": 5,
            },
        )

    client = SimklClient(
        client_id="registered-client",
        transport=httpx.MockTransport(handler),
    )

    pin = client.start_pin_authorization()

    assert pin == PinAuthorization(
        user_code="ABCDE",
        verification_url="https://simkl.com/pin",
        expires_in=900,
        interval=5,
        device_code="DEVICE_CODE",
    )
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url == httpx.URL(
        "https://api.simkl.com/oauth2/device",
        params={
            "client_id": "registered-client",
            "app-name": "simklcli",
            "app-version": __version__,
        },
    )
    assert request.headers["user-agent"] == f"simklcli/{__version__}"


def test_approved_pin_returns_access_token_without_sending_it_as_input() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oauth2/token"
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={
                "access_token": "secret-token",
                "refresh_token": "refresh-secret",
                "expires_in": 604800,
                "token_type": "Bearer",
                "scope": "media:read media:write",
            },
        )

    client = SimklClient(client_id="registered-client", transport=httpx.MockTransport(handler))

    assert (
        client.poll_pin_authorization(
            PinAuthorization("ABCDE", "https://simkl.com/pin", 900, 5, "DEVICE_CODE")
        )
        or pytest.fail("Missing token pair")
    ).access_token == "secret-token"


def test_account_validation_uses_authenticated_settings_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/users/settings"
        assert request.content == b""
        assert request.headers["authorization"] == "Bearer secret-token"
        assert request.headers["content-type"] == "application/json"
        return httpx.Response(
            200,
            json={"user": {"name": "Javier"}, "account": {"id": 12345}},
        )

    client = SimklClient(client_id="registered-client", transport=httpx.MockTransport(handler))

    assert client.get_authenticated_account("secret-token") == Account(id=12345, name="Javier")


def test_get_retries_throttling_with_retry_after_delay() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "3"},
                json={"error": "rate_limit", "code": 429},
            )
        return httpx.Response(
            200,
            json={
                "result": "OK",
                "device_code": "DEVICE_CODE",
                "user_code": "ABCDE",
                "verification_uri": "https://simkl.com/pin",
                "expires_in": 900,
                "interval": 5,
            },
        )

    client = SimklClient(
        client_id="registered-client",
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        jitter=lambda delay: delay,
    )

    client.get_json("/movies/1")

    assert attempts == 2
    assert sleeps == [3.0]


def test_malformed_pin_json_is_reported_as_a_simkl_response_error() -> None:
    client = SimklClient(
        client_id="registered-client",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not-json")),
    )

    with pytest.raises(SimklResponseError, match="malformed PIN response"):
        client.start_pin_authorization()


def test_pending_and_expired_pin_responses_are_distinct() -> None:
    responses = iter(
        [
            httpx.Response(400, json={"error": "authorization_pending"}),
            httpx.Response(
                400,
                json={"error": "expired_token"},
            ),
        ]
    )
    client = SimklClient(
        client_id="registered-client",
        transport=httpx.MockTransport(lambda request: next(responses)),
    )

    assert (
        client.poll_pin_authorization(
            PinAuthorization("ABCDE", "https://simkl.com/pin", 900, 5, "DEVICE_CODE")
        )
        is None
    )
    with pytest.raises(SimklResponseError, match="expired or is no longer valid"):
        client.poll_pin_authorization(
            PinAuthorization("ABCDE", "https://simkl.com/pin", 900, 5, "DEVICE_CODE")
        )


def test_confirmed_user_token_failure_has_a_specific_error() -> None:
    client = SimklClient(
        client_id="registered-client",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, json={"error": "user_token_failed"})
        ),
    )

    with pytest.raises(InvalidAccessTokenError, match="invalid or revoked"):
        client.get_authenticated_account("revoked-token")


def test_every_request_passes_through_the_rate_control_boundary() -> None:
    class RecordingGate:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None]] = []

        @contextmanager
        def limit(self, method: str, access_token: str | None) -> Iterator[None]:
            self.calls.append((method, access_token))
            yield

    gate = RecordingGate()
    responses = iter(
        [
            httpx.Response(
                200,
                json={
                    "result": "OK",
                    "device_code": "DEVICE_CODE",
                    "user_code": "ABCDE",
                    "verification_uri": "https://simkl.com/pin",
                    "expires_in": 900,
                    "interval": 5,
                },
            ),
            httpx.Response(200, json={"user": {"name": "Javier"}, "account": {"id": 12345}}),
        ]
    )
    client = SimklClient(
        client_id="registered-client",
        transport=httpx.MockTransport(lambda request: next(responses)),
        rate_gate=gate,
    )

    client.start_pin_authorization()
    client.get_authenticated_account("secret-token")

    assert gate.calls == [("POST", None), ("POST", "secret-token")]


@pytest.mark.parametrize(
    "payload",
    [
        {"result": "KO"},
        {
            "result": "OK",
            "user_code": "",
            "verification_uri": "https://simkl.com/pin",
            "expires_in": 900,
            "interval": 5,
        },
        {
            "result": "OK",
            "user_code": "ABCDE",
            "verification_uri": "https://simkl.com/pin",
            "expires_in": True,
            "interval": 5,
        },
    ],
)
def test_invalid_pin_shapes_fail_without_guessing(payload: object) -> None:
    client = SimklClient(
        client_id="registered-client",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )

    with pytest.raises(SimklResponseError):
        client.start_pin_authorization()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"result": "maybe"},
        {"result": "OK"},
    ],
)
def test_invalid_pin_poll_shapes_fail_without_guessing(payload: object) -> None:
    client = SimklClient(
        client_id="registered-client",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )

    with pytest.raises(SimklResponseError, match="malformed token response"):
        client.poll_pin_authorization(
            PinAuthorization("ABCDE", "https://simkl.com/pin", 900, 5, "DEVICE_CODE")
        )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"user": "Javier", "account": {"id": 12345}},
        {"user": {"name": ""}, "account": {"id": 12345}},
        {"user": {"name": "Javier"}, "account": {"id": 0}},
    ],
)
def test_invalid_account_shapes_are_never_persistable(payload: object) -> None:
    client = SimklClient(
        client_id="registered-client",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )

    with pytest.raises(SimklResponseError, match="stable account ID and display name"):
        client.get_authenticated_account("secret-token")


def test_get_exhausts_documented_retry_schedule_then_fails() -> None:
    sleeps: list[float] = []
    client = SimklClient(
        client_id="registered-client",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, headers={"Retry-After": "not-a-number"})
        ),
        sleep=sleeps.append,
        jitter=lambda delay: delay,
    )

    with pytest.raises(httpx.HTTPStatusError):
        client.get_json("/movies/1")

    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]
