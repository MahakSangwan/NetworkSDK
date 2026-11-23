# netsdk-core

Shared building blocks for netsdk device packages: the base `Device`, transport
contract and optional base transports, exception hierarchy, shared models and
capability protocols.

Extras:

- `netsdk-core[ssh]`: netmiko-based `SSHTransport`
- `netsdk-core[rest]`: httpx-based `RESTTransport`
- `netsdk-core[genie]`: Cisco's Genie parsers (`parse_genie`), Linux/macOS only

Not installed on its own: device packages depend on it. Installing with pip
means listing it explicitly from the same tag as the device packages (see the
[root README](../../README.md#install)).
