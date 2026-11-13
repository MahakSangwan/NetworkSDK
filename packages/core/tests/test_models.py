import logging
from datetime import timedelta

import pytest
from pydantic import ValidationError

from netsdk.core import Facts, NotSupportedError, ParseError, SupportsFacts, Switchport
from netsdk.core.parsing import parse_genie, parse_json, parse_textfsm

FACTS = {
    "hostname": "r1",
    "vendor": "Cisco",
    "model": "ISR4331/K9",
    "serial_number": "FDO1234X0AB",
    "os_version": "15.2(7)E4",
}


def test_facts_are_frozen() -> None:
    facts = Facts(**FACTS, uptime=timedelta(minutes=5))
    with pytest.raises(ValidationError):
        facts.hostname = "r2"  # type: ignore[misc]


def test_facts_reject_unknown_and_empty_fields() -> None:
    with pytest.raises(ValidationError):
        Facts(**FACTS, colour="red")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        Facts(**{**FACTS, "serial_number": ""})


def test_uptime_is_optional() -> None:
    assert Facts(**FACTS).uptime is None


def test_supports_facts_protocol() -> None:
    class WithFacts:
        def get_facts(self) -> Facts:
            return Facts(**FACTS)

        def get_facts_raw(self) -> dict[str, str]:
            return {}

    assert isinstance(WithFacts(), SupportsFacts)
    assert not isinstance(object(), SupportsFacts)


# -- parse_textfsm ---------------------------------------------------------


def test_parse_textfsm_returns_records() -> None:
    rows = parse_textfsm("cisco_ios", "show clock", "*12:34:56.789 UTC Mon Nov 9 2026\n")
    assert rows[0]["year"] == "2026"


def test_unrecognised_output_is_a_parse_error_not_empty_data() -> None:
    with pytest.raises(ParseError, match="did not match"):
        parse_textfsm("cisco_ios", "show version", "% Invalid input detected\n")


def test_command_without_template_is_a_parse_error() -> None:
    with pytest.raises(ParseError) as excinfo:
        parse_textfsm("cisco_ios", "show netsdk-no-such-command", "x\n")
    assert not isinstance(excinfo.value, NotSupportedError)


# -- parse_genie -----------------------------------------------------------

GENIE_CLOCK = "*12:34:56.789 UTC Mon Nov 9 2026\n"


def test_parse_genie_returns_plain_dicts() -> None:
    result = parse_genie("ios", "show clock", GENIE_CLOCK)
    assert type(result) is dict
    assert result["year"] == "2026"


@pytest.mark.parametrize(
    ("command", "output"),
    [
        pytest.param("show clock", "% Invalid input detected\n", id="unrecognised"),
        pytest.param("show clock", "", id="empty"),
        pytest.param("show netsdk-no-such-command", "x\n", id="no-parser"),
    ],
)
def test_parse_genie_failures_are_parse_errors(command: str, output: str) -> None:
    with pytest.raises(ParseError) as excinfo:
        parse_genie("ios", command, output)
    assert not isinstance(excinfo.value, NotSupportedError)


# -- parse_json -------------------------------------------------------------


@pytest.mark.parametrize(
    "output",
    [
        pytest.param('{"a": 1, "b": {"c": [1, 2]}}', id="one-line"),
        pytest.param('{\n  "a": 1,\n  "b": {\n    "c": [1, 2]\n  }\n}\n', id="indented"),
        pytest.param('\n  {"a": 1, "b": {"c": [1, 2]}}  \n\n', id="blank-lines-around"),
    ],
)
def test_parse_json(output: str) -> None:
    assert parse_json("show x | json", output) == {"a": 1, "b": {"c": [1, 2]}}


def test_parse_json_ignores_and_logs_lines_before_the_object(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # As NX-OS 9.3 prints them ahead of every `| json` output.
    output = (
        "SB: Permission denied.\nSB: Permission denied.\nSB: Permission denied.\n"
        "drbg_instantiate failed\n"
        '{"host_name": "sw1"}'
    )
    with caplog.at_level(logging.INFO, logger="netsdk.core.parsing"):
        assert parse_json("show version | json", output) == {"host_name": "sw1"}
    assert "ignoring 4 line(s)" in caplog.text
    assert "drbg_instantiate failed" in caplog.text


@pytest.mark.parametrize(
    "output",
    [
        pytest.param("", id="empty"),
        pytest.param("% Invalid command at '^' marker.", id="error-text"),
        pytest.param('{"a": [1, 2', id="cut-short"),
        pytest.param('{"a": 1}\n{"b": 2}', id="two-objects"),
        pytest.param('{"a": 1}\ntrailing text', id="text-after"),
        pytest.param('[{"a": 1}]', id="not-an-object"),
        pytest.param('{"a": 1\n --More-- \n, "b": 2}', id="pager-left-inside"),
    ],
)
def test_parse_json_failures_are_parse_errors(output: str) -> None:
    with pytest.raises(ParseError):
        parse_json("show x | json", output)


# -- Switchport ------------------------------------------------------------


def test_switchport_allowed_vlans_states() -> None:
    assert Switchport(interface="Gi1", mode="trunk", all_vlans_allowed=True).allowed_vlans is None
    assert Switchport(interface="Gi1", mode="trunk", allowed_vlans=()).allowed_vlans == ()
    assert Switchport(interface="Gi1", mode=None).allowed_vlans is None  # not printed


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"all_vlans_allowed": True, "allowed_vlans": (10,)}, id="all-and-list"),
        pytest.param({"allowed_vlans": (20, 10)}, id="unsorted"),
        pytest.param({"allowed_vlans": (10, 10)}, id="duplicate"),
        pytest.param({"allowed_vlans": (0,)}, id="vlan-0"),
        pytest.param({"allowed_vlans": (4095,)}, id="vlan-4095"),
        pytest.param({"access_vlan": 0}, id="access-0"),
    ],
)
def test_switchport_rejects(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Switchport(interface="Gi1", mode="trunk", **fields)
