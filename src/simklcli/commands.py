"""Public tracking command surface and shared terminal/JSON behavior."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import replace
from typing import Annotated, Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from simklcli.api import InvalidAccessTokenError, SimklResponseError
from simklcli.catalog import AmbiguousReferenceError, Catalog
from simklcli.media import ListStatus, Media, MediaKind
from simklcli.runtime import Runtime
from simklcli.storage import CredentialSource, CredentialStorageError
from simklcli.targets import select_target, viewing_time
from simklcli.tracking import Tracking, WriteOutcomeError

Reference = Annotated[str, typer.Argument(help="Media Reference: qualified ID or title text.")]
KindOption = Annotated[MediaKind | None, typer.Option(help="Media kind or ID namespace.")]
YearOption = Annotated[int | None, typer.Option(min=1, help="Disambiguate by release year.")]
JsonOption = Annotated[bool, typer.Option("--json", help="Emit one JSON payload; never prompt.")]
YesOption = Annotated[
    bool, typer.Option("--yes", "-y", help="Confirm noninteractive or bulk operations.")
]
SeasonOption = Annotated[int | None, typer.Option(min=0, help="Show Season; omit for Anime.")]
EpisodeOption = Annotated[int | None, typer.Option(min=1, help="Canonical Episode Number.")]
AllOption = Annotated[bool, typer.Option("--all", help="Target every applicable Episode.")]
EpisodeIdOption = Annotated[
    str | None, typer.Option(help="Qualified TVDB/AniDB Episode ID within this parent.")
]


def _media_table(rows: list[dict[str, Any]]) -> Table:
    table = Table()
    for name in ["Simkl ID", "Title", "Year", "Kind", "List Status", "User Rating"]:
        table.add_column(name)
    for row in rows:
        table.add_row(
            str(row["simkl_id"]),
            row["title"],
            str(row.get("year") or ""),
            row["kind"],
            str(row.get("list_status", "")),
            str(row.get("user_rating") or ""),
        )
    return table


def _confirm(runtime: Runtime, *, yes: bool, json_output: bool, message: str) -> None:
    if yes:
        return
    if json_output or not runtime.interactive:
        raise ValueError("This operation requires --yes in JSON or noninteractive use.")
    if not typer.confirm(message):
        raise typer.Abort()


def _resolve(
    runtime: Runtime,
    catalog: Catalog,
    reference: str,
    kind: MediaKind | None,
    year: int | None,
    json_output: bool,
) -> Media:
    try:
        return catalog.resolve(reference, kind=kind, year=year)
    except AmbiguousReferenceError as exc:
        if json_output or not runtime.interactive:
            raise
        Console().print(_media_table([item.identity() for item in exc.candidates]))
        choice = typer.prompt("Choose candidate number (starting at 1)", type=int)
        if not 1 <= choice <= len(exc.candidates):
            raise ValueError("Invalid candidate selection.") from exc
        chosen = exc.candidates[choice - 1]
        return catalog.details(chosen.simkl_id, chosen.kind)


def _tracking(runtime: Runtime, *, offline: bool = False) -> Tracking:
    credential = runtime.active_credential()
    if credential is None:
        raise ValueError("No Access Token is available. Run simkl auth login explicitly.")
    account = credential.account
    account_id = account.id if account is not None else None
    if offline:
        api = runtime.api_client()
    else:
        credential, api = runtime.authenticated_client()
    if credential.source is CredentialSource.ENVIRONMENT:
        if offline:
            account_id = runtime.identity_gate().remembered_account_id(credential.access_token)
            if account_id is None:
                stored = runtime.credentials.read()
                if stored is None or stored.access_token != credential.access_token:
                    raise ValueError(
                        "An environment override needs online account validation "
                        "before Library reads."
                    )
                account_id = stored.account.id if stored.account is not None else None
        else:
            account = api.get_authenticated_account(credential.access_token)
            account_id = account.id
            runtime.identity_gate().bind_account(credential.access_token, account.id)
    if account_id is None:
        raise ValueError("The active credential has no validated Authenticated Account.")
    if runtime.rate_gate is not None:
        runtime.rate_gate.bind_account(credential.access_token, account_id)
    return Tracking(
        api,
        runtime.snapshots,
        account_id=account_id,
        access_token=api.access_token or credential.access_token,
    )


def _invalidate(runtime: Runtime) -> None:
    credential = runtime.active_credential()
    if credential is not None and credential.source is not CredentialSource.ENVIRONMENT:
        runtime.credentials.save(replace(credential, invalidated=True))


def _catalog(runtime: Runtime) -> Catalog:
    if runtime.active_credential() is None:
        return Catalog(runtime.api_client())
    _, api = runtime.authenticated_client()
    return Catalog(api)


def _run(
    runtime_factory: Callable[[], Runtime],
    json_output: bool,
    action: Callable[[Runtime], dict[str, Any]],
    *,
    authenticated: bool = False,
) -> None:
    runtime = runtime_factory()
    try:
        with runtime.credentials.account_transaction() if authenticated else nullcontext():
            try:
                payload = action(runtime)
            except InvalidAccessTokenError:
                _invalidate(runtime)
                raise
    except (
        ValueError,
        SimklResponseError,
        CredentialStorageError,
        httpx.HTTPError,
        OSError,
        KeyboardInterrupt,
        typer.Abort,
    ) as exc:
        error: dict[str, Any] = {"error": "command_failed", "message": str(exc) or "Cancelled."}
        if isinstance(exc, AmbiguousReferenceError):
            error.update(
                error="ambiguous_reference", candidates=[m.identity() for m in exc.candidates]
            )
        if isinstance(exc, WriteOutcomeError):
            error.update(outcome=exc.outcome, **exc.details)
        if isinstance(exc, InvalidAccessTokenError):
            error["error"] = "invalid_or_revoked"
            error["message"] += (
                " Require explicit login; environment tokens cannot be removed by this CLI."
            )
            if exc.remote_success:
                error["remote_success"] = True
                error["message"] += (
                    " The write succeeded before reconciliation failed; do not repeat it."
                )
        if json_output:
            typer.echo(json.dumps(error, ensure_ascii=False, separators=(",", ":")))
        Console(stderr=True).print(error["message"], markup=False)
        if "captured_state" in error and not json_output:
            Console(stderr=True).print(
                json.dumps(error["captured_state"], ensure_ascii=False), markup=False
            )
        exit_code = (
            exc.exit_code
            if isinstance(exc, WriteOutcomeError)
            else (130 if isinstance(exc, KeyboardInterrupt) else 1)
        )
        raise typer.Exit(exit_code) from None
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    elif "items" in payload or "results" in payload:
        Console().print(_media_table(payload.get("items", payload.get("results", []))))
        if "snapshot_age_seconds" in payload:
            Console().print(
                f"{payload['source']} · snapshot age {payload['snapshot_age_seconds']}s",
                markup=False,
            )
    elif "metadata" in payload:
        Console().print(_media_table([payload["item"]]))
        Console().print_json(data=payload["metadata"])
    else:
        Console().print(payload["message"], markup=False)
        if payload.get("state"):
            Console().print(_media_table([payload["state"]]))


def register_commands(app: typer.Typer, runtime_factory: Callable[[], Runtime]) -> None:
    library = typer.Typer(help="Read and change Library state.", no_args_is_help=True)
    watched = typer.Typer(help="Mark or unmark Watched State.", no_args_is_help=True)
    rating = typer.Typer(help="Set or remove User Ratings.", no_args_is_help=True)
    app.add_typer(library, name="library")
    app.add_typer(watched, name="watched")
    app.add_typer(rating, name="rating")

    @app.command()
    def search(
        query: Reference,
        kind: KindOption = None,
        year: YearOption = None,
        limit: Annotated[int, typer.Option(min=1, max=50, help="Results per media kind.")] = 10,
        page: Annotated[int, typer.Option(min=1, max=20)] = 1,
        json_output: JsonOption = False,
    ) -> None:
        """Search the catalog for candidate Media Items."""

        def action(runtime: Runtime) -> dict[str, Any]:
            results = _catalog(runtime).search(query, kind=kind, year=year, limit=limit, page=page)
            return {
                "query": query,
                "results": [i.identity() for i in results.items],
                "next_page": results.next_page,
            }

        _run(runtime_factory, json_output, action, authenticated=True)

    @app.command()
    def lookup(
        reference: Reference,
        kind: KindOption = None,
        year: YearOption = None,
        json_output: JsonOption = False,
    ) -> None:
        """Resolve a Media Reference and fetch current Catalog Metadata."""

        def action(runtime: Runtime) -> dict[str, Any]:
            item = _resolve(runtime, _catalog(runtime), reference, kind, year, json_output)
            return {"item": item.identity(), "metadata": item.metadata}

        _run(runtime_factory, json_output, action, authenticated=True)

    @library.command("list")
    def library_list(
        status: Annotated[ListStatus | None, typer.Option()] = None,
        kind: KindOption = None,
        offline: Annotated[bool, typer.Option()] = False,
        json_output: JsonOption = False,
    ) -> None:
        """Reconcile the Library online, or read a dated complete snapshot."""

        def action(runtime: Runtime) -> dict[str, Any]:
            snapshot = _tracking(runtime, offline=offline).read(offline=offline)
            return {
                "items": [
                    e.payload()
                    for e in snapshot.entries.values()
                    if (kind is None or e.item.kind is kind)
                    and (status is None or e.list_status is status)
                ],
                "source": "library-snapshot" if offline else "simkl-reconciled",
                "snapshot_age_seconds": max(0, int(time.time() - snapshot.reconciled_at)),
                "dirty": snapshot.dirty,
            }

        _run(runtime_factory, json_output, action, authenticated=True)

    @library.command("repair")
    def library_repair(json_output: JsonOption = False) -> None:
        """Rebuild the complete Library Snapshot from Simkl."""

        def action(runtime: Runtime) -> dict[str, Any]:
            snapshot = _tracking(runtime).read(repair=True)
            return {"message": "Library Snapshot rebuilt.", "count": len(snapshot.entries)}

        _run(runtime_factory, json_output, action, authenticated=True)

    @library.command("set-status")
    def library_set_status(
        reference: Reference,
        status: ListStatus,
        kind: KindOption = None,
        year: YearOption = None,
        json_output: JsonOption = False,
    ) -> None:
        """Add a Media Item or change its List Status."""

        def action(runtime: Runtime) -> dict[str, Any]:
            service = _tracking(runtime)
            item = _resolve(runtime, Catalog(service.api), reference, kind, year, json_output)
            return service.set_status(item, status)

        _run(runtime_factory, json_output, action, authenticated=True)

    @library.command("remove")
    def library_remove(
        reference: Reference,
        yes: YesOption = False,
        kind: KindOption = None,
        year: YearOption = None,
        json_output: JsonOption = False,
    ) -> None:
        """Remove an item together with its Watched State and User Rating."""

        def action(runtime: Runtime) -> dict[str, Any]:
            service = _tracking(runtime)
            item = _resolve(runtime, Catalog(service.api), reference, kind, year, json_output)
            _confirm(
                runtime,
                yes=yes,
                json_output=json_output,
                message=f"Remove {item.title}, its Watched State, and its User Rating?",
            )
            return service.remove(item)

        _run(runtime_factory, json_output, action, authenticated=True)

    @watched.command("mark")
    def watched_mark(
        reference: Reference,
        season: SeasonOption = None,
        episode: EpisodeOption = None,
        all_episodes: AllOption = False,
        episode_id: EpisodeIdOption = None,
        yes: YesOption = False,
        watched_at: Annotated[str | None, typer.Option("--at")] = None,
        rewatch: Annotated[bool, typer.Option()] = False,
        rewatch_id: Annotated[int | None, typer.Option(min=1)] = None,
        kind: KindOption = None,
        year: YearOption = None,
        json_output: JsonOption = False,
    ) -> None:
        """Record a Viewing or an explicit Bulk Watched Update."""

        def action(runtime: Runtime) -> dict[str, Any]:
            at = viewing_time(watched_at)
            service = _tracking(runtime)
            catalog = Catalog(service.api)
            item = _resolve(runtime, catalog, reference, kind, year, json_output)
            target = select_target(
                catalog,
                item,
                season=season,
                episode=episode,
                all_episodes=all_episodes,
                episode_id=episode_id,
            )
            if target.bulk:
                _confirm(
                    runtime,
                    yes=yes,
                    json_output=json_output,
                    message=f"Mark selected Episodes of {item.title} watched?",
                )
            return service.mark(item, target, watched_at=at, rewatch=rewatch, rewatch_id=rewatch_id)

        _run(runtime_factory, json_output, action, authenticated=True)

    @watched.command("unmark")
    def watched_unmark(
        reference: Reference,
        season: SeasonOption = None,
        episode: EpisodeOption = None,
        all_episodes: AllOption = False,
        episode_id: EpisodeIdOption = None,
        yes: YesOption = False,
        kind: KindOption = None,
        year: YearOption = None,
        json_output: JsonOption = False,
    ) -> None:
        """Clear Watched State and preserve Library membership, status, and rating."""

        def action(runtime: Runtime) -> dict[str, Any]:
            service = _tracking(runtime)
            catalog = Catalog(service.api)
            item = _resolve(runtime, catalog, reference, kind, year, json_output)
            target = select_target(
                catalog,
                item,
                season=season,
                episode=episode,
                all_episodes=all_episodes,
                episode_id=episode_id,
                marking=False,
            )
            if target.bulk:
                _confirm(
                    runtime,
                    yes=yes,
                    json_output=json_output,
                    message=f"Clear selected Episodes of {item.title}?",
                )
            return service.unmark(item, target)

        _run(runtime_factory, json_output, action, authenticated=True)

    @rating.command("set")
    def rating_set(
        reference: Reference,
        score: Annotated[int, typer.Argument(min=1, max=10)],
        kind: KindOption = None,
        year: YearOption = None,
        json_output: JsonOption = False,
    ) -> None:
        """Set a User Rating from 1 to 10."""

        def action(runtime: Runtime) -> dict[str, Any]:
            service = _tracking(runtime)
            item = _resolve(runtime, Catalog(service.api), reference, kind, year, json_output)
            return service.rating(item, score)

        _run(runtime_factory, json_output, action, authenticated=True)

    @rating.command("remove")
    def rating_remove(
        reference: Reference,
        kind: KindOption = None,
        year: YearOption = None,
        json_output: JsonOption = False,
    ) -> None:
        """Remove a User Rating while retaining the Library item."""

        def action(runtime: Runtime) -> dict[str, Any]:
            service = _tracking(runtime)
            item = _resolve(runtime, Catalog(service.api), reference, kind, year, json_output)
            return service.rating(item, None)

        _run(runtime_factory, json_output, action, authenticated=True)
