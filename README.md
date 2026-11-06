# netsdk

Python SDKs for enterprise network devices, one independently versioned
package per platform, sharing conventions through `netsdk-core`.

| Package | Import | Status |
|---------|--------|--------|
| `netsdk-core` | `netsdk.core` | in progress |
| `netsdk-cisco-ios` | `netsdk.cisco.ios` | next |

Requires Python 3.12+.

## Development (uv)

```
uv sync --all-packages --all-extras
uv run pytest
```

## Install without uv (venv + pip)

Packages aren't published, so install the local packages together so pip
resolves `netsdk-core` from the checkout:

```
python -m venv .venv
.venv\Scripts\activate
pip install -e "packages/core[ssh]"
```

See [docs/decisions.md](docs/decisions.md) for design decisions.
