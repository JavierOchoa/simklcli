from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from simklcli.api import InvalidAccessTokenError, SimklClient, SimklResponseError
from simklcli.catalog import Catalog
from simklcli.cli import create_app
from simklcli.media import MediaKind
from simklcli.runtime import Runtime
from simklcli.storage import Account, CredentialRepository, CredentialSource, CredentialStorageError
from tests.test_storage import MemoryKeyring


def tokens(access: str = "access-new") -> dict[str, Any]:
    return {
        "access_token": access,
        "refresh_token": "refresh-secret",
        "expires_in": 604800,
        "token_type": "Bearer",
        "scope": "media:read media:write",
    }


def pin() -> dict[str, Any]:
    return {
        "device_code": "private-device-code",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://simkl.com/pin",
        "verification_uri_complete": "https://simkl.com/pin?user_code=ABCD-EFGH",
        "expires_in": 900,
        "interval": 5,
    }


def settings(account_id: int = 123) -> httpx.Response:
    return httpx.Response(200, json={"account": {"id": account_id}, "user": {"name": "Test"}})


def runtime_at(root: Path, handler: Callable[[httpx.Request], httpx.Response]) -> Runtime:
    return Runtime.for_testing(
        config_dir=root / "config",
        data_dir=root / "data",
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
        clock=lambda: 1000.0,
    )


def store(runtime: Runtime, *, expires_at: float = 2000.0, invalidated: bool = False) -> None:
    runtime.credentials.write(
        access_token="access-old",
        refresh_token="refresh-secret",
        expires_at=expires_at,
        refresh_expires_at=10000000.0,
        client_id=runtime.client_id(),
        scope="media:read media:write",
        account=Account(123, "Test"),
        source=CredentialSource.FILE,
        invalidated=invalidated,
    )


def test_device_wire_uses_private_code_in_body_and_complete_link(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        assert request.method == "POST"
        assert body["client_id"] == "test-v2-client"
        assert "authorization" not in request.headers
        assert "private-device-code" not in str(request.url)
        if request.url.path == "/oauth2/device":
            assert body["scope"] == "media:read media:write"
            assert "code_challenge" not in body
            return httpx.Response(200, json=pin())
        if request.url.path == "/oauth2/token":
            assert body["device_code"] == "private-device-code"
            assert body["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"
            return httpx.Response(200, json=tokens())
        assert request.url.path == "/users/settings"
        return settings()

    # Settings is authenticated; handle it before the OAuth-only assertions.
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/settings":
            seen.append(request)
            assert request.headers["authorization"] == "Bearer access-new"
            return settings()
        return handler(request)

    runtime = runtime_at(tmp_path, transport)
    opened: list[str] = []
    runtime.interactive = True

    def open_browser(url: str) -> bool:
        opened.append(url)
        return True

    runtime.open_browser = open_browser
    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "login", "--storage", "file"])
    assert result.exit_code == 0, result.output
    assert opened == [pin()["verification_uri_complete"]]
    for secret in ["private-device-code", "access-new", "refresh-secret"]:
        assert secret not in result.output
    credential = runtime.credentials.read()
    assert credential is not None and credential.expires_at == 605800.0
    assert credential.refresh_expires_at == 1000.0 + 180 * 86400
    assert len(seen) == 3


def test_slow_down_accumulates_and_timeout_does_not_poll_after_expiry(tmp_path: Path) -> None:
    elapsed = 0.0
    waits: list[float] = []
    polls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.url.path == "/oauth2/device":
            return httpx.Response(200, json={**pin(), "expires_in": 20})
        polls += 1
        return httpx.Response(400, json={"error": "slow_down"})

    def sleep(seconds: float) -> None:
        nonlocal elapsed
        waits.append(seconds)
        elapsed += seconds

    runtime = runtime_at(tmp_path, handler)
    runtime.monotonic = lambda: elapsed
    runtime.sleep = sleep
    result = CliRunner().invoke(
        create_app(lambda: runtime), ["auth", "login", "--storage", "file", "--json"]
    )
    assert result.exit_code == 1
    assert waits == [5.0, 10.0, 5.0] and polls == 2
    assert "expired" in result.stderr
    assert runtime.credentials.read() is None


