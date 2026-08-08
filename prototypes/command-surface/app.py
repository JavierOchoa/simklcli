#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "typer>=0.16,<1",
#   "rich>=14,<15",
# ]
# ///
"""PROTOTYPE ONLY: a mock command surface for the proposed simkl CLI."""

from __future__ import annotations

import json
import sys
from enum import Enum
from typing import Annotated, Any

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


class Kind(str, Enum):
    movie = "movie"
    show = "show"
    anime = "anime"


class ListStatus(str, Enum):
    plan_to_watch = "plan-to-watch"
    watching = "watching"
    on_hold = "on-hold"
    dropped = "dropped"
    completed = "completed"


class CredentialStorage(str, Enum):
    keyring = "keyring"
    file = "file"


app = typer.Typer(
    name="simkl",
    help="Track movies, shows, and anime on Simkl. [PROTOTYPE: no API calls]",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
auth_app = typer.Typer(help="Authorize this CLI for one Simkl account.", no_args_is_help=True)
library_app = typer.Typer(help="Read and change your Simkl Library.", no_args_is_help=True)
watched_app = typer.Typer(help="Mark or unmark Watched State.", no_args_is_help=True)
rating_app = typer.Typer(help="Set or remove your User Rating.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")
app.add_typer(library_app, name="library")
app.add_typer(watched_app, name="watched")
app.add_typer(rating_app, name="rating")

console = Console()
error_console = Console(stderr=True)

PROTOTYPE_NOTICE = "PROTOTYPE — mock output; no API call or local change occurred"

MOCK_ITEMS = [
    {
        "simkl_id": 3708,
        "kind": "anime",
        "title": "Cowboy Bebop",
        "year": 1998,
        "list_status": "completed",
        "watched": True,
        "user_rating": 10,
        "community_rating": 8.8,
    },
    {
        "simkl_id": 17465,
        "kind": "show",
        "title": "Stranger Things",
        "year": 2016,
        "list_status": "watching",
        "watched": False,
        "user_rating": None,
        "community_rating": 8.4,
    },
    {
        "simkl_id": 53536,
        "kind": "movie",
        "title": "The Matrix",
        "year": 1999,
        "list_status": "plan-to-watch",
        "watched": False,
        "user_rating": None,
        "community_rating": 8.6,
    },
]


def emit_json(payload: Any) -> None:
    console.print_json(json.dumps(payload, ensure_ascii=False))
    error_console.print(f"[dim]{PROTOTYPE_NOTICE}[/dim]")


def emit_trace(*, remote: str, local: str, resolution: str) -> None:
    body = f"[bold]Reference resolution[/bold]\n{resolution}\n\n[bold]Remote intent[/bold]\n{remote}\n\n[bold]Local intent[/bold]\n{local}"
    console.print(Panel(body, title=PROTOTYPE_NOTICE, border_style="yellow"))


def media_table(items: list[dict[str, Any]], *, library: bool = False) -> Table:
    table = Table(box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Kind")
    table.add_column("Title")
    table.add_column("Year", justify="right")
    table.add_column("Simkl ID", justify="right")
    if library:
        table.add_column("List Status")
        table.add_column("Watched")
        table.add_column("Your Rating", justify="right")
    else:
        table.add_column("Community", justify="right")
    for item in items:
        row = [item["kind"], item["title"], str(item["year"]), str(item["simkl_id"])]
        if library:
            row.extend(
                [
                    item["list_status"],
                    "yes" if item["watched"] else "no",
                    "—" if item["user_rating"] is None else str(item["user_rating"]),
                ]
            )
        else:
            row.append(str(item["community_rating"]))
        table.add_row(*row)
    return table


def ref_text(reference: str, kind: Kind | None, year: int | None) -> str:
    if reference.startswith(("simkl:", "imdb:", "tvdb:", "mal:", "anidb:")):
        return f"Resolve qualified reference `{reference}` directly."
    qualifiers = ", ".join(filter(None, [kind.value if kind else None, str(year) if year else None]))
    suffix = f" ({qualifiers})" if qualifiers else ""
    return f"Search title `{reference}`{suffix}; prompt on ambiguity only in an interactive terminal, otherwise fail with candidates."


def watched_target(
    season: int | None,
    episode: int | None,
    all_episodes: bool,
    *,
    allow_standalone: bool = True,
) -> str:
    if all_episodes and episode is not None:
        raise typer.BadParameter("--all cannot be combined with --episode")
    if season is not None and all_episodes:
        return f"every Episode in Season {season}"
    if all_episodes:
        return "every applicable Episode"
    if season is not None and episode is not None:
        return f"Season {season}, Episode {episode}"
    if episode is not None:
        return f"Anime Episode {episode}"
    if season is not None:
        raise typer.BadParameter("marking a whole Season requires --all")
    if not allow_standalone:
        raise typer.BadParameter(
            "unmark requires --episode or --all; removing a standalone item is `library remove`"
        )
    return "the standalone item (reject an episodic parent unless --all is explicit)"


@app.command()
def surface() -> None:
    """Explain the command grammar and scriptability contract under test."""
    table = Table(title="Proposed command surface", box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("Workflow")
    table.add_column("Commands")
    table.add_column("Design intent")
    rows = [
        ("Authentication", "auth login | status | logout", "One Authenticated Account; explicit lifecycle."),
        ("Catalog", "search | lookup", "Search returns candidates; lookup resolves one Media Reference."),
        ("Library", "library list | set-status | remove | repair", "Use domain language; synchronization stays internal."),
        ("Watched State", "watched mark | unmark", "Keep Viewing separate from List Status."),
        ("User Rating", "rating set | remove", "The verb carries the mutation; score is positional."),
    ]
    for row in rows:
        table.add_row(*row)
    console.print(table)
    console.print(
        Panel(
            "[bold]Media References[/bold] accept `simkl:3708`, qualified External IDs such as `imdb:tt0133093`, or title text with optional --kind/--year.\n\n"
            "[bold]Human output[/bold] uses Rich tables and panels. Each read command offers --json; JSON is the only stdout payload and disables prompts. Diagnostics go to stderr.\n\n"
            "[bold]Safety[/bold] prompts only for destructive whole-item actions in an interactive terminal; scripts must pass --yes. Ambiguous title resolution fails nonzero in non-interactive/JSON use.\n\n"
            "[bold]Freshness[/bold] Library reads reconcile automatically. --offline explicitly reads a dated Library Snapshot; library repair explicitly rebuilds it.",
            title="Hypothesis",
        )
    )


@auth_app.command("login")
def auth_login(
    no_open_browser: Annotated[bool, typer.Option("--no-open-browser", help="Print the PIN URL and code without opening a browser.")] = False,
    storage: Annotated[CredentialStorage, typer.Option(help="Credential storage; file must be chosen explicitly.")] = CredentialStorage.keyring,
) -> None:
    """Start Simkl PIN Authorization."""
    console.print(Panel("Open https://simkl.com/pin and enter [bold cyan]ABCDE[/bold cyan]\nExpires in 15:00 · poll every 5 seconds", title="PIN Authorization"))
    emit_trace(
        resolution="No Media Reference.",
        remote=f"Request a PIN, {'do not open' if no_open_browser else 'best-effort open'} the browser, poll until approved, then validate the account.",
        local=f"Persist the Access Token in {storage.value} storage only after validation; record account 12345 (Javier).",
    )


@auth_app.command("status")
def auth_status(
    check: Annotated[bool, typer.Option(help="Validate the active Access Token online and refresh the display name.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON and never prompt.")] = False,
) -> None:
    """Show the locally recorded Authenticated Account."""
    payload = {"authenticated": True, "account": {"id": 12345, "name": "Javier"}, "credential_source": "keyring", "checked_online": check}
    if json_output:
        emit_json(payload)
        return
    console.print(Panel("Javier · account 12345\nCredential source: keyring\n" + ("Access Token valid (checked online)" if check else "Access Token not checked online"), title="Authenticated Account"))


@auth_app.command("logout")
def auth_logout(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Confirm local credential deletion for non-interactive use.")] = False,
) -> None:
    """Delete local credentials; the remote Access Token remains active."""
    confirmed = yes or typer.confirm("Delete the local Access Token and Library Snapshot?")
    if not confirmed:
        raise typer.Abort()
    emit_trace(
        resolution="No Media Reference.",
        remote="Do nothing. Remote revocation remains available in Simkl Connected Apps.",
        local="Delete persisted Access Token, Authenticated Account metadata, and that account's Library Snapshot.",
    )


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Title text to search for.")],
    kind: Annotated[Kind | None, typer.Option(help="Limit results to one media kind.")] = None,
    year: Annotated[int | None, typer.Option(help="Prefer an exact release year.")] = None,
    limit: Annotated[int, typer.Option(min=1, max=50)] = 10,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON and never prompt.")] = False,
) -> None:
    """Search the Simkl catalog and return candidate Media Items."""
    items = [item for item in MOCK_ITEMS if kind is None or item["kind"] == kind.value][:limit]
    if year is not None:
        items = [item for item in items if item["year"] == year]
    payload = {"query": query, "results": items, "next_page": None}
    if json_output:
        emit_json(payload)
        return
    console.print(media_table(items))
    console.print(f"[dim]Mock candidates for {query!r}. Use `simkl lookup simkl:<id>` to inspect one.[/dim]")


@app.command()
def lookup(
    reference: Annotated[str, typer.Argument(help="Media Reference: qualified ID or title text.")],
    kind: Annotated[Kind | None, typer.Option(help="Disambiguate title or provider-specific IDs.")] = None,
    year: Annotated[int | None, typer.Option(help="Disambiguate title text by release year.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON and never prompt.")] = False,
) -> None:
    """Resolve one Media Reference and show Catalog Metadata."""
    item = MOCK_ITEMS[0]
    payload = {"item": item, "episodes": 26, "anime_kind": "tv", "reference": reference}
    if json_output:
        emit_json(payload)
        return
    console.print(Panel("Cowboy Bebop (1998)\nAnime · TV Anime · 26 episodes\nSimkl ID 3708\nCommunity Rating 8.8", title="Catalog Metadata"))
    emit_trace(remote="Resolve the Media Reference, then fetch current Catalog Metadata.", local="Do not persist Catalog Metadata.", resolution=ref_text(reference, kind, year))


@library_app.command("list")
def library_list(
    status: Annotated[ListStatus | None, typer.Option(help="Filter by List Status.")] = None,
    kind: Annotated[Kind | None, typer.Option(help="Filter by media kind.")] = None,
    offline: Annotated[bool, typer.Option(help="Read the last complete Library Snapshot without network access.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON and never prompt.")] = False,
) -> None:
    """List Library items, reconciling online unless --offline is explicit."""
    items = MOCK_ITEMS
    if status is not None:
        items = [item for item in items if item["list_status"] == status.value]
    if kind is not None:
        items = [item for item in items if item["kind"] == kind.value]
    payload = {"items": items, "source": "library-snapshot" if offline else "simkl-reconciled", "snapshot_age_seconds": 312 if offline else 0}
    if json_output:
        emit_json(payload)
        return
    if offline:
        console.print("[yellow]Offline Library Snapshot · last reconciled 5 minutes ago[/yellow]")
    else:
        console.print("[green]Library reconciled with Simkl just now[/green]")
    console.print(media_table(items, library=True))


@library_app.command("set-status")
def library_set_status(
    reference: Annotated[str, typer.Argument(help="Media Reference: qualified ID or title text.")],
    status: Annotated[ListStatus, typer.Argument(help="New List Status.")],
    kind: Annotated[Kind | None, typer.Option(help="Disambiguate the Media Reference.")] = None,
    year: Annotated[int | None, typer.Option(help="Disambiguate title text.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit the proposed result as JSON and never prompt.")] = False,
) -> None:
    """Add a Media Item to the Library or change its List Status."""
    payload = {"item": {"simkl_id": 3708, "kind": "anime", "title": "Cowboy Bebop"}, "list_status": status.value, "changed": True}
    if json_output:
        emit_json(payload)
        return
    console.print(f"[green]Cowboy Bebop → {status.value}[/green]")
    emit_trace(remote=f"Set List Status to {status.value}; accept Simkl's normalized resulting state as authoritative.", local="Focused reconciliation updates the affected Library Snapshot entry.", resolution=ref_text(reference, kind, year))


@library_app.command("remove")
def library_remove(
    reference: Annotated[str, typer.Argument(help="Media Reference: qualified ID or title text.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Confirm removal for non-interactive use.")] = False,
    kind: Annotated[Kind | None, typer.Option(help="Disambiguate the Media Reference.")] = None,
    year: Annotated[int | None, typer.Option(help="Disambiguate title text.")] = None,
) -> None:
    """Remove a whole Media Item, including Watched State and User Rating."""
    confirmed = yes or typer.confirm("Remove this item, its Watched State, and its User Rating from the Library?")
    if not confirmed:
        raise typer.Abort()
    emit_trace(remote="Remove the whole item from the Library, including its Watched State and User Rating.", local="Focused reconciliation removes the Library Snapshot entry.", resolution=ref_text(reference, kind, year))


@library_app.command("repair")
def library_repair() -> None:
    """Discard and rebuild the current account's Library Snapshot."""
    emit_trace(remote="Fetch a complete Library after checking activity positions.", local="Atomically replace the current account's Library Snapshot.", resolution="No Media Reference; operates on the Authenticated Account.")


@watched_app.command("mark")
def watched_mark(
    reference: Annotated[str, typer.Argument(help="Media Reference: qualified ID or title text.")],
    season: Annotated[int | None, typer.Option(min=0, help="Show Season; omit for Anime.")] = None,
    episode: Annotated[int | None, typer.Option(min=1, help="Episode Number.")] = None,
    all_episodes: Annotated[bool, typer.Option("--all", help="Explicitly mark every applicable Episode.")] = False,
    watched_at: Annotated[str | None, typer.Option("--at", help="Viewing time as ISO-8601; defaults to now.")] = None,
    rewatch: Annotated[bool, typer.Option(help="Record a later Viewing; may require Simkl Pro/VIP.")] = False,
    kind: Annotated[Kind | None, typer.Option(help="Disambiguate the Media Reference.")] = None,
    year: Annotated[int | None, typer.Option(help="Disambiguate title text.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit the proposed result as JSON and never prompt.")] = False,
) -> None:
    """Record a Viewing or an explicit Bulk Watched Update."""
    target = watched_target(season, episode, all_episodes)
    payload = {"item": {"simkl_id": 3708}, "target": target, "watched_at": watched_at or "now", "rewatch": rewatch, "changed": True}
    if json_output:
        emit_json(payload)
        return
    console.print(f"[green]Marked {target} watched at {watched_at or 'now'}[/green]")
    emit_trace(remote=f"Record {'a Rewatch' if rewatch else 'Viewing/Watched State'} for {target}; inspect not_found and resolved status.", local="Focused reconciliation updates Watched State and any resulting List Status.", resolution=ref_text(reference, kind, year))


@watched_app.command("unmark")
def watched_unmark(
    reference: Annotated[str, typer.Argument(help="Parent Media Reference.")],
    season: Annotated[int | None, typer.Option(min=0, help="Show Season.")] = None,
    episode: Annotated[int | None, typer.Option(min=1, help="Episode Number.")] = None,
    all_episodes: Annotated[bool, typer.Option("--all", help="Explicitly clear every applicable Episode.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Confirm a bulk update for non-interactive use.")] = False,
) -> None:
    """Clear selected Watched State without removing the parent from the Library."""
    if all_episodes and not (yes or typer.confirm("Clear Watched State for every applicable Episode?")):
        raise typer.Abort()
    target = watched_target(season, episode, all_episodes, allow_standalone=False)
    emit_trace(remote=f"Clear Watched State for {target}; keep the parent Library item and User Rating.", local="Focused reconciliation updates the affected Library Snapshot state.", resolution=f"Resolve `{reference}` as the parent Media Reference.")


@rating_app.command("set")
def rating_set(
    reference: Annotated[str, typer.Argument(help="Movie, Show, or Anime Media Reference.")],
    score: Annotated[int, typer.Argument(min=1, max=10, help="User Rating from 1 to 10.")],
    kind: Annotated[Kind | None, typer.Option(help="Disambiguate the Media Reference.")] = None,
    year: Annotated[int | None, typer.Option(help="Disambiguate title text.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit the proposed result as JSON and never prompt.")] = False,
) -> None:
    """Set or replace the User Rating for a Movie, Show, or Anime."""
    payload = {"item": {"simkl_id": 3708}, "user_rating": score, "changed": True}
    if json_output:
        emit_json(payload)
        return
    console.print(f"[green]User Rating set to {score}/10[/green]")
    emit_trace(remote=f"Set User Rating to {score}; read back resulting rating and List Status.", local="Focused reconciliation updates User Rating and any auto-created Library entry.", resolution=ref_text(reference, kind, year))


@rating_app.command("remove")
def rating_remove(
    reference: Annotated[str, typer.Argument(help="Movie, Show, or Anime Media Reference.")],
    kind: Annotated[Kind | None, typer.Option(help="Disambiguate the Media Reference.")] = None,
    year: Annotated[int | None, typer.Option(help="Disambiguate title text.")] = None,
) -> None:
    """Remove the User Rating without removing the Library item."""
    emit_trace(remote="Remove the User Rating and verify the resulting Library state.", local="Focused reconciliation clears User Rating but retains the Library entry.", resolution=ref_text(reference, kind, year))


if __name__ == "__main__":
    try:
        app()
    except KeyboardInterrupt:
        error_console.print("Cancelled.")
        sys.exit(130)
