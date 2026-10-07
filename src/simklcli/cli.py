import json
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from enum import StrEnum
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.panel import Panel

from simklcli.api import (
    InvalidAccessTokenError,
    PollSlowDown,
    SimklClient,
    SimklResponseError,
    TokenPair,
)
from simklcli.commands import register_commands
from simklcli.output import JsonGroup
from simklcli.runtime import Runtime, default_runtime
from simklcli.storage import Account, CredentialSource, CredentialStorageError


class CredentialStorage(StrEnum):
    KEYRING = "keyring"
    FILE = "file"


class AuthenticationStateError(RuntimeError):
    """The requested authentication transition is not currently allowed."""


def _complete_pin_authorization(
    runtime: Runtime,
    credential_source: CredentialSource,
    *,
    no_open_browser: bool,
    json_output: bool = False,
) -> Account:
    previous = runtime.credentials.read()
    previous_account = runtime.credentials.read_account()
    if (
        previous is not None
        and not previous.legacy
        and not previous.invalidated
        and (previous.refresh_expires_at is None or previous.refresh_expires_at > runtime.clock())
    ):
        raise AuthenticationStateError(
            "An Authenticated Account is already stored. "
            "Run simkl auth logout before switching accounts."
        )
    runtime.credentials.ensure_available(credential_source)
    api = runtime.api_client()
    pin = api.start_pin_authorization()
    Console(stderr=json_output).print(
        Panel(
            f"Open {pin.verification_url_complete or pin.verification_url}\n"
            f"Or open {pin.verification_url} and enter "
            f"[bold cyan]{pin.user_code}[/bold cyan]\n"
            f"Expires in {pin.expires_in} seconds · poll every {pin.interval} seconds",
            title="PIN Authorization",
        )
    )
    if not no_open_browser and runtime.interactive and not json_output:
        try:
            runtime.open_browser(pin.verification_url_complete or pin.verification_url)
        except Exception:
            Console(stderr=True).print(
                "[yellow]Could not open a browser; continue with the URL above.[/yellow]"
            )
    deadline = runtime.monotonic() + pin.expires_in
    pair: TokenPair | None = None
    interval = pin.interval
    while pair is None:
        remaining = deadline - runtime.monotonic()
        if remaining <= 0:
            raise SimklResponseError("PIN Authorization expired before approval.")
        runtime.sleep(min(float(interval), remaining))
        if runtime.monotonic() >= deadline:
            raise SimklResponseError("PIN Authorization expired before approval.")
        try:
            pair = api.poll_pin_authorization(pin)
        except PollSlowDown:
            interval += 5
        if pair is None:
            remaining_seconds = max(0, int(deadline - runtime.monotonic()))
            Console(stderr=json_output).print(
                f"Waiting for approval… {remaining_seconds}s remaining"
            )
    account = api.get_authenticated_account(pair.access_token)
    if previous_account is not None and account.id != previous_account.id:
        raise AuthenticationStateError(
            "Authorization belongs to a different account. Existing credentials and Library "
            "Snapshot were retained; log out explicitly before switching accounts."
        )
    runtime.credentials.write(
        access_token=pair.access_token,
        account=account,
        source=credential_source,
        refresh_token=pair.refresh_token,
        expires_at=pair.expires_at,
        refresh_expires_at=pair.refresh_expires_at,
        client_id=runtime.client_id(),
        scope=pair.scope,
    )
    return account


