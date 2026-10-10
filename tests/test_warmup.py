import asyncio
import json
from unittest.mock import Mock

import pytest
from openspline_server import warmup


def test_timing_uses_monotonic_clock_and_stops_at_readiness(monkeypatch, capsys):
    clock = [100.0]
    monkeypatch.setattr(warmup.time, "perf_counter", lambda: clock[0])
    monkeypatch.setenv("OPENSPLINE_WARMUP_SESSION_ID", "cloud-session")
    monkeypatch.setenv("RANK", "1")
    with warmup.WarmupSpan("ready", enabled=True) as span:
        clock[0] += 1.25
        span.finish()
        clock[0] += 300  # Conversation time is not startup time.
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["status"] for event in events] == ["started", "ok"]
    assert events[-1]["duration_ms"] == 1250
    assert events[-1]["session_id"] == "cloud-session"
    assert events[-1]["rank"] == "1"


@pytest.mark.parametrize("error,status", [
    (ValueError("https://private.invalid/?token=secret"), "error"),
    (asyncio.CancelledError("private disconnect details"), "cancelled"),
])
def test_failure_logs_type_without_sensitive_message(error, status, capsys):
    with pytest.raises(type(error)):
        with warmup.WarmupSpan("load", enabled=True):
            raise error
    output = capsys.readouterr().out
    event = json.loads(output.splitlines()[-1])
    assert event["status"] == status
    assert event["error_type"] == type(error).__name__
    assert event["duration_ms"] >= 0
    assert "private" not in output and "secret" not in output


def test_disabled_logging_does_not_synchronize_gpu(monkeypatch, capsys):
    monkeypatch.delenv("OPENSPLINE_WARMUP_TIMING", raising=False)
    synchronize = Mock()
    with warmup.WarmupSpan("load", synchronize=synchronize):
        pass
    synchronize.assert_not_called()
    assert capsys.readouterr().out == ""
    with warmup.WarmupSpan("load", enabled=True, synchronize=synchronize):
        pass
    synchronize.assert_called_once_with()


def test_broken_log_pipe_does_not_prevent_cleanup(monkeypatch):
    monkeypatch.setattr(warmup.sys.stdout, "write", Mock(side_effect=BrokenPipeError))
    with warmup.WarmupSpan("load", enabled=True):
        pass
