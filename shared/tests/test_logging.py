"""Tests for shared.logging.

The engines log the record of what they did to someone else's infrastructure,
and VLE is meant to ingest those logs later. Two properties matter enough to
pin: the format switch actually switches, and re-calling configure() does not
quietly duplicate every line.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from shared import logging as vlog


@pytest.fixture(autouse=True)
def restore_root_handlers():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


class NonTTY(io.StringIO):
    def isatty(self):
        return False


class TTY(io.StringIO):
    def isatty(self):
        return True


def test_json_format_emits_parseable_lines():
    stream = NonTTY()
    vlog.configure(fmt="json", stream=stream)
    logging.getLogger("velocitee.test").info("provisioned")

    record = json.loads(stream.getvalue().strip())
    assert record["event"] == "provisioned"
    assert record["logger"] == "velocitee.test"
    assert record["level"] == "info"
    assert "timestamp" in record


def test_console_format_is_not_json():
    stream = TTY()
    vlog.configure(fmt="console", stream=stream)
    logging.getLogger("velocitee.test").info("provisioned")
    out = stream.getvalue()
    assert "provisioned" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out.strip())


def test_format_defaults_to_json_off_a_tty():
    stream = NonTTY()
    vlog.configure(stream=stream)
    logging.getLogger("x").info("hello")
    assert json.loads(stream.getvalue().strip())["event"] == "hello"


def test_env_var_overrides_the_format_default(monkeypatch):
    monkeypatch.setenv("VELOCITEE_LOG_FORMAT", "json")
    stream = TTY()  # would otherwise choose console
    vlog.configure(stream=stream)
    logging.getLogger("x").info("hello")
    assert json.loads(stream.getvalue().strip())["event"] == "hello"


def test_an_unrecognised_format_env_var_falls_back_to_the_tty_default(monkeypatch):
    monkeypatch.setenv("VELOCITEE_LOG_FORMAT", "yaml")
    stream = NonTTY()
    vlog.configure(stream=stream)
    assert json.loads(stream.getvalue() or "{}") == {}


def test_explicit_argument_beats_the_env_var(monkeypatch):
    monkeypatch.setenv("VELOCITEE_LOG_FORMAT", "json")
    stream = TTY()
    vlog.configure(fmt="console", stream=stream)
    logging.getLogger("x").info("hello")
    with pytest.raises(json.JSONDecodeError):
        json.loads(stream.getvalue().strip())


def test_level_accepts_a_string():
    stream = NonTTY()
    vlog.configure(level="DEBUG", fmt="json", stream=stream)
    logging.getLogger("x").debug("visible")
    assert "visible" in stream.getvalue()


def test_an_unparseable_level_string_falls_back_to_info():
    stream = NonTTY()
    vlog.configure(level="LOUD", fmt="json", stream=stream)
    logging.getLogger("x").debug("hidden")
    logging.getLogger("x").info("shown")
    assert "hidden" not in stream.getvalue()
    assert "shown" in stream.getvalue()


def test_env_level_overrides_the_argument(monkeypatch):
    monkeypatch.setenv("VELOCITEE_LOG_LEVEL", "ERROR")
    stream = NonTTY()
    vlog.configure(level=logging.DEBUG, fmt="json", stream=stream)
    logging.getLogger("x").warning("suppressed")
    logging.getLogger("x").error("emitted")
    assert "suppressed" not in stream.getvalue()
    assert "emitted" in stream.getvalue()


def test_reconfiguring_does_not_duplicate_output():
    # configure() is documented as safe to re-call. If it stacked handlers,
    # every engine that configures twice would double every log line and the
    # duplication would only show up in production.
    stream = NonTTY()
    vlog.configure(fmt="json", stream=stream)
    vlog.configure(fmt="json", stream=stream)
    vlog.configure(fmt="json", stream=stream)
    logging.getLogger("x").info("once")
    assert stream.getvalue().count('"once"') == 1


def test_extra_fields_from_stdlib_calls_reach_the_output():
    stream = NonTTY()
    vlog.configure(fmt="json", stream=stream)
    logging.getLogger("x").info("deployed", extra={"vmid": 100})
    assert json.loads(stream.getvalue().strip())["vmid"] == 100


def test_get_logger_returns_a_bindable_logger():
    stream = NonTTY()
    vlog.configure(fmt="json", stream=stream)
    log = vlog.get_logger("velocitee.bound").bind(run_id="abc123")
    log.info("step complete")
    record = json.loads(stream.getvalue().strip())
    assert record["run_id"] == "abc123"
    assert record["event"] == "step complete"


def test_get_logger_without_a_name_still_works():
    assert vlog.get_logger() is not None


@pytest.mark.parametrize("stream", [object(), None])
def test_isatty_is_false_for_streams_that_cannot_answer(stream):
    assert vlog._isatty(stream) is False


def test_isatty_handles_a_closed_stream():
    s = io.StringIO()
    s.close()
    assert vlog._isatty(s) is False