def create_app(runtime_factory: Callable[[], Runtime] = default_runtime) -> typer.Typer:
    app = typer.Typer(
        cls=JsonGroup,
        name="simkl",
        help="Track movies, shows, and anime on Simkl.",
        no_args_is_help=True,
        pretty_exceptions_enable=False,
    )
    auth_app = typer.Typer(
        help="Authorize this CLI for one Simkl account.",
        no_args_is_help=True,
    )
    app.add_typer(auth_app, name="auth")

    @auth_app.command("login")
    def auth_login(
        no_open_browser: Annotated[
            bool,
            typer.Option(
                "--no-open-browser",
                help="Print the PIN URL and code without opening a browser.",
            ),
        ] = False,
        storage: Annotated[
            CredentialStorage,
            typer.Option(help="Credential storage; file must be chosen explicitly."),
        ] = CredentialStorage.KEYRING,
        json_output: Annotated[bool, typer.Option("--json", help="Emit one JSON result.")] = False,
    ) -> None:
        """Start Simkl PIN Authorization."""
        runtime = runtime_factory()
        credential_source = CredentialSource(storage.value)
        if runtime.environ.get("SIMKL_ACCESS_TOKEN", "").strip():
            if json_output:
                typer.echo(json.dumps({"error": "environment_override_active"}))
            Console(stderr=True).print(
                "[red]SIMKL_ACCESS_TOKEN is active.[/red] Remove it before persistent login; "
                "a stored credential would be shadowed."
            )
            raise typer.Exit(1)
        try:
            with runtime.credentials.account_transaction():
                account = _complete_pin_authorization(
                    runtime,
                    credential_source,
                    no_open_browser=no_open_browser,
                    json_output=json_output,
                )
        except KeyboardInterrupt:
            if json_output:
                typer.echo(json.dumps({"error": "cancelled"}))
            Console(stderr=True).print(
                "[yellow]PIN Authorization cancelled; nothing was stored.[/yellow]"
            )
            raise typer.Exit(130) from None
        except (
            AuthenticationStateError,
            SimklResponseError,
            CredentialStorageError,
            httpx.HTTPError,
            OSError,
        ) as exc:
            if json_output:
                typer.echo(json.dumps({"error": "login_failed", "message": str(exc)}))
            Console(stderr=True).print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "authenticated": True,
                        "account": {"id": account.id, "name": account.name},
                        "credential_source": storage.value,
                    }
                )
            )
            return
        Console().print(
            Panel(
                f"{account.name} · account {account.id}\nCredential source: {storage.value}",
                title="Authenticated Account",
            )
        )

    def _auth_status_impl(
        runtime: Runtime,
        *,
        check: bool,
        json_output: bool,
    ) -> None:
        try:
            credential = runtime.active_credential()
        except CredentialStorageError as exc:
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "authenticated": False,
                            "account": None,
                            "credential_source": None,
                            "checked_online": False,
                            "error": "credential_storage_error",
                        },
                        separators=(",", ":"),
                    )
                )
            Console(stderr=True).print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None
        if credential is None:
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "authenticated": False,
                            "account": None,
                            "credential_source": None,
                            "checked_online": False,
                        },
                        separators=(",", ":"),
                    )
                )
                raise typer.Exit(1)
            Console().print(
                Panel(
                    "Run [bold]simkl auth login[/bold] to authorize this CLI.",
                    title="No Authenticated Account",
                )
            )
            raise typer.Exit(1)
        account = credential.account
        if not check and (credential.legacy or credential.invalidated):
            payload = {
                "authenticated": False,
                "account": {"id": account.id, "name": account.name} if account else None,
                "credential_source": credential.source.value,
                "checked_online": False,
                "requires_login": True,
                "auth_version": 1 if credential.legacy else 2,
            }
            if json_output:
                typer.echo(json.dumps(payload))
            else:
                Console().print(
                    "AUTH V2 authorization required. Run simkl auth login; "
                    "your Library Snapshot is retained."
                )
            raise typer.Exit(1)
        if check:
            try:
                credential, api = runtime.authenticated_client()
                checked_account = api.get_authenticated_account(credential.access_token)
                if (
                    credential.source is not CredentialSource.ENVIRONMENT
                    and account is not None
                    and checked_account.id != account.id
                ):
                    raise SimklResponseError(
                        "The Access Token belongs to a different account than local metadata."
                    )
                account = checked_account
                if credential.source is not CredentialSource.ENVIRONMENT:
                    current = runtime.credentials.read() or credential
                    runtime.credentials.save(replace(current, account=checked_account))
            except InvalidAccessTokenError as exc:
                if credential.source is not CredentialSource.ENVIRONMENT:
                    try:
                        current = runtime.credentials.read() or credential
                        runtime.credentials.save(replace(current, invalidated=True))
                    except (CredentialStorageError, OSError) as cleanup_error:
                        if json_output:
                            typer.echo(
                                json.dumps(
                                    {
                                        "authenticated": False,
                                        "account": None,
                                        "credential_source": credential.source.value,
                                        "checked_online": True,
                                        "error": "cleanup_failed",
                                    },
                                    separators=(",", ":"),
                                )
                            )
                        Console(stderr=True).print(
                            f"[red]{exc} Local cleanup failed: {cleanup_error}[/red]"
                        )
                        raise typer.Exit(1) from None
                message = (
                    f"{exc} The environment variable was not removed."
                    if credential.source is CredentialSource.ENVIRONMENT
                    else f"{exc} Run simkl auth login; the Library Snapshot was retained."
                )
                if json_output:
                    typer.echo(
                        json.dumps(
                            {
                                "authenticated": False,
                                "account": None,
                                "credential_source": credential.source.value,
                                "checked_online": True,
                                "error": "invalid_or_revoked",
                            },
                            separators=(",", ":"),
                        )
                    )
                Console(stderr=True).print(f"[red]{message}[/red]")
                raise typer.Exit(1) from None
            except (SimklResponseError, CredentialStorageError, httpx.HTTPError, OSError) as exc:
                if json_output:
                    typer.echo(
                        json.dumps(
                            {
                                "authenticated": False,
                                "account": None,
                                "credential_source": credential.source.value,
                                "checked_online": False,
                                "error": "check_failed",
                            },
                            separators=(",", ":"),
                        )
                    )
                Console(stderr=True).print(f"[red]{exc}[/red]")
                raise typer.Exit(1) from None
        payload = {
            "authenticated": True,
            "account": ({"id": account.id, "name": account.name} if account is not None else None),
            "credential_source": credential.source.value,
            "checked_online": check,
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            return
        account_text = (
            f"{account.name} · account {account.id}"
            if account is not None
            else "Account identity not recorded locally"
        )
        Console().print(
            Panel(
                f"{account_text}\nCredential source: {credential.source.value}\n"
                + (
                    "Access Token valid (checked online)"
                    if check
                    else "Access Token not checked online"
                ),
                title="Authenticated Account",
            )
        )

    @auth_app.command("status")
    def auth_status(
        check: Annotated[
            bool,
            typer.Option(help="Validate the Access Token online and refresh the display name."),
        ] = False,
        json_output: Annotated[
            bool,
            typer.Option("--json", help="Emit one machine-readable payload on stdout."),
        ] = False,
    ) -> None:
        """Show the locally recorded Authenticated Account."""
        runtime = runtime_factory()
        environment_active = bool(runtime.environ.get("SIMKL_ACCESS_TOKEN", "").strip())
        transaction = (
            runtime.credentials.account_transaction()
            if check and not environment_active
            else nullcontext()
        )
        try:
            with transaction:
                _auth_status_impl(runtime, check=check, json_output=json_output)
        except OSError as exc:
            if json_output:
                typer.echo(
                    json.dumps(
                        {
                            "authenticated": False,
                            "account": None,
                            "credential_source": None,
                            "checked_online": False,
                            "error": "credential_storage_error",
                        },
                        separators=(",", ":"),
                    )
                )
            Console(stderr=True).print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None

    @auth_app.command("logout")
    def auth_logout(
        yes: Annotated[
            bool,
            typer.Option("--yes", "-y", help="Confirm local credential deletion."),
        ] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Emit one JSON result.")] = False,
        local_only: Annotated[
            bool, typer.Option(help="Remove local credentials without remote revocation.")
        ] = False,
    ) -> None:
        """Remove local credentials and request revocation of the stored V2 grant."""
        runtime = runtime_factory()
        environment_active = bool(runtime.environ.get("SIMKL_ACCESS_TOKEN", "").strip())
        credential = None
        try:
            with runtime.credentials.account_transaction():
                account = runtime.credentials.read_account()
                if account is None:
                    if json_output:
                        typer.echo(json.dumps({"error": "no_local_credentials"}))
                    if environment_active:
                        Console(stderr=True).print(
                            "[yellow]SIMKL_ACCESS_TOKEN is active and cannot be removed by "
                            "logout. Unset the environment variable in its source.[/yellow]"
                        )
                    else:
                        Console(stderr=True).print(
                            "[yellow]No local credentials are stored.[/yellow]"
                        )
                    raise typer.Exit(1)
                if not yes and (json_output or not runtime.interactive):
                    raise AuthenticationStateError("Noninteractive logout requires --yes.")
                if not yes and not typer.confirm(
                    "Delete local credentials and Library Snapshot, and request V2 revocation?"
                ):
                    raise typer.Abort()
                credential = runtime.credentials.read()
                with runtime.snapshots.reversible_delete(account.id):
                    runtime.credentials.delete()
        except KeyboardInterrupt:
            if json_output:
                typer.echo(json.dumps({"error": "cancelled"}))
            Console(stderr=True).print(
                "[yellow]Logout cancelled; local credentials and snapshot were preserved.[/yellow]"
            )
            raise typer.Exit(130) from None
        except (AuthenticationStateError, CredentialStorageError, OSError) as exc:
            if json_output:
                typer.echo(json.dumps({"error": "logout_failed", "message": str(exc)}))
            Console(stderr=True).print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None
        acknowledged = False
        if credential is not None and credential.client_id and not local_only:
            try:
                SimklClient(
                    client_id=credential.client_id,
                    transport=runtime.transport,
                    sleep=runtime.sleep,
                    rate_gate=runtime.rate_gate,
                ).revoke_token(credential.refresh_token or credential.access_token)
                acknowledged = True
            except (SimklResponseError, httpx.HTTPError, OSError, KeyboardInterrupt):
                Console(stderr=True).print(
                    "Local logout completed, but remote revocation was not acknowledged. "
                    "Disconnect this grant in Simkl Connected Apps if needed."
                )
        message = "Local credentials, account metadata, and Library Snapshot were removed. " + (
            "Simkl acknowledged the revocation request; its response does not prove "
            "which token was revoked."
            if acknowledged
            else "The remote Access Token remains active or unverified; revoke it in "
            "Simkl Connected Apps if needed."
        )
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "logged_out": True,
                        "remote_token_revoked": False,
                        "remote_revocation_acknowledged": acknowledged,
                        "environment_override_active": environment_active,
                        "message": message,
                    }
                )
            )
        else:
            Console().print(message)
        if environment_active:
            Console(stderr=True).print(
                "[yellow]SIMKL_ACCESS_TOKEN is still active and cannot be removed by "
                "logout.[/yellow]"
            )

    register_commands(app, runtime_factory)
    return app


app = create_app()
