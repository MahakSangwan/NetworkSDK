# netsdk

uv workspace monorepo of independent Python SDK packages for network devices.
Decisions live in [docs/decisions.md](docs/decisions.md); read it before
changing design.

## Layout

- Root `pyproject.toml` is a virtual workspace root: tooling config only.
- One package per platform under `packages/<platform>/`, src layout.
- PEP 420 namespace packages: `src/netsdk/` and vendor dirs like
  `src/netsdk/cisco/` must **never** get an `__init__.py`.
  Imports: `netsdk.core`, `netsdk.cisco.ios`, `netsdk.fortinet.fortigate`.
- Each package sets `[tool.uv.build-backend] module-name` to its dotted name.

## Conventions

- Getters: `get_<plural_noun>()` return Pydantic models, never dicts; each has
  a matching `get_<plural_noun>_raw()`.
- No mutation in getters. Getter accounts are read-only.
- Device packages keep three layers: transport, commands/endpoints, parsers.
- Shared models in core only for truly cross-vendor concepts; vendors subclass.
- Errors derive from `netsdk.core.NetSDKError`; never shadow builtins.
- Code must run on Python 3.12 (`requires-python >=3.12`).

## Workflow

Work in small slices: agree the design, implement with unit tests on
fixtures, then Mahak live-tests on a separate Windows machine (venv + pip, no
uv) and reports back. Don't batch untested getters.

## Commands

```
uv sync --all-packages --all-extras
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/core/src packages/core/tests
uv run pytest                     # unit tests; live tests excluded
uv run --python 3.12 --isolated --all-packages pytest
```
