from pathlib import Path

import httpx
from typer.testing import CliRunner

from simklcli.cli import create_app
from simklcli.runtime import Runtime
from simklcli.storage import Account, CredentialSource


def test_login_polls_at_server_interval_validates_account_then_persists(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json={
                    "result": "OK",
                    "device_code": "DEVICE_CODE",
                    "user_code": "ABCDE",
                    "verification_url": "https://simkl.com/pin",
                    "expires_in": 15,
                    "interval": 5,
                },
            )
        if calls == 2:
            return httpx.Response(200, json={"result": "KO", "message": "pending"})
        if calls == 3:
            return httpx.Response(200, json={"result": "OK", "access_token": "secret-token"})
        assert calls == 4
        assert request.url.path == "/users/settings"
        assert not (config_dir / "auth.json").exists()
        return httpx.Response(200, json={"user": {"name": "Javier"}, "account": {"id": 12345}})

    runtime = Runtime.for_testing(
        config_dir=config_dir,
        data_dir=tmp_path / "data",
        environ={"SIMKL_CLIENT_ID": "registered-client"},
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "login", "--no-open-browser", "--storage", "file"],
    )

    assert result.exit_code == 0, result.output
    assert "https://simkl.com/pin" in result.stdout
    assert "ABCDE" in result.stdout
    assert "Javier" in result.stdout
    assert sleeps == [5.0, 5.0]
    credential = runtime.credentials.read()
    assert credential is not None
    assert credential.account == Account(id=12345, name="Javier")
    assert credential.source is CredentialSource.FILE


def test_login_requires_explicit_file_selection_when_keyring_is_unavailable(tmp_path: Path) -> None:
    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"network request was not expected: {request.url}")

    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        transport=httpx.MockTransport(unexpected_request),
    )

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "login", "--no-open-browser"],
    )

    assert result.exit_code == 1
    assert "No usable OS keyring" in result.stderr
    assert "--storage file" in result.stderr


def test_login_refuses_to_create_credentials_shadowed_by_environment_token(tmp_path: Path) -> None:
    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        environ={"SIMKL_ACCESS_TOKEN": "environment-secret"},
    )

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "login", "--storage", "file", "--no-open-browser"],
    )

    assert result.exit_code == 1
    assert "SIMKL_ACCESS_TOKEN is active" in result.stderr
    assert "environment-secret" not in result.output
    assert runtime.credentials.read() is None


def test_login_requires_logout_before_switching_persisted_accounts(tmp_path: Path) -> None:
    runtime = Runtime.for_testing(config_dir=tmp_path / "config", data_dir=tmp_path / "data")
    runtime.credentials.write(
        access_token="existing-token",
        account=Account(id=12345, name="Javier"),
        source=CredentialSource.FILE,
    )

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "login", "--storage", "file", "--no-open-browser"],
    )

    assert result.exit_code == 1
    assert "already stored" in result.stderr
    assert "auth logout" in result.stderr


def test_browser_failure_does_not_fail_pin_authorization(tmp_path: Path) -> None:
    responses = iter(
        [
            httpx.Response(
                200,
                json={
                    "result": "OK",
                    "device_code": "DEVICE_CODE",
                    "user_code": "ABCDE",
                    "verification_uri": "https://simkl.com/pin",
                    "expires_in": 15,
                    "interval": 5,
                },
            ),
            httpx.Response(200, json={"result": "OK", "access_token": "secret-token"}),
            httpx.Response(200, json={"user": {"name": "Javier"}, "account": {"id": 12345}}),
        ]
    )

    def browser_failure(url: str) -> bool:
        assert url == "https://simkl.com/pin"
        raise OSError("no browser")

    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        transport=httpx.MockTransport(lambda request: next(responses)),
        sleep=lambda seconds: None,
        open_browser=browser_failure,
        interactive=True,
    )

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "login", "--storage", "file"],
    )

    assert result.exit_code == 0
    assert "Could not open a browser" in result.stderr
    assert runtime.credentials.read() is not None


def test_ctrl_c_cancels_without_persisting_partial_credential(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
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

    def interrupt(seconds: float) -> None:
        raise KeyboardInterrupt

    runtime = Runtime.for_testing(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        transport=httpx.MockTransport(handler),
        sleep=interrupt,
    )

    result = CliRunner().invoke(
        create_app(lambda: runtime),
        ["auth", "login", "--storage", "file", "--no-open-browser"],
    )

    assert result.exit_code == 130
    assert "cancelled; nothing was stored" in result.stderr
    assert runtime.credentials.read() is None
