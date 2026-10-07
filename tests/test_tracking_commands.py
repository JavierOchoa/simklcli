from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from simklcli.cli import create_app
from simklcli.snapshots import LibrarySnapshot
from tests.simkl_fixture import SimklFixture


def invoke(server: SimklFixture, *args: str) -> dict[str, Any]:
    result = CliRunner().invoke(create_app(lambda: server.runtime), [*args, "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def test_core_loop_runs_through_real_cli_and_authored_transport(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    assert invoke(server, "search", "Anime", "--kind", "anime")["results"]
    assert invoke(server, "lookup", "imdb:tt123")["item"]["simkl_id"] == 1
    assert invoke(server, "library", "list")["items"] == []
    invoke(server, "library", "set-status", "simkl:3", "plan-to-watch")
    invoke(server, "watched", "mark", "simkl:3", "--episode", "1", "--at", "2026-08-09T00:00:00Z")
    invoke(server, "rating", "set", "simkl:3", "9")
    result = invoke(server, "watched", "unmark", "simkl:3", "--episode", "1")
    assert result["state"]["user_rating"] == 9
    assert result["state"]["list_status"] == "watching"
    assert result["state"]["watched"] is False
    invoke(server, "rating", "remove", "simkl:3")
    invoke(server, "watched", "mark", "simkl:3", "--all", "--yes")
    marked = server.posts()[-1]
    episodes = json.loads(marked.content)["anime"][0]["seasons"][0]["episodes"]
    assert [episode["number"] for episode in episodes] == [1, 2]  # Unaired Episode 3 excluded.
    invoke(server, "library", "remove", "simkl:3", "--yes")
    assert json.loads(server.posts()[-1].content) == {"shows": [{"ids": {"simkl": 3}}]}
    assert invoke(server, "library", "repair")["count"] == 0
    before = len(server.requests)
    assert invoke(server, "library", "list", "--offline")["source"] == "library-snapshot"
    assert len(server.requests) == before


@pytest.mark.parametrize("media_id", [1, 4])
def test_standalone_unmark_preserves_status_and_rating(tmp_path: Path, media_id: int) -> None:
    server = SimklFixture(tmp_path)
    server.put(media_id, status="dropped", rating=8, watched=True)
    result = invoke(server, "watched", "unmark", f"simkl:{media_id}")
    assert result["state"]["list_status"] == "dropped"
    assert result["state"]["user_rating"] == 8
    assert result["state"]["watched"] is False
    assert [r.url.path for r in server.posts()] == [
        "/sync/history/remove",
        "/sync/add-to-list",
        "/sync/ratings",
    ]
    snapshot = server.runtime.snapshots.load(100)
    assert snapshot is not None and not snapshot.complete
    assert "overview" not in server.runtime.snapshots.path(100).read_text()


@pytest.mark.parametrize(
    "failure,exit_code,outcome",
    [
        ("lost_applied", 0, None),
        ("lost_absent", 1, "not_applied"),
        ("partial", 1, "not_applied"),
        ("blocked", 1, None),
    ],
)
def test_lost_post_is_verified_and_never_replayed(
    tmp_path: Path, failure: str, exit_code: int, outcome: str | None
) -> None:
    server = SimklFixture(tmp_path)
    server.fail_post = failure
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["rating", "set", "simkl:1", "7", "--json"]
    )
    assert result.exit_code == exit_code, result.output
    payload = json.loads(result.stdout)
    if exit_code == 0:
        assert payload["recovered_by_readback"] is True
    elif outcome:
        assert payload["outcome"] == outcome
    assert len(server.posts()) == 1
    if failure == "blocked":
        assert not any(r.url.path == "/sync/activities" for r in server.requests)


def test_remote_success_and_failed_reconciliation_marks_dirty_without_retry(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.fail_after_post = True
    payload = invoke(server, "rating", "set", "simkl:1", "8")
    assert payload["remote_success"] and payload["local_state"] == "dirty"
    snapshot = server.runtime.snapshots.load(100)
    assert snapshot is not None and snapshot.dirty and not snapshot.complete
    server.fail_reads = False
    assert invoke(server, "library", "list")["items"][0]["user_rating"] == 8


def test_lost_post_and_failed_verification_reports_unknown(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.fail_post = "lost_applied"
    server.fail_after_post = True
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["rating", "set", "simkl:1", "8", "--json"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["outcome"] == "unknown"
    assert "blindly retry" in result.stderr
    assert len(server.posts()) == 1


def test_unmark_reports_captured_state_if_preservation_cannot_be_verified(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.put(1, status="dropped", rating=9, watched=True)
    server.fail_after_post = True
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["watched", "unmark", "simkl:1", "--json"]
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "partial_or_unknown"
    assert payload["captured_state"]["user_rating"] == 9
    assert server.runtime.snapshots.load(100).dirty  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "args",
    [
        ["library", "remove", "simkl:1"],
        ["watched", "mark", "simkl:3", "--all"],
        ["watched", "unmark", "simkl:3", "--all"],
    ],
)
def test_noninteractive_destructive_commands_require_yes_without_prompting(
    tmp_path: Path, args: list[str]
) -> None:
    server = SimklFixture(tmp_path)
    result = CliRunner().invoke(create_app(lambda: server.runtime), [*args, "--json"], input="y\n")
    assert result.exit_code == 1
    assert "--yes" in json.loads(result.stdout)["message"]
    assert server.posts() == []


@pytest.mark.parametrize(
    "args",
    [
        ["library", "set-status", "simkl:1", "watching"],
        ["watched", "mark", "simkl:2"],
        ["watched", "mark", "simkl:2", "--episode", "1"],
        ["watched", "mark", "simkl:3", "--season", "1", "--episode", "1"],
        ["watched", "mark", "simkl:1", "--episode", "1"],
        ["watched", "mark", "simkl:3", "--episode", "99"],
        ["watched", "mark", "simkl:3", "--episode", "1", "--all", "--yes"],
        ["watched", "mark", "simkl:3", "--at", "2026-08-09"],
        ["watched", "mark", "simkl:3", "--episode-id", "imdb:bad"],
        ["watched", "mark", "simkl:3", "--episode-id", "tvdb:5", "--episode", "1"],
    ],
)
def test_invalid_domain_operations_fail_before_write(tmp_path: Path, args: list[str]) -> None:
    server = SimklFixture(tmp_path)
    result = CliRunner().invoke(create_app(lambda: server.runtime), [*args, "--json"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["error"]
    assert not server.posts()


@pytest.mark.parametrize("score", ["0", "11"])
def test_rating_argument_range_is_checked(tmp_path: Path, score: str) -> None:
    server = SimklFixture(tmp_path)
    result = CliRunner().invoke(
        create_app(lambda: server.runtime), ["rating", "set", "simkl:1", score]
    )
    assert result.exit_code == 2
    assert not server.requests


def test_reference_ambiguity_returns_candidates_and_never_prompts_in_json(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    result = CliRunner().invoke(
        create_app(lambda: server.runtime),
        ["lookup", "Anime", "--kind", "anime", "--json"],
        input="1\n",
    )
    assert result.exit_code == 1
    assert len(json.loads(result.stdout)["candidates"]) == 2
    payload = invoke(server, "lookup", "Anime", "--kind", "anime", "--year", "1998")
    assert payload["item"]["simkl_id"] == 3


def test_online_reads_do_not_silently_use_stale_or_partial_state(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    server.runtime.snapshots.save(LibrarySnapshot(100))
    app = create_app(lambda: server.runtime)
    assert CliRunner().invoke(app, ["library", "list", "--offline", "--json"]).exit_code == 1
    invoke(server, "library", "list")
    previous = server.runtime.snapshots.path(100).read_bytes()
    server.fail_reads = True
    result = CliRunner().invoke(app, ["library", "list", "--json"])
    assert result.exit_code == 1
    assert server.runtime.snapshots.path(100).read_bytes() == previous
    assert invoke(server, "library", "list", "--offline")["items"] == []


def test_revoked_token_clears_credentials_and_snapshot(tmp_path: Path) -> None:
    server = SimklFixture(tmp_path)
    invoke(server, "library", "list")

    def revoked(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(401, json={"error": "user_token_failed"})

    server.runtime.transport = httpx.MockTransport(revoked)
    result = CliRunner().invoke(create_app(lambda: server.runtime), ["library", "list", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "invalid_or_revoked"
    assert server.runtime.credentials.read().invalidated  # type: ignore[union-attr]
    assert server.runtime.snapshots.path(100).exists()
