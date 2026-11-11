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
| `get_vlans()` | `show vlan brief` | Access ports only; trunk VLANs are in `get_switchports()`. `NotSupportedError` on devices without VLANs. |
| `get_switchports()` | `show interfaces switchport` | Layer-2 ports only: mode, access/native/voice VLAN, allowed VLANs (`ALL` is a flag). Order is not the device's. |
| `get_inventory()` | `show inventory` | Every item in device order: name, description, part ID, version ID, serial. |
| `get_neighbors()` | `show cdp neighbors detail`, `show lldp neighbors detail` | One record per neighbor per port per protocol; remote values as advertised. A disabled protocol adds nothing. |
| `get_vtp_status()` | `show vtp status` | VTP v1-v3, VLAN feature only; no MD5 digest. `NotSupportedError` on devices without VTP. |

Output is parsed with Cisco's Genie by default, or ntc-templates where Genie
falls short (D33 lists which per command); output that doesn't parse raises
`ParseError`, never an empty or partial model. Genie runs on Linux and macOS
only; on Python 3.14 installing it needs `gcc` and `make`.

Every session is recorded under `session_logs/`. New SSH host keys are
saved to `~/.netsdk/known_hosts`; a changed key is rejected.
