# netsdk

Python SDKs for enterprise network devices: one package per platform, sharing
conventions through `netsdk-core`. Getters return Pydantic models, send only
the commands they document, and work with read-only accounts.

| Package | Import | Status |
|---------|--------|--------|
| `netsdk-core` | `netsdk.core` | 0.x, shared by every package |
| `netsdk-cisco-ios` | `netsdk.cisco.ios` | 0.x, live-verified ([getters](packages/cisco-ios/README.md)) |
| `netsdk-cisco-nxos` | `netsdk.cisco.nxos` | 0.x, live-verified on NX-OS 7.3, 9.3, 10.5 ([getters](packages/cisco-nxos/README.md)) |

0.x means the API may still change between releases (D7). 0.1.0 is a
reference point for the NetBox bootstrap: the getters are live-verified, the
NetBox integration is not yet.

## Requirements

- Linux (or macOS) with Python 3.12+.
- `gcc` and `make`: Cisco's Genie parser, used by the IOS package, builds a
  dependency from source on some Python versions. Genie doesn't run on Windows.
- `git`, and HTTPS access to GitHub: packages are installed from this repo,
  not from PyPI.

## Install

Each package has its own tags: `core-v0.1.0`, `cisco-ios-v0.1.0`,
`cisco-nxos-v0.1.0` (`git tag -l`). Every netsdk package you use must come
**from the same commit** (D49): pick the tag of the package you need newest
and install all of them from that tag. At that commit the other packages are
at whatever version they have there.

### uv (recommended)

In your project's `pyproject.toml`:

```toml
[project]
dependencies = ["netsdk-cisco-ios", "netsdk-cisco-nxos"]

[tool.uv.sources]
netsdk-cisco-ios = { git = "https://github.com/MahakSangwan/NetworkSDK", tag = "cisco-nxos-v0.1.0", subdirectory = "packages/cisco-ios" }
netsdk-cisco-nxos = { git = "https://github.com/MahakSangwan/NetworkSDK", tag = "cisco-nxos-v0.1.0", subdirectory = "packages/cisco-nxos" }
```

Both use the same tag on purpose: today all three tags point at the same
commit, but later they won't. Then run `uv sync`; uv takes `netsdk-core`
from that tag on its own. Using different tags for two packages fails with
"conflicting URLs for package `netsdk-core`".

### pip

List `netsdk-core` explicitly, from the same tag. Otherwise pip looks it up on
public PyPI, where anyone could publish a package under that name.

```
python3 -m venv .venv
. .venv/bin/activate
TAG=cisco-nxos-v0.1.0
REPO=git+https://github.com/MahakSangwan/NetworkSDK@$TAG
pip install \
  "netsdk-core[ssh,genie] @ $REPO#subdirectory=packages/core" \
  "netsdk-cisco-ios @ $REPO#subdirectory=packages/cisco-ios" \
  "netsdk-cisco-nxos @ $REPO#subdirectory=packages/cisco-nxos"
```

Drop the packages you don't need, but keep `netsdk-core`. In a
`requirements.txt`, write the same three lines with the tag spelled out.

## Usage

```python
from netsdk.core import Credentials
from netsdk.cisco.nxos import CiscoNXOS

creds = Credentials(username="netops-ro", password="...")
with CiscoNXOS("192.0.2.10", creds) as device:
    for iface in device.get_interfaces():
        print(iface.name, iface.enabled)
```

Each package's README lists its getters and the exact commands they send.
Every session is recorded under `session_logs/` in the working directory, and
new SSH host keys are saved to `~/.netsdk/known_hosts` (a changed key is
rejected).

## Development

```
uv sync --all-packages --all-extras
uv run ruff check . && uv run ruff format --check .
uv run mypy packages/*/src packages/*/tests
uv run pytest
```

See [CLAUDE.md](CLAUDE.md) for conventions and the release steps, and
[docs/decisions.md](docs/decisions.md) for design decisions.
