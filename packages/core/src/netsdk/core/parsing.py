"""Helpers for the parsers layer of CLI device packages.

Devices that can print JSON (NX-OS `| json`, D38) are read with
`parse_json`. Text output is parsed with Cisco's Genie by default, or with
ntc-templates (TextFSM) where Genie falls short; the decision log lists which
per command (D33). Each text engine needs its extra (`genie`, `ssh`);
importing this module works without them.
"""

import json
import logging
from pathlib import Path
from typing import Any

from netsdk.core.exceptions import ParseError

log = logging.getLogger(__name__)


def parse_json(command: str, output: str) -> dict[str, Any]:
    """Read the one JSON object a CLI printed for `command`.

    Lines the device prints before the object (NX-OS 9.3 prints
    `SB: Permission denied.` ahead of every `| json` output) are logged and
    ignored; they are never read as data. Raises ParseError when there is no
    object, it is broken or cut short, or anything follows it.
    """
    lines = output.split("\n")
    start = next((i for i, line in enumerate(lines) if line.lstrip().startswith("{")), None)
    if start is None:
        raise ParseError(f"{command!r} output has no JSON object")
    before = [line.strip() for line in lines[:start] if line.strip()]
    if before:
        log.info(
            "%r: ignoring %d line(s) printed before the JSON: %s",
            command,
            len(before),
            " | ".join(before),
        )
    text = "\n".join(lines[start:]).strip()
    try:
        data, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"{command!r} output is not valid JSON: {exc}") from exc
    if text[end:].strip():
        raise ParseError(f"{command!r} output continues after its JSON object")
    if not isinstance(data, dict):
        raise ParseError(f"{command!r} output is JSON but not an object")
    return data


def parse_textfsm(
    platform: str, command: str, output: str, *, template: Path | None = None
) -> list[dict[str, Any]]:
    """Parse `output` of `command` with the ntc-templates template for `platform`.

    `template` replaces ntc-templates' own file with a corrected copy while an
    upstream fix is unreleased (D36). Returns one dict per TextFSM record,
    keys lowercased. Raises ParseError when there is no template or the
    output matches nothing: TextFSM itself returns an empty list for output
    it doesn't recognise, which must never pass as "no data".
    """
    try:
        import textfsm
        from ntc_templates.parse import parse_output
    except ImportError as exc:
        raise ImportError("parse_textfsm() needs ntc-templates: install netsdk-core[ssh]") from exc

    try:
        if template is None:
            rows: list[dict[str, Any]] = parse_output(
                platform=platform, command=command, data=output
            )
        else:
            with template.open(encoding="utf-8") as fh:
                fsm = textfsm.TextFSM(fh)
            header = [name.lower() for name in fsm.header]
            rows = [dict(zip(header, row, strict=True)) for row in fsm.ParseText(output)]
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
