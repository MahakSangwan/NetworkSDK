# Decision log

Agreed design decisions for netsdk. Add an entry when a decision is made or
changed; don't rewrite history, supersede it.

## Context

Consumers, in order of arrival:

1. **NetBox bootstrap script**: collects data from a few hundred devices
   (run in batches of 5-10 while results are inspected), correlates it and
   populates NetBox. This drives getter priorities.
2. **Config drift checker**: NetBox vs. actual device config.
3. **Ansible on Red Hat AAP**: pushes config (setters phase). The AAP team owns
   their execution environment; we only state the Python minimum.

Packages are internal only and not published. Live tests are manual, on a
separate RHEL 9 machine with device access (git clone, uv); until 2026-11-10
that was a Windows machine using venv and pip. Development is in RHEL 9 under
WSL2.

## Decisions

| # | Decision | Notes |
|---|----------|-------|
| D1 | Sync API; thread-pool helper for many devices | Not urgent: batches are 5-10 devices. |
| D2 | `requires-python >=3.12`; develop and test on 3.14 | ruff/mypy target 3.12 so newer syntax fails checks. |
| D3 | Hybrid transport ownership | Core defines the contract and ships optional base transports; each device's `open()` composes them or uses its own. |
| D4 | Raw access via separate `get_<noun>_raw()` methods | Not a `raw=True` flag. Returns a mapping of command/endpoint to raw output. |
| D5 | netmiko for SSH | Via `netsdk-core[ssh]`; REST via `netsdk-core[rest]` (httpx). REST-only packages never pull netmiko. |
| D6 | Getter accounts are strictly read-only; we handle the pager ourselves | `terminal length 0` is rejected by AAA command authorization (verified). The SSH transport pages through `--More--` itself. |
| D7 | 0.x versioning; core 1.0 not before FortiGate ships | Device packages pin `netsdk-core>=0.N,<0.N+1`. |
| D8 | Canonical value formats and frozen models | Formats defined in core with the first models. |
| D9 | SDK is NetBox-agnostic | Follow NetBox naming where it is free to do so; diverge whenever it isn't comfortable. No NetBox type mapping in the SDK. |
| D10 | IOS and IOS-XE are fully separate packages | IOS-XE is copied from IOS once IOS is solid, then decoupled. Duplication is tracked in `docs/duplication.md`. |
| D11 | Platform order: IOS, FortiGate, IOS-XE, NX-OS, ASA, WLC, Firepower, F5 | FortiGate second so core sees a REST transport early. |
| D12 | Transport per device decided when we get to it | RESTCONF/NETCONF assumed off; SSH or REST. |
| D13 | Sanitized device output is supplied by Mahak for fixtures | |
| D14 | No CI for now | Lint/type/test locally. |
| D15 | Name stays `netsdk` (placeholder, internal) | `netsdk`, `netsdk-core` were free on PyPI on 2026-11-06. |
| D16 | The SDK sends only commands the caller explicitly asked for | No implicit `terminal length/width`, autodetect, NUL keepalives or extra Enters. The only session-management writes are those in D21. Library defaults that send anything are overridden and covered by tests. |
| D17 | Session logs are mandatory for every device | Full session recorded to a file, secrets redacted. Applies to REST devices too (request/response log). |
| D18 | Auth: password and SSH key both supported | |
| D19 | `send_command()` is public | Documented raw escape hatch alongside getters. |
| D20 | Privilege is controlled by AAA | Assume any `show` command is permitted; no `enable` handling for getters. |
| D21 | SSH session lifecycle | After login, read the prompt passively; send one bare Enter only if none arrives. On close, send `exit` and wait for the device to drop the channel. |
| D22 | SSH host keys: accept and save new, reject changed | Saved to `~/.netsdk/known_hosts` (overridable); `strict_host_keys=True` rejects unknown keys too. No agent, no `~/.ssh` key search: only the credentials given are tried. |
| D23 | Session logs are byte-exact raw captures | Written in binary with netmiko line-ending normalization off, so a sanitized log can become a fixture. `*.raw` fixtures are `-text` in `.gitattributes`. Default dir `session_logs/` (gitignored). |
| D24 | Test helpers ship in core (`netsdk.core.testing`) | Fake CLI for transport/getter unit tests; contract tests will live there too. |
| D25 | Paging is handled, never side-stepped | The SSH PTY size sets the IOS terminal width/length (verified: 511x1000, netmiko's default, which we keep). A height of 0 would disable the pager like `terminal length 0`, so sizes below 1 are rejected. `terminal_height` is configurable; live tests use 24 to force the pager. |
| D26 | No switch stacks: facts model a single chassis | None exist in the environment (2026-11-09). If a parser sees more than one stack member it raises `NotSupportedError` rather than return half the truth. Revisit if stacks appear. |
| D27 | CLI parsing uses ntc-templates (TextFSM) by default, chosen per command | Pure Python, Apache-2.0, works on Windows; already pulled in by netmiko, but declared explicitly by packages that use it. Lives in the parsers layer only: getters map results into our models. Our own parser where a template falls short (fix upstream when we can). A template that matches nothing raises `ParseError`. Genie/pyATS rejected for now: no Windows wheels and heavy; revisit per SDK (IOS-XE, NX-OS). |
| D28 | Facts values are reported, not derived | `uptime` is the device's own figure as a `timedelta` (not a boot time from clocks); `os_version` is the string exactly as printed. `CiscoIOS` does not refuse IOS-XE devices: callers who don't care which SDK they use aren't blocked. |
| D29 | Canonical formats (fills in D8) | Defined in `netsdk.core.formats` and enforced by the models: MAC `aa:bb:cc:dd:ee:ff`; speed and bandwidth integer kbit/s (NetBox's unit); duplex `full`/`half`/`auto`; MTU integer. Interface names are full names as the device prints them, never abbreviated. Admin and oper state are separate booleans (`enabled`, `oper_up`); the raw state strings stay on the vendor model. Unknown spellings of optional values are logged and become None, not fatal. |
| D30 | IP addresses: one record per address | `IPAddress(interface, address, secondary, vrf)`; `address` is an `ipaddress` interface (address + prefix), IPv4 or IPv6 so IPv6 can be added without a model change. `vrf=None` is the global table. Unnumbered interfaces get no record: the address belongs to the interface it is configured on. A missing prefix is a ParseError, never assumed /32. VRFs are in use (2026-11-10); IPv6 is not, so getters collect IPv4 only for now. |
| D31 | VLANs and interface-name expansion | `VLAN(vlan_id, name, active, interfaces)`; `active` only for status exactly `active` (shut, suspended and the legacy 1002-1005 `act/unsup` are not); raw status on the vendor model. Ports are access ports only; trunk membership is a separate getter. Abbreviated names (`Gi1/0/1`) are expanded with netutils' `canonical_interface_name`, with per-OS overrides where its canonical spelling differs from the OS (IOS `Vlan`, not `VLAN`); unknown prefixes are logged and kept as printed. A device without VLAN support raises `NotSupportedError`, not an empty list. |

## Open

- Deferred to their SDKs: WLC AireOS vs 9800; Firepower FMC vs FDM; NX-API.
