"""Helpers for the parsers layer of CLI device packages.

CLI output is parsed with ntc-templates (TextFSM) by default (D27), or with
Cisco's Genie where the decision log says so for a command (D32). Each engine
needs its extra (`ssh`, `genie`); importing this module works without them.
"""

from typing import Any

from netsdk.core.exceptions import ParseError


def parse_textfsm(platform: str, command: str, output: str) -> list[dict[str, Any]]:
    """Parse `output` of `command` with the ntc-templates template for `platform`.

    Returns one dict per TextFSM record, keys lowercased. Raises ParseError
    when there is no template or the output matches nothing: TextFSM itself
    returns an empty list for output it doesn't recognise, which must never
    pass as "no data".
    """
    try:
        from ntc_templates.parse import parse_output
    except ImportError as exc:
        raise ImportError("parse_textfsm() needs ntc-templates: install netsdk-core[ssh]") from exc

    try:
        rows: list[dict[str, Any]] = parse_output(platform=platform, command=command, data=output)
    except Exception as exc:  # no template, or the template hit an Error state
        raise ParseError(f"could not parse {command!r} output: {exc}") from exc
    if not rows:
        raise ParseError(f"{command!r} output did not match the {platform} template")
    return rows


def parse_genie(os: str, command: str, output: str) -> dict[str, Any]:
    """Parse `output` of `command` with Genie's parser for `os` ("ios", "iosxe", ...).

    Only parses text already collected: nothing connects or sends anything.
    Returns Genie's structure as plain dicts. Raises ParseError when Genie has
    no parser, the output fails Genie's schema (empty, unrecognised or
    truncated output) or the parser crashes on it.
    """
    try:
        from genie.conf.base import Device as GenieDevice
    except ImportError as exc:
        raise ImportError("parse_genie() needs Genie: install netsdk-core[genie]") from exc

    try:
        # parse() checks the result against the parser's schema; a bare
        # parser's cli() would return {} for output it doesn't recognise.
        result = GenieDevice("netsdk", os=os).parse(command, output=output)
    except Exception as exc:  # no parser, schema mismatch, or a crash inside Genie
        reason = f"{type(exc).__name__}: {exc}"
        raise ParseError(f"could not parse {command!r} output: {reason}") from exc
    if not result:
        raise ParseError(f"{command!r} output did not match Genie's {os} parser")
    plain: dict[str, Any] = _plain(result)
    return plain


def _plain(value: Any) -> Any:
    # Genie returns its own dict subclass; hand callers ordinary containers.
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value
