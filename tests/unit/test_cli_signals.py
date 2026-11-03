"""The first Ctrl-C/SIGTERM of `factory run`/`resume` pauses at the next safe point; the second ends the agents now."""

from __future__ import annotations

import os
import signal

import pytest

from agent_factory import cli


class Factory:
    def __init__(self):
        self.stop_requested = None
        self.stopped: list = []

    def stop_agents_now(self, job_ids):
        self.stopped.append(job_ids)
        return 2


@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_first_signal_asks_for_a_pause_and_the_second_stops_now(monkeypatch, sig):
    factory, exits = Factory(), []
    monkeypatch.setattr(cli.os, "_exit", exits.append)
    before = signal.getsignal(sig)
    with cli._stop_on_signal(factory, ["job-1"]):
        os.kill(os.getpid(), sig)
        assert factory.stop_requested == f"signal {sig.name}" and not factory.stopped
        os.kill(os.getpid(), sig)
        assert factory.stopped == [["job-1"]] and exits == [130]
    assert signal.getsignal(sig) == before  # the previous handler is back
