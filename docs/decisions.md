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
separate Windows machine with device access (git clone, venv, pip; no uv).

## Decisions

| # | Decision | Notes |
|---|----------|-------|
| D1 | Sync API; thread-pool helper for many devices | Not urgent: batches are 5-10 devices. |
| D2 | `requires-python >=3.12`; develop and test on 3.14 | ruff/mypy target 3.12 so newer syntax fails checks. |
| D3 | Hybrid transport ownership | Core defines the contract and ships optional base transports; each device's `open()` composes them or uses its own. |
| D4 | Raw access via separate `get_<noun>_raw()` methods | Not a `raw=True` flag. Returns a mapping of command/endpoint to raw output. |
| D5 | netmiko for SSH | Via `netsdk-core[ssh]`; REST via `netsdk-core[rest]` (httpx). REST-only packages never pull netmiko. |
| D6 | Getter accounts are strictly read-only | Pager can't be assumed disabled; the SSH transport must handle `--More--` itself. See O1. |
| D7 | 0.x versioning; core 1.0 not before FortiGate ships | Device packages pin `netsdk-core>=0.N,<0.N+1`. |
| D8 | Canonical value formats and frozen models | Formats defined in core with the first models. |
| D9 | SDK is NetBox-agnostic | Follow NetBox naming where it is free to do so; diverge whenever it isn't comfortable. No NetBox type mapping in the SDK. |
| D10 | IOS and IOS-XE are fully separate packages | IOS-XE is copied from IOS once IOS is solid, then decoupled. Duplication is tracked in `docs/duplication.md`. |
| D11 | Platform order: IOS, FortiGate, IOS-XE, NX-OS, ASA, WLC, Firepower, F5 | FortiGate second so core sees a REST transport early. |
| D12 | Transport per device decided when we get to it | RESTCONF/NETCONF assumed off; SSH or REST. |
| D13 | Sanitized device output is supplied by Mahak for fixtures | |
| D14 | No CI for now | Lint/type/test locally. |
| D15 | Name stays `netsdk` (placeholder, internal) | `netsdk`, `netsdk-core` were free on PyPI on 2026-11-06. |

## Open

- **O1. Pager handling under read-only accounts.** Can the read-only account
  run `terminal length 0` at all (it is an exec command, not config), or does
  command authorization reject it? Approach decides between "try it, fall back
  to pager handling" and "always handle the pager".
- **O2. What the read-only account can see.** Below privilege 15,
  `show running-config` only shows what that level may configure, which
  matters for `get_config()` and the drift checker.
- **O3. Auth method.** Password, SSH key, or both.
- Deferred to their SDKs: WLC AireOS vs 9800; Firepower FMC vs FDM; NX-API.
