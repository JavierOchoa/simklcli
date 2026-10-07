"""Authored Simkl-like wire responses for offline integration tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx

from simklcli.media import MediaKind
from simklcli.runtime import Runtime
from simklcli.storage import Account, CredentialSource


class SimklFixture:
    def __init__(self, root: Path) -> None:
        self.catalog: dict[int, dict[str, Any]] = {
            1: {"title": "Movie", "year": 1999, "ids": {"simkl": 1}, "overview": "Catalog only"},
            2: {"title": "Show", "year": 2001, "ids": {"simkl": 2}},
            3: {
                "title": "Anime",
                "year": 1998,
                "ids": {"simkl_id": 3},
                "anime_type": "tv",
                "ep_count": 3,
            },
            4: {"title": "Anime Movie", "year": 2002, "ids": {"simkl": 4}, "anime_type": "movie"},
        }
        self.kinds = {1: MediaKind.MOVIE, 2: MediaKind.SHOW, 3: MediaKind.ANIME, 4: MediaKind.ANIME}
        self.library: dict[int, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []
        self.revision = 1
        self.positions: dict[str, Any] = {"all": self.timestamp}
        for kind in MediaKind:
            self.positions[kind.activity] = {
                "all": self.timestamp,
                "rated_at": self.timestamp,
                "removed_from_list": self.timestamp,
                **{
                    s: self.timestamp
                    for s in ["watching", "plantowatch", "hold", "dropped", "completed"]
                },
            }
        self.fail_post: str | None = None
        self.fail_reads = False
        self.fail_after_post = False
        self.overrides: dict[str, object] = {}
        self.plan = "pro"
        self.rewatch_confirm = True
        self.runtime = Runtime.for_testing(
            config_dir=root / "config",
            data_dir=root / "data",
            transport=httpx.MockTransport(self.handle),
            sleep=lambda _: None,
        )
        self.runtime.credentials.write(
            access_token="test-token", account=Account(100, "Fixture"), source=CredentialSource.FILE
        )

    @property
    def timestamp(self) -> str:
        return f"2026-08-09T00:00:{self.revision:02d}Z"

    def put(
        self,
        media_id: int,
        *,
        status: str = "plantowatch",
        rating: int | None = None,
        watched: bool = False,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "status": status,
            "user_rating": rating,
            "last_watched_at": self.timestamp if watched else None,
            "movie" if self.kinds[media_id] is MediaKind.MOVIE else "show": deepcopy(
                self.catalog[media_id]
            ),
            "seasons": [],
            "watched_episodes_count": 0,
        }
        if self.kinds[media_id] is MediaKind.ANIME:
            row["anime_type"] = self.catalog[media_id]["anime_type"]
        self.library[media_id] = row
        return row

    def moved(self, media_id: int, field: str) -> None:
        self.revision += 1
        self.positions["all"] = self.timestamp
        block = self.positions[self.kinds[media_id].activity]
        block["all"] = block[field] = self.timestamp
        if media_id in self.library:
            self.library[media_id]["changed_at"] = self.timestamp

    def posts(self) -> list[httpx.Request]:
        return [
            request
            for request in self.requests
            if request.method == "POST" and request.url.path.startswith("/sync/")
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        assert request.url.params["app-name"] == "simklcli"
        assert request.headers["user-agent"].startswith("simklcli/")
        if path in self.overrides:
            return httpx.Response(200, json=self.overrides[path])
        if path == "/users/settings":
            return httpx.Response(
                200, json={"account": {"id": 100, "type": self.plan}, "user": {"name": "Fixture"}}
            )
        if path == "/redirect":
            media_id = int(request.url.params.get("simkl", "1"))
            kind = self.kinds[media_id]
            return httpx.Response(
                301, headers={"Location": f"https://simkl.com/{kind.endpoint}/{media_id}/slug"}
            )
        if path.startswith("/search/"):
            kind = {"movie": MediaKind.MOVIE, "tv": MediaKind.SHOW, "anime": MediaKind.ANIME}[
                path.split("/")[-1]
            ]
            return httpx.Response(
                200,
                json=[
                    record
                    for media_id, record in self.catalog.items()
                    if self.kinds[media_id] is kind
                ],
            )
        if "/episodes/" in path:
            media_id = int(path.split("/")[-1])
            return httpx.Response(
                200,
                json=[
                    {
                        "episode": n,
                        "season": 1,
                        "aired": n < 3,
                        "ids": {"simkl_id": media_id * 10 + n},
                    }
                    for n in range(1, 4)
                ],
            )
        if path.split("/")[1] in {"movies", "tv", "anime"}:
            return httpx.Response(200, json=self.catalog.get(int(path.split("/")[-1]), []))
        assert request.headers["authorization"] == "Bearer test-token"
        if request.method == "GET":
            if self.fail_reads:
                return httpx.Response(403, json={"error": "forbidden"})
            if path == "/sync/activities":
                return httpx.Response(200, json=deepcopy(self.positions))
            response: dict[str, Any] = {}
            selected = path.split("/")[3:]
            for media_id, library_row in self.library.items():
                kind = self.kinds[media_id]
                if selected and selected[0] != kind.bucket:
                    continue
                since = request.url.params.get("date_from")
                if since and library_row.get("changed_at", "") <= since:
                    continue
                value = deepcopy(library_row)
                if request.url.params.get("extended") == "simkl_ids_only":
                    value = {"ids": {"simkl": media_id}}
                response.setdefault(kind.bucket, []).append(value)
            return httpx.Response(200, json=response)
        failure = self.fail_post
        self.fail_post = None
        if failure == "blocked":
            return httpx.Response(429, json={"error": "rate_limit"})
        if failure == "lost_absent":
            raise httpx.ReadTimeout("Lost response", request=request)
        if failure == "partial":
            return httpx.Response(
                201, json={"added": {"movies": 0}, "not_found": {"movies": [{"ids": {"simkl": 1}}]}}
            )
        body = json.loads(request.content)
        payload = next(iter(body.values()))[0]
        media_id = payload["ids"]["simkl"]
        row = self.library.get(media_id)
        if path == "/sync/history/remove" and not (
            payload.get("seasons") or payload.get("episodes")
        ):
            self.library.pop(media_id, None)
            self.moved(media_id, "removed_from_list")
        else:
            row = row if row is not None else self.put(media_id)
            if path == "/sync/add-to-list":
                row["status"] = payload["to"]
            elif path.startswith("/sync/ratings"):
                row["user_rating"] = payload.get("rating")
            elif path == "/sync/history":
                row["last_watched_at"] = payload.get("watched_at", self.timestamp)
                row["status"] = (
                    "completed" if self.kinds[media_id] is MediaKind.MOVIE else "watching"
                )
                row["seasons"] = payload.get("seasons", [])
                row["watched_episodes_count"] = sum(len(s["episodes"]) for s in row["seasons"])
            elif path == "/sync/history/remove":
                row["seasons"] = []
                row["watched_episodes_count"] = 0
                row["last_watched_at"] = None
            self.moved(media_id, "rated_at" if "/ratings" in path else row["status"])
        if self.fail_after_post:
            self.fail_reads = True
        if failure == "lost_applied":
            raise httpx.ReadTimeout("Lost response", request=request)
        response = {
            "deleted" if path.endswith("/remove") else "added": {"movies": 1, "shows": 1},
            "not_found": {"movies": [], "shows": []},
        }
        if request.url.params.get("allow_rewatch") == "yes" and self.rewatch_confirm:
            response["added"]["statuses"] = [
                {"response": {"rewatch_id": 7, "rewatch_status": "active"}}
            ]
        return httpx.Response(201, json=response)
