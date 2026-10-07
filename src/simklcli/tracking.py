"""Activities-gated reconciliation and single-attempt, verified tracking writes."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import httpx

from simklcli.api import InvalidAccessTokenError, PostBlockedError, SimklClient, SimklResponseError
from simklcli.media import (
    LibraryEntry,
    ListStatus,
    Media,
    MediaKind,
    library_entries,
    positive_int,
    simkl_id,
    validate_status,
)
from simklcli.snapshots import LibrarySnapshot, LibrarySnapshotStore
from simklcli.targets import WatchedTarget


class WriteOutcomeError(SimklResponseError):
    def __init__(
        self,
        message: str,
        *,
        outcome: str = "unknown",
        details: dict[str, Any] | None = None,
        exit_code: int = 1,
    ) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.details = details or {}
        self.exit_code = exit_code


class Tracking:
    def __init__(
        self,
        api: SimklClient,
        snapshots: LibrarySnapshotStore,
        *,
        account_id: int,
        access_token: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.api = api
        self.snapshots = snapshots
        self.account_id = account_id
        self.token = access_token
        self.clock = clock

    def _get(self, path: str, params: dict[str, str | int] | None = None) -> dict[str, Any]:
        payload = self.api.get_json(path, access_token=self.token, params=params)
        if not isinstance(payload, dict):
            raise SimklResponseError("Malformed Library response; freshness cannot be established.")
        return payload

    def activities(self) -> dict[str, Any]:
        payload = self._get("/sync/activities")
        if "all" not in payload or not isinstance(payload["all"], str | type(None)):
            raise SimklResponseError(
                "Malformed activity positions; freshness cannot be established."
            )
        for kind in MediaKind:
            block = payload.get(kind.activity, {})
            if not isinstance(block, dict) or any(
                not isinstance(value, str | type(None)) for value in block.values()
            ):
                raise SimklResponseError(
                    "Malformed activity positions; freshness cannot be established."
                )
        return payload

    def _entries(
        self, path: str, params: dict[str, str | int] | None = None
    ) -> dict[int, LibraryEntry]:
        flags: dict[str, str | int] = {
            "extended": "full",
            "include_all_episodes": "yes",
            "episode_watched_at": "yes",
        }
        try:
            return library_entries(self._get(path, {**flags, **(params or {})}))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise SimklResponseError(
                "Malformed Library state; freshness cannot be established."
            ) from exc

    def read(self, *, offline: bool = False, repair: bool = False) -> LibrarySnapshot:
        with self.snapshots.lock(self.account_id):
            previous = self.snapshots.load(self.account_id)
            if offline:
                if previous is None or not previous.complete:
                    raise ValueError(
                        "No complete Library Snapshot exists for this account. Read online first."
                    )
                return previous
            current = self.activities()  # Always captured before any anchored Library fetch.
            if repair or previous is None or not previous.complete:
                entries: dict[int, LibraryEntry] = {}
                for kind in MediaKind:
                    entries.update(self._entries(f"/sync/all-items/{kind.bucket}"))
            else:
                entries = dict(previous.entries)
                if previous.dirty:
                    # A failed focused read cannot be repaired from an unchanged activity clock.
                    # Explicitly rebuild uncertain state instead of claiming it is current.
                    for kind in MediaKind:
                        fresh = self._entries(f"/sync/all-items/{kind.bucket}")
                        entries = {
                            key: value
                            for key, value in entries.items()
                            if value.item.kind is not kind
                        }
                        entries.update(fresh)
                elif current != previous.activities:
                    since = (
                        {"date_from": previous.activities["all"]}
                        if previous.activities.get("all")
                        else {}
                    )
                    for kind in MediaKind:
                        before = previous.activities.get(kind.activity, {})
                        after = current.get(kind.activity, {})
                        if any(
                            before.get(s.api_value) != after.get(s.api_value) for s in ListStatus
                        ):
                            entries.update(self._entries(f"/sync/all-items/{kind.bucket}", since))
                        if before.get("rated_at") != after.get("rated_at"):
                            entries.update(self._entries(f"/sync/ratings/{kind.bucket}", since))
                    if any(
                        previous.activities.get(k.activity, {}).get("removed_from_list")
                        != current.get(k.activity, {}).get("removed_from_list")
                        for k in MediaKind
                    ):
                        ids_payload = self._get("/sync/all-items", {"extended": "simkl_ids_only"})
                        try:
                            ids = {
                                simkl_id(
                                    row.get("movie") or row.get("show") or row.get("anime") or row
                                )
                                for k in MediaKind
                                for row in ids_payload.get(k.bucket, [])
                            }
                        except (ValueError, TypeError, AttributeError) as exc:
                            raise SimklResponseError(
                                "Malformed IDs-only Library comparison."
                            ) from exc
                        entries = {key: value for key, value in entries.items() if key in ids}
            snapshot = LibrarySnapshot(self.account_id, entries, current, True, False, self.clock())
            self.snapshots.save(snapshot)
            return snapshot

    def focused(self, item: Media) -> LibraryEntry | None:
        # A user-requested write read-back is scoped to one media kind, never initializes
        # the complete Library, and always reads the server rather than trusting local state.
        self.activities()
        return self._entries(f"/sync/all-items/{item.kind.bucket}").get(item.simkl_id)

    def _dirty(self) -> bool:
        try:
            self.snapshots.mark_dirty(self.account_id)
            snapshot = self.snapshots.load(self.account_id) or LibrarySnapshot(self.account_id)
            snapshot.dirty = True
            # The separate marker still forces reconciliation if the snapshot cannot be saved.
            with suppress(OSError):
                self.snapshots.save(snapshot)
            return True
        except OSError:
            return False

    def _save_focused(self, item: Media, entry: LibraryEntry | None) -> None:
        snapshot = self.snapshots.load(self.account_id) or LibrarySnapshot(self.account_id)
        if entry is None:
            snapshot.entries.pop(item.simkl_id, None)
        else:
            snapshot.entries[item.simkl_id] = entry
        # Keep the complete Library activity positions/age: one entry is not a full sync.
        self.snapshots.save(snapshot)

    def _send(
        self,
        path: str,
        body: dict[str, Any],
        item: Media,
        intended: Callable[[LibraryEntry | None], bool | None],
        *,
        params: dict[str, str | int] | None = None,
        rewatch: bool = False,
    ) -> bool:
        try:
            result = self.api.write_state(path, body, access_token=self.token, params=params)
            if rewatch:
                statuses = result.get("added", {}).get("statuses", [])
                if not any(
                    s.get("response", {}).get("rewatch_status") in {"active", "completed", "closed"}
                    for s in statuses
                ):
                    raise SimklResponseError("Simkl did not confirm a recorded Rewatch.")
            return False
        except (InvalidAccessTokenError, PostBlockedError):
            raise
        except (httpx.HTTPError, SimklResponseError, ValueError, TypeError) as exc:
            try:
                state = self.focused(item)
                verified = intended(state)
            except InvalidAccessTokenError:
                raise
            except (httpx.HTTPError, SimklResponseError, ValueError, OSError):
                verified = None
            if verified is True:
                return True
            if verified is False:
                raise WriteOutcomeError(
                    "Read-back confirmed the intended change is absent; rerunning is safe.",
                    outcome="not_applied",
                ) from exc
            raise WriteOutcomeError(
                "Outcome unknown. Verify with simkl library repair --json "
                "and the item's Simkl page; "
                "do not blindly retry."
            ) from exc

    def _write(
        self,
        item: Media,
        path: str,
        body: dict[str, Any],
        intended: Callable[[LibraryEntry | None], bool | None],
        *,
        params: dict[str, str | int] | None = None,
        rewatch: bool = False,
        verify: Callable[[LibraryEntry | None], None] | None = None,
    ) -> dict[str, Any]:
        with self.snapshots.lock(self.account_id):
            try:
                recovered = self._send(path, body, item, intended, params=params, rewatch=rewatch)
                try:
                    state = self.focused(item)
                    if verify is not None:
                        verify(state)
                    if (
                        not rewatch
                        and intended(state) is False
                        and (path != "/sync/add-to-list" or state is None)
                    ):
                        raise WriteOutcomeError(
                            "Read-back confirmed an incomplete change; rerunning is safe.",
                            outcome="not_applied",
                        )
                    self._save_focused(item, state)
                    return self._result(item, state, recovered=recovered)
                except InvalidAccessTokenError as exc:
                    exc.remote_success = True
                    raise
                except WriteOutcomeError:
                    raise
                except (httpx.HTTPError, SimklResponseError, ValueError, OSError):
                    saved = self._dirty()
                    return self._result(
                        item, None, recovered=recovered, dirty=True, dirty_saved=saved
                    )
            except KeyboardInterrupt as exc:
                self._dirty()
                raise WriteOutcomeError(
                    "Outcome unknown after cancellation; verify before retrying.", exit_code=130
                ) from exc
            except WriteOutcomeError:
                self._dirty()
                raise

    @staticmethod
    def _result(
        item: Media,
        state: LibraryEntry | None,
        *,
        recovered: bool = False,
        dirty: bool = False,
        dirty_saved: bool = True,
    ) -> dict[str, Any]:
        message = "Remote write succeeded."
        if recovered:
            message += " Read-back recovered the result."
        if dirty:
            message += (
                " Local state is dirty; the next online read will reconcile. "
                "Do not repeat the write."
            )
            if not dirty_saved:
                message += (
                    " The dirty marker could not be saved; run simkl library repair explicitly."
                )
        return {
            "item": item.identity(),
            "remote_success": True,
            "state": state.payload() if state else None,
            "local_state": ("dirty" if dirty_saved else "unavailable") if dirty else "reconciled",
            "recovered_by_readback": recovered,
            "message": message,
        }

    def set_status(self, item: Media, status: ListStatus) -> dict[str, Any]:
        validate_status(item.kind, status)
        return self._write(
            item,
            "/sync/add-to-list",
            {item.kind.bucket: [{"ids": {"simkl": item.simkl_id}, "to": status.api_value}]},
            lambda state: state is not None and state.list_status is status,
        )

    def remove(self, item: Media) -> dict[str, Any]:
        bucket = "movies" if item.kind is MediaKind.MOVIE else "shows"
        return self._write(
            item,
            "/sync/history/remove",
            {bucket: [{"ids": {"simkl": item.simkl_id}}]},
            lambda state: state is None,
        )

    def rating(self, item: Media, score: int | None) -> dict[str, Any]:
        if score is not None and (isinstance(score, bool) or not 1 <= score <= 10):
            raise ValueError("User Rating must be from 1 to 10.")
        payload: dict[str, Any] = {"ids": {"simkl": item.simkl_id}}
        if score is not None:
            payload["rating"] = score
        return self._write(
            item,
            "/sync/ratings" + ("/remove" if score is None else ""),
            {item.kind.bucket: [payload]},
            lambda state: (state.user_rating if state is not None else None) == score,
        )

    def mark(
        self,
        item: Media,
        target: WatchedTarget,
        *,
        watched_at: str,
        rewatch: bool = False,
        rewatch_id: int | None = None,
    ) -> dict[str, Any]:
        payload = target.item_payload(item, watched_at=watched_at)
        if rewatch:
            settings = self.api.checked_json(
                self.api._request("POST", "/users/settings", access_token=self.token)
            )
            if settings.get("account", {}).get("type") not in {"pro", "vip"}:
                raise ValueError("Rewatch tracking requires Simkl PRO or VIP.")
            payload["is_rewatch"] = True
            if rewatch_id is None:
                self.activities()
                sessions = self._get(
                    f"/sync/all-items/{item.kind.bucket}", {"allow_rewatch": "yes"}
                )
                for row in sessions.get(item.kind.bucket, []):
                    identity = row.get("movie") or row.get("show") or row.get("anime")
                    if (
                        row.get("is_rewatch")
                        and row.get("rewatch_status") == "active"
                        and isinstance(identity, dict)
                        and simkl_id(identity) == item.simkl_id
                    ):
                        rewatch_id = positive_int(row.get("rewatch_id"))
                        break
            if rewatch_id is not None:
                payload["rewatch_id"] = rewatch_id
        elif rewatch_id is not None:
            raise ValueError("--rewatch-id requires --rewatch.")
        return self._write(
            item,
            "/sync/history",
            {item.kind.bucket: [payload]},
            lambda state: None if rewatch else target.matches(state, watched=True),
            params={"allow_rewatch": "yes"} if rewatch else None,
            rewatch=rewatch,
        )

    def unmark(self, item: Media, target: WatchedTarget) -> dict[str, Any]:
        bucket = "movies" if item.kind is MediaKind.MOVIE else "shows"
        if target.coordinates or target.external_id is not None:
            before = self.focused(item)

            def preserved(state: LibraryEntry | None) -> bool | None:
                matched = target.matches(state, watched=False)
                if matched is None:
                    return None
                same = (before is None and state is None) or (
                    before is not None
                    and state is not None
                    and before.list_status is state.list_status
                    and before.user_rating == state.user_rating
                )
                return matched if same else None

            def verify_preservation(state: LibraryEntry | None) -> None:
                if (before is None and state is not None) or (
                    before is not None
                    and (
                        state is None
                        or state.list_status is not before.list_status
                        or state.user_rating != before.user_rating
                    )
                ):
                    raise WriteOutcomeError(
                        "Partial or unknown outcome: Episode unmark did not preserve Library state."
                        " "
                        "Verify with simkl library repair --json before another change.",
                        outcome="partial_or_unknown",
                        details={"captured_state": before.payload() if before else None},
                    )

            return self._write(
                item,
                "/sync/history/remove",
                {bucket: [target.item_payload(item)]},
                preserved,
                verify=verify_preservation,
            )
        with self.snapshots.lock(self.account_id):
            before = self.focused(item)
            if before is None or not before.watched:
                self._save_focused(item, before)
                return {**self._result(item, before), "message": "Watched State was already clear."}
            try:
                recovered = self._send(
                    "/sync/history/remove",
                    {bucket: [target.item_payload(item)]},
                    item,
                    lambda state: state is None,
                )
                restore = {"ids": {"simkl": item.simkl_id}, "to": before.list_status.api_value}
                recovered |= self._send(
                    "/sync/add-to-list",
                    {item.kind.bucket: [restore]},
                    item,
                    lambda state: state is not None and state.list_status is before.list_status,
                )
                if before.user_rating is not None:
                    recovered |= self._send(
                        "/sync/ratings",
                        {
                            item.kind.bucket: [
                                {"ids": {"simkl": item.simkl_id}, "rating": before.user_rating}
                            ]
                        },
                        item,
                        lambda state: state is not None and state.user_rating == before.user_rating,
                    )
                after = self.focused(item)
                if (
                    after is None
                    or after.watched
                    or after.list_status is not before.list_status
                    or after.user_rating != before.user_rating
                ):
                    raise WriteOutcomeError(
                        "Final read-back could not confirm preserved Library state."
                    )
                self._save_focused(item, after)
                return self._result(item, after, recovered=recovered)
            except (httpx.HTTPError, SimklResponseError, OSError, KeyboardInterrupt) as exc:
                self._dirty()
                if isinstance(exc, InvalidAccessTokenError):
                    raise
                raise WriteOutcomeError(
                    "Partial or unknown outcome while preserving Library state. "
                    "Verify with simkl library repair --json before making another change.",
                    outcome="partial_or_unknown",
                    details={"captured_state": before.payload()},
                    exit_code=130 if isinstance(exc, KeyboardInterrupt) else 1,
                ) from exc
