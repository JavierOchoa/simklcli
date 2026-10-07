"""Manually triggered, reserved-Movie smoke with a durable backup and verified restoration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from simklcli import __version__
from simklcli.catalog import Catalog
from simklcli.filesystem import atomic_write_json
from simklcli.media import ListStatus, MediaKind, positive_int, simkl_id
from simklcli.runtime import Runtime, default_runtime
from simklcli.targets import WatchedTarget, viewing_time
from simklcli.tracking import Tracking


def run_smoke(runtime: Runtime, reference: str, expected_account_id: int, receipt: Path) -> None:
    credential = runtime.active_credential()
    if credential is None:
        raise ValueError("The dedicated smoke account needs SIMKL_ACCESS_TOKEN.")
    api = runtime.api_client()
    account = api.get_authenticated_account(credential.access_token)
    if account.id != positive_int(expected_account_id):
        raise ValueError(
            "Access Token is not for the dedicated smoke account; nothing was changed."
        )
    if runtime.rate_gate is not None:
        runtime.rate_gate.bind_account(credential.access_token, account.id)
    catalog = Catalog(api)
    item = catalog.resolve(reference, kind=MediaKind.MOVIE)
    catalog.search(item.title, kind=MediaKind.MOVIE, year=item.year)
    service = Tracking(
        api, runtime.snapshots, account_id=account.id, access_token=credential.access_token
    )
    before = service.focused(item)
    if before is not None and before.watched and not before.last_watched_at:
        raise ValueError(
            "The reserved Movie has an unknown Viewing time; choose another reserved Movie."
        )
    # Refuse to destroy richer Rewatch state that the v1 snapshot cannot restore.
    if api.account_plan in {"pro", "vip"}:
        service.activities()
        sessions: Any = api.get_json(
            "/sync/all-items/movies",
            access_token=credential.access_token,
            params={"allow_rewatch": "yes"},
        )
        if any(
            row.get("is_rewatch") and simkl_id(row["movie"]) == item.simkl_id
            for row in sessions.get("movies", [])
        ):
            raise ValueError(
                "The reserved Movie has Rewatch sessions; choose a clean reserved Movie."
            )
    atomic_write_json(
        receipt.with_name("smoke-backup.json"),
        {
            "account_id": account.id,
            "item": item.identity(),
            "initial_state": before.payload() if before else None,
        },
    )
    passed = False
    try:
        service.read(repair=True)
        service.set_status(item, ListStatus.PLAN_TO_WATCH)
        service.mark(item, WatchedTarget(), watched_at=viewing_time(None))
        service.rating(item, 7)
        service.unmark(item, WatchedTarget())
        service.rating(item, None)
        service.remove(item)
        service.read()
        service.read(offline=True)
        passed = True
    finally:
        # Do not replay failed writes: each restoration write uses the normal read-back policy.
        service.remove(item)
        if before is not None:
            if before.watched:
                assert before.last_watched_at is not None
                service.mark(item, WatchedTarget(), watched_at=before.last_watched_at)
            service.set_status(item, before.list_status)
            if before.user_rating is not None:
                service.rating(item, before.user_rating)
        restored = service.focused(item)
        if (restored.payload() if restored else None) != (before.payload() if before else None):
            raise RuntimeError(
                "Smoke restoration was not confirmed. Use smoke-backup.json for recovery."
            )
        service.read(repair=True)
    if passed:
        atomic_write_json(
            receipt,
            {
                "passed": True,
                "restored": True,
                "version": __version__,
                "commit": os.environ.get("GITHUB_SHA"),
                "account_id": account.id,
                "simkl_id": item.simkl_id,
            },
        )


def main() -> None:
    expected = int(os.environ["SIMKL_SMOKE_ACCOUNT_ID"])
    reference = os.environ["SIMKL_SMOKE_MEDIA_REFERENCE"]
    run_smoke(default_runtime(), reference, expected, Path("smoke-receipt.json"))


if __name__ == "__main__":
    main()
