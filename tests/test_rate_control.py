from pathlib import Path
from threading import Event, Thread

import pytest

from simklcli.rate_control import CrossProcessRateGate


def test_rate_gate_shares_app_and_account_scoped_cadence(tmp_path: Path) -> None:
    now = 100.0
    sleeps: list[float] = []

    def clock() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    first = CrossProcessRateGate(
        tmp_path,
        client_id="registered-client",
        clock=clock,
        sleep=sleep,
    )
    second = CrossProcessRateGate(
        tmp_path,
        client_id="registered-client",
        clock=clock,
        sleep=sleep,
    )

    with first.limit("GET", None):
        pass
    with second.limit("GET", None):
        pass
    with first.limit("POST", "account-token"):
        pass
    with second.limit("POST", "account-token"):
        pass
    with second.limit("POST", "different-account-token"):
        pass

    assert sleeps == pytest.approx([0.1, 1.0])
    assert all("account-token" not in path.name for path in tmp_path.iterdir())


def test_rate_gate_holds_account_lock_for_the_whole_request(tmp_path: Path) -> None:
    first = CrossProcessRateGate(tmp_path, client_id="registered-client")
    second = CrossProcessRateGate(tmp_path, client_id="registered-client")
    attempted = Event()
    entered = Event()

    def enter_second_request() -> None:
        attempted.set()
        with second.limit("POST", "account-token"):
            entered.set()

    with first.limit("POST", "account-token"):
        thread = Thread(target=enter_second_request)
        thread.start()
        assert attempted.wait(timeout=1)
        assert not entered.wait(timeout=0.1)

    thread.join(timeout=2)
    assert entered.is_set()
