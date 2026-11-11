# netsdk-cisco-ios

netsdk SDK for Cisco IOS (classic). IOS-XE lives in its own package.

```python
from netsdk.core import Credentials
from netsdk.cisco.ios import CiscoIOS

creds = Credentials(username="netops-ro", password="...")
with CiscoIOS("10.0.0.1", creds) as device:
    facts = device.get_facts()  # IOSFacts model
    raw = device.get_facts_raw()  # {"show version": "..."}
    print(device.send_command("show clock"))  # anything getters don't cover
```

| Getter | Commands | Notes |
|---|---|---|
| `get_facts()` | `show version` | Switch stacks raise `NotSupportedError` (D26). |
| `get_interfaces()` | `show interfaces` | No counters, IPs or switchport mode; those are separate getters. |
| `get_ip_addresses()` | `show ip interface` | One record per address, with VRF. IPv4 only for now; unnumbered interfaces have none. |
| `get_vlans()` | `show vlan brief` | Access ports only (trunks come later). `NotSupportedError` on devices without VLANs. |
| `get_vtp_status()` | `show vtp status` | VTP v1-v3, VLAN feature only; no MD5 digest. `NotSupportedError` on devices without VTP. |

Output is parsed with ntc-templates (D27), or with Cisco's Genie for
`show vtp status` (D32); output that doesn't parse raises `ParseError`, never
an empty or partial model. Genie runs on Linux and macOS only; on Python 3.14
installing it needs `gcc` and `make`.

Every session is recorded under `session_logs/`. New SSH host keys are
saved to `~/.netsdk/known_hosts`; a changed key is rejected.
