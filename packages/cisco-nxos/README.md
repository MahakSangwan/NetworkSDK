# netsdk-cisco-nxos

netsdk SDK for Cisco Nexus switches running standalone NX-OS. ACI-mode
switches are not supported (D38).

Install from a release tag together with `netsdk-core`: see the
[root README](../../README.md#install).

```python
from netsdk.core import Credentials
from netsdk.cisco.nxos import CiscoNXOS

creds = Credentials(username="netops-ro", password="...")
with CiscoNXOS("10.0.0.1", creds) as device:
    facts = device.get_facts()  # NXOSFacts model
    raw = device.get_facts_raw()  # {"show version | json": "...", ...}
    print(device.send_command("show clock"))  # anything getters don't cover
```

| Getter | Commands | Notes |
|---|---|---|
| `get_facts()` | `show version \| json`, `show inventory \| json` | Model and serial are the chassis entry of `show inventory`. |
| `get_interfaces()` | `show interface \| json` | Every interface incl. SVIs, port-channels, `mgmt0` and FEX ports; no counters or IPs. |
| `get_ip_addresses()` | `show ip interface vrf all \| json` | One record per IPv4 address (primary, then secondaries) with its VRF; `default` is None. Unnumbered interfaces have none. |
| `get_vlans()` | `show vlan brief \| json`, `show interface switchport \| json` | Access ports only, taken from `show interface switchport`: NX-OS's own VLAN port lists include trunks and are damaged on 7.3 (D41). |
| `get_switchports()` | `show interface switchport \| json` | Layer-2 ports: mode, access/native VLAN, allowed VLANs; `1-4094` is `all_vlans_allowed`. |
| `get_vtp_status()` | `show vtp status \| json` | Version, mode, domain, revision, pruning, traps, MD5 digest. `NotSupportedError` when VTP is off. |
| `get_neighbors()` | `show cdp neighbors detail \| json`, `show lldp neighbors detail \| json` | One record per neighbor per local port per protocol; remote values as advertised. A protocol that is off adds nothing. |
| `get_inventory()` | `show inventory \| json` | Every item in device order (chassis, modules, FEX, PSUs, fans); the quotes 10.5 adds are dropped. |
| `get_port_channels()` | `show port-channel summary \| json` | Core `LAG` model: state, LACP or static, each member's bundled state; group, layer and raw flags as extras. |
| `get_vpc_status()` | `show vpc \| json` | Domain, role, peer-link, each vPC's port-channel; peer, keepalive, consistency and up/down as health flags. `NotSupportedError` when vPC is off. |
| `get_fexes()` | `show fex detail \| json` | Each attached FEX: ID, model, serial, fabric port-channel and ports, host ports, `online`. A FEX the switch no longer sees isn't listed. `NotSupportedError` when FEX is off. |

Commands are sent with `| json` and read as JSON (D38): no text parsing.
Sessions use a 255-column terminal: on a wider one NX-OS 7.3 cuts lines at
255 characters and drops one (D42). Any string still broken across lines
other than at the terminal width is never repaired: reading it raises
`ParseError`.
Lines NX-OS prints ahead of the JSON (`SB: Permission denied.` on 9.3) are
logged at INFO and ignored. Output that isn't one complete JSON object
raises `ParseError`, never an empty or partial model.

Every session is recorded under `session_logs/`. New SSH host keys are
saved to `~/.netsdk/known_hosts`; a changed key is rejected.