@pytest.mark.parametrize(
    "error,status",
    [
        ("invalid_client", 401),
        ("unauthorized_client", 400),
        ("invalid_request", 400),
        ("access_denied", 400),
        ("unknown-secret", 500),
    ],
)
def test_oauth_failure_does_not_expose_response_description_or_retry(
    tmp_path: Path,
    error: str,
    status: int,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"error": error, "error_description": "refresh-secret"})

    runtime = runtime_at(tmp_path, handler)
    result = CliRunner().invoke(
        create_app(lambda: runtime), ["auth", "login", "--storage", "file", "--json"]
    )
    assert result.exit_code == 1 and calls == 1
    assert "refresh-secret" not in result.output and "unknown-secret" not in result.output
    assert runtime.credentials.read() is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("access_token", ""),
        ("refresh_token", None),
        ("expires_in", True),
        ("token_type", "other"),
        ("token_type", []),
        ("scope", "media:read"),
    ],
)
def test_incomplete_or_read_only_tokens_are_rejected(field: str, value: object) -> None:
    client = SimklClient(
        client_id="app",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={**tokens(), field: value})
        ),
    )
    with pytest.raises(SimklResponseError):
        client.refresh_tokens("refresh-secret")


def test_proactive_refresh_saves_pair_before_tracking_and_offline_never_refreshes(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/oauth2/token":
            assert json.loads(request.content) == {
                "client_id": "test-v2-client",
                "grant_type": "refresh_token",
                "refresh_token": "refresh-secret",
            }
            return httpx.Response(200, json=tokens())
        if request.url.path == "/users/settings":
            assert request.headers["authorization"] == "Bearer access-new"
            return settings()
        assert runtime.credentials.read().access_token == "access-new"  # type: ignore[union-attr]
        assert request.headers["authorization"] == "Bearer access-new"
        return httpx.Response(
            200,
            json={"all": "2026-10-07T00:00:00Z"} if request.url.path == "/sync/activities" else {},
        )

    runtime = runtime_at(tmp_path, handler)
    store(runtime, expires_at=1001)
    result = CliRunner().invoke(create_app(lambda: runtime), ["library", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert calls[:2] == ["/oauth2/token", "/users/settings"]
    credential = runtime.credentials.read()
    assert credential is not None and credential.refresh_token == "refresh-secret"
    before = len(calls)
    runtime.credentials.save(replace(credential, expires_at=1, invalidated=True))
    result = CliRunner().invoke(
        create_app(lambda: runtime), ["library", "list", "--offline", "--json"]
    )
    assert result.exit_code == 0 and len(calls) == before


def test_definitive_401_allows_one_refresh_and_one_post_retry(tmp_path: Path) -> None:
    posted: list[str] = []
    refreshes = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refreshes
        if request.url.path == "/oauth2/token":
            refreshes += 1
            return httpx.Response(200, json=tokens())
        if request.url.path == "/users/settings":
            return settings()
        posted.append(request.headers["authorization"])
        if len(posted) == 1:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(201, json={"added": {"movies": 1}})

    runtime = runtime_at(tmp_path, handler)
    store(runtime)
    credential, api = runtime.authenticated_client()
    assert api.write_state("/sync/ratings", {}, access_token=credential.access_token)["added"]
    assert posted == ["Bearer access-old", "Bearer access-new"] and refreshes == 1


@pytest.mark.parametrize(
    "status,error",
    [
        (401, "user_token_required"),
        (403, "insufficient_scope"),
        (429, "rate_limit"),
        (503, "internal"),
    ],
)
def test_other_rejections_never_refresh_or_replay_post(
    tmp_path: Path, status: int, error: str
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(status, json={"error": error})

    runtime = runtime_at(tmp_path, handler)
    store(runtime)
    credential, api = runtime.authenticated_client()
    with pytest.raises((httpx.HTTPError, SimklResponseError)):
        api.write_state("/sync/ratings", {}, access_token=credential.access_token)
    assert seen == ["/sync/ratings"]
    assert not runtime.credentials.read().invalidated  # type: ignore[union-attr]


def test_second_401_stops_without_refresh_loop(tmp_path: Path) -> None:
    counts: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        counts[path] = counts.get(path, 0) + 1
        if path == "/oauth2/token":
            return httpx.Response(200, json=tokens())
        if path == "/users/settings":
            return settings()
        return httpx.Response(401, json={"error": "user_token_failed"})

    runtime = runtime_at(tmp_path, handler)
    store(runtime)
    credential, api = runtime.authenticated_client()
    with pytest.raises(InvalidAccessTokenError):
        api.get_json("/sync/activities", access_token=credential.access_token)
    assert counts == {"/sync/activities": 2, "/oauth2/token": 1, "/users/settings": 1}


def test_concurrent_commands_refresh_shared_grant_only_once(tmp_path: Path) -> None:
    started, release = Event(), Event()
    refreshes = 0
    errors: list[BaseException] = []
    results: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal refreshes
        if request.url.path == "/oauth2/token":
            refreshes += 1
            started.set()
            assert release.wait(timeout=3)
            return httpx.Response(200, json=tokens())
        return settings()

    first, second = runtime_at(tmp_path, handler), runtime_at(tmp_path, handler)
    store(first, expires_at=1001)

    def refresh(runtime: Runtime) -> None:
        try:
            credential, _ = runtime.authenticated_client()
            results.append(credential.access_token)
        except BaseException as exc:
            errors.append(exc)

    threads = [Thread(target=refresh, args=(runtime,)) for runtime in [first, second]]
    threads[0].start()
    assert started.wait(timeout=2)
    threads[1].start()
    release.set()
    for thread in threads:
        thread.join(timeout=4)
        assert not thread.is_alive()
    assert errors == [] and refreshes == 1 and results == ["access-new", "access-new"]


def test_pair_is_atomic_in_keyring_and_metadata_has_no_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyring = MemoryKeyring()
    repository = CredentialRepository(tmp_path, keyring)
    args: dict[str, Any] = {
        "access_token": "access-old",
        "refresh_token": "refresh-secret",
        "expires_at": 2000,
        "refresh_expires_at": 3000,
        "client_id": "app",
        "scope": "media:read media:write",
        "account": Account(123, "Test"),
        "source": CredentialSource.KEYRING,
    }
    repository.write(**args)
    metadata = (tmp_path / "auth.json").read_bytes()
    for secret in [b"access-old", b"refresh-secret"]:
        assert secret not in metadata
    assert json.loads(keyring.password or "{}")["refresh_token"] == "refresh-secret"
    original_secret = keyring.password

    def fail(path: Path, payload: dict[str, object]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("simklcli.storage.atomic_write_json", fail)
    with pytest.raises(OSError):
        repository.write(**{**args, "access_token": "access-new"})
    assert keyring.password == original_secret and (tmp_path / "auth.json").read_bytes() == metadata
    assert repository.read().refresh_token == "refresh-secret"  # type: ignore[union-attr]


@pytest.mark.parametrize("account_id", [123, 999])
def test_v1_reauthorization_preserves_snapshot_and_requires_same_account(
    tmp_path: Path,
    account_id: int,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert not request.url.path.startswith("/oauth/")
        if request.url.path == "/oauth2/device":
            return httpx.Response(200, json=pin())
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=tokens())
        return settings(account_id)

    runtime = runtime_at(tmp_path, handler)
    runtime.credentials.write(
        access_token="legacy-token", account=Account(123, "Old name"), source=CredentialSource.FILE
    )
    old = (tmp_path / "config" / "auth.json").read_bytes()
    snapshot = runtime.snapshots.path(123)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("snapshot evidence")
    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "login", "--storage", "file"])
    assert result.exit_code == (0 if account_id == 123 else 1), result.output
    assert snapshot.read_text() == "snapshot evidence"
    if account_id == 123:
        assert not runtime.credentials.read().legacy  # type: ignore[union-attr]
    else:
        assert (tmp_path / "config" / "auth.json").read_bytes() == old
        assert "different account" in result.stderr


@pytest.mark.parametrize("local_only,failure", [(False, False), (True, False), (False, True)])
def test_logout_revocation_is_best_effort_and_never_claims_confirmation(
    tmp_path: Path,
    local_only: bool,
    failure: bool,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/oauth2/revoke"
        assert json.loads(request.content)["token"] == "refresh-secret"
        if failure:
            raise httpx.ReadTimeout("lost revocation response")
        return httpx.Response(200, json={})

    runtime = runtime_at(tmp_path, handler)
    store(runtime)
    args = ["auth", "logout", "--yes", "--json"] + (["--local-only"] if local_only else [])
    result = CliRunner().invoke(create_app(lambda: runtime), args)
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["remote_token_revoked"] is False
    assert payload["remote_revocation_acknowledged"] is (not local_only and not failure)
    assert calls == (0 if local_only else 1)
    assert runtime.credentials.read() is None
    assert "refresh-secret" not in result.output


def test_missing_client_id_and_legacy_environment_fail_before_network(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path, lambda request: pytest.fail("Unexpected network call"))
    runtime.environ = {}
    result = CliRunner().invoke(create_app(lambda: runtime), ["auth", "login", "--storage", "file"])
    assert result.exit_code == 1 and "SIMKL_CLIENT_ID" in result.stderr
    runtime.environ = {"SIMKL_CLIENT_ID": "v2", "SIMKL_ACCESS_TOKEN": "a" * 64}
    result = CliRunner().invoke(create_app(lambda: runtime), ["library", "list", "--json"])
    assert result.exit_code == 1 and "V1 token" in result.stderr


def test_environment_401_never_uses_shadowed_refresh_token(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.headers["authorization"] == "Bearer environment-access"
        return httpx.Response(401, json={"error": "user_token_failed"})

    runtime = runtime_at(tmp_path, handler)
    store(runtime)
    before = runtime.credentials.read()
    runtime.environ = {
        "SIMKL_CLIENT_ID": "test-v2-client",
        "SIMKL_ACCESS_TOKEN": "environment-access",
    }
    result = CliRunner().invoke(
        create_app(lambda: runtime), ["auth", "status", "--check", "--json"]
    )
    assert result.exit_code == 1 and seen == ["/users/settings"]
    assert runtime.credentials.read() == before


@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf")])
def test_bad_expiry_cannot_be_persisted(tmp_path: Path, value: float | None) -> None:
    runtime = runtime_at(tmp_path, lambda request: settings())
    with pytest.raises(CredentialStorageError):
        runtime.credentials.write(
            access_token="access",
            refresh_token="refresh",
            expires_at=value,
            refresh_expires_at=3000,
            client_id="app",
            scope="media:read media:write",
            account=Account(123, "Test"),
            source=CredentialSource.FILE,
        )


def test_public_known_id_lookup_and_authenticated_search_have_explicit_boundaries() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"title": "Movie", "ids": {"simkl": 1}})

    catalog = Catalog(SimklClient(client_id="app", transport=httpx.MockTransport(handler)))
    assert catalog.resolve("simkl:1", kind=MediaKind.MOVIE).simkl_id == 1
    with pytest.raises(SimklResponseError, match="sign-in"):
        catalog.search("Movie")
    with pytest.raises(SimklResponseError, match="sign-in"):
        catalog.resolve("imdb:tt1")
    assert len(seen) == 1


def test_stored_client_id_supports_later_invocations_and_mismatched_override_is_rejected(
    tmp_path: Path,
) -> None:
    runtime = runtime_at(tmp_path, lambda request: pytest.fail("Unexpected network call"))
    store(runtime)
    runtime.environ = {}
    credential, api = runtime.authenticated_client()
    assert api.access_token == credential.access_token and runtime.client_id() == "test-v2-client"
    runtime.environ = {"SIMKL_CLIENT_ID": "different-app"}
    with pytest.raises(SimklResponseError, match="differs"):
        runtime.authenticated_client()


def test_expired_refresh_requires_login_and_preserves_snapshot(tmp_path: Path) -> None:
    runtime = runtime_at(tmp_path, lambda request: pytest.fail("Unexpected network call"))
    store(runtime, expires_at=1)
    credential = runtime.credentials.read()
    assert credential is not None
    runtime.credentials.save(replace(credential, refresh_expires_at=1))
    snapshot = runtime.snapshots.path(123)
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text("snapshot evidence")
    result = CliRunner().invoke(create_app(lambda: runtime), ["library", "list", "--json"])
    assert result.exit_code == 1 and json.loads(result.stdout)["error"] == "invalid_or_revoked"
    assert runtime.credentials.read().invalidated  # type: ignore[union-attr]
    assert snapshot.read_text() == "snapshot evidence"


def test_refresh_account_mismatch_stops_before_tracking_write(tmp_path: Path) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json=tokens())
        assert request.url.path == "/users/settings"
        return settings(999)

    runtime = runtime_at(tmp_path, handler)
    store(runtime, expires_at=1)
    original = runtime.credentials.read()
    result = CliRunner().invoke(
        create_app(lambda: runtime), ["rating", "set", "simkl:1", "8", "--json"]
    )
    assert result.exit_code == 1 and "did not match" in result.stderr
    assert paths == ["/oauth2/token", "/users/settings"]
    assert runtime.credentials.read() == original


def test_lost_refresh_response_preserves_local_pair_without_sending_tracking_write(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/oauth2/token"
        raise httpx.ReadTimeout("refresh-secret")

    runtime = runtime_at(tmp_path, handler)
    store(runtime, expires_at=1)
    original = runtime.credentials.read()
    result = CliRunner().invoke(
        create_app(lambda: runtime), ["rating", "set", "simkl:1", "8", "--json"]
    )
    assert result.exit_code == 1 and calls == 1
    assert runtime.credentials.read() == original and "refresh-secret" not in result.output


def test_explicit_storage_change_removes_old_keyring_secret_and_rolls_back_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    keyring = MemoryKeyring()
    repository = CredentialRepository(tmp_path, keyring)
    repository.write(
        access_token="legacy-secret", account=Account(123, "Test"), source=CredentialSource.KEYRING
    )
    old_metadata = (tmp_path / "auth.json").read_bytes()
    original_delete = keyring.delete_password

    def failed_delete(service: str, username: str) -> None:
        original_delete(service, username)
        raise OSError("keyring deletion failed")

    args: dict[str, Any] = {
        "access_token": "access-new",
        "refresh_token": "refresh-secret",
        "expires_at": 2000,
        "refresh_expires_at": 3000,
        "client_id": "app",
        "scope": "media:read media:write",
        "account": Account(123, "Test"),
        "source": CredentialSource.FILE,
    }
    monkeypatch.setattr(keyring, "delete_password", failed_delete)
    with pytest.raises(OSError):
        repository.write(**args)
    assert keyring.password == "legacy-secret"
    assert (tmp_path / "auth.json").read_bytes() == old_metadata
    monkeypatch.setattr(keyring, "delete_password", original_delete)
    repository.write(**args)
    assert keyring.password is None
    stored = repository.read()
    assert stored is not None and stored.refresh_token == "refresh-secret"
