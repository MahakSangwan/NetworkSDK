"""Helpers for the parsers layer of CLI device packages.

CLI output is parsed with ntc-templates (TextFSM) by default (D27). Needs the
`ssh` extra; importing this module works without it.
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
