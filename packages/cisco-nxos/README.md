# netsdk-cisco-nxos

netsdk SDK for Cisco Nexus switches running standalone NX-OS. ACI-mode
switches are not supported (D38).

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

Commands are sent with `| json` and read as JSON (D38): no text parsing.
Lines NX-OS prints ahead of the JSON (`SB: Permission denied.` on 9.3) are
logged at INFO and ignored. Output that isn't one complete JSON object
raises `ParseError`, never an empty or partial model.

Every session is recorded under `session_logs/`. New SSH host keys are
saved to `~/.netsdk/known_hosts`; a changed key is rejected.
