import json
from collections.abc import Callable
from contextlib import nullcontext
from enum import StrEnum
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.panel import Panel

from simklcli.api import InvalidAccessTokenError, SimklResponseError
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
) -> Account:
    if runtime.credentials.read() is not None:
        raise AuthenticationStateError(
            "An Authenticated Account is already stored. "
            "Run simkl auth logout before switching accounts."
        )
    runtime.credentials.ensure_available(credential_source)
    api = runtime.api_client()
    pin = api.start_pin_authorization()
    Console().print(
        Panel(
            f"Open {pin.verification_url} and enter "
            f"[bold cyan]{pin.user_code}[/bold cyan]\n"
            f"Expires in {pin.expires_in} seconds · poll every {pin.interval} seconds",
            title="PIN Authorization",
        )
    )
    if not no_open_browser and runtime.interactive:
        try:
            runtime.open_browser(pin.verification_url)
        except Exception:
            Console(stderr=True).print(
                "[yellow]Could not open a browser; continue with the URL above.[/yellow]"
            )
    deadline = runtime.monotonic() + pin.expires_in
    token: str | None = None
    while token is None:
        remaining = deadline - runtime.monotonic()
        if remaining <= 0:
            raise SimklResponseError("PIN Authorization expired before approval.")
        runtime.sleep(min(float(pin.interval), remaining))
        token = api.poll_pin_authorization(pin.user_code)
        if token is None:
            remaining_seconds = max(0, int(deadline - runtime.monotonic()))
            Console().print(f"Waiting for approval… {remaining_seconds}s remaining")
    account = api.get_authenticated_account(token)
    runtime.credentials.write(
        access_token=token,
        account=account,
        source=credential_source,
    )
    return account


def create_app(runtime_factory: Callable[[], Runtime] = default_runtime) -> typer.Typer:
    app = typer.Typer(
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
    ) -> None:
        """Start Simkl PIN Authorization."""
        runtime = runtime_factory()
        credential_source = CredentialSource(storage.value)
        if runtime.environ.get("SIMKL_ACCESS_TOKEN", "").strip():
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
                )
        except KeyboardInterrupt:
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
            Console(stderr=True).print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None
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
        if check:
            try:
                checked_account = runtime.api_client().get_authenticated_account(
                    credential.access_token
                )
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
                    runtime.credentials.write(
                        access_token=credential.access_token,
                        account=checked_account,
                        source=credential.source,
                    )
            except InvalidAccessTokenError as exc:
                if credential.source is not CredentialSource.ENVIRONMENT:
                    try:
                        if credential.account is None:
                            runtime.credentials.delete()
                        else:
                            with runtime.snapshots.reversible_delete(credential.account.id):
                                runtime.credentials.delete()
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
                    else f"{exc} Local credentials were removed; run simkl auth login."
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
    ) -> None:
        """Delete local credentials; remote authorization remains active."""
        runtime = runtime_factory()
        environment_active = bool(runtime.environ.get("SIMKL_ACCESS_TOKEN", "").strip())
        try:
            with runtime.credentials.account_transaction():
                account = runtime.credentials.read_account()
                if account is None:
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
                if not yes and not typer.confirm(
                    "Delete the local Access Token, account metadata, and Library Snapshot?"
                ):
                    raise typer.Abort()
                with runtime.snapshots.reversible_delete(account.id):
                    runtime.credentials.delete()
        except KeyboardInterrupt:
            Console(stderr=True).print(
                "[yellow]Logout cancelled; local credentials and snapshot were preserved.[/yellow]"
            )
            raise typer.Exit(130) from None
        except (CredentialStorageError, OSError) as exc:
            Console(stderr=True).print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None
        Console().print(
            "Local credentials, account metadata, and Library Snapshot were removed. "
            "The remote Access Token remains active; revoke it in Simkl Connected Apps if needed."
        )
        if environment_active:
            Console(stderr=True).print(
                "[yellow]SIMKL_ACCESS_TOKEN is still active and cannot be removed by "
                "logout.[/yellow]"
            )

    return app


app = create_app()
