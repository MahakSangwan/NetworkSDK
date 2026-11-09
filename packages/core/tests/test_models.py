from datetime import timedelta

import pytest
from pydantic import ValidationError

from netsdk.core import Facts, NotSupportedError, ParseError, SupportsFacts
from netsdk.core.parsing import parse_textfsm

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
