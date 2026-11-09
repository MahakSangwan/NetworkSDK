# netsdk-cisco-ios

netsdk SDK for Cisco IOS (classic). IOS-XE lives in its own package.

```python
from netsdk.core import Credentials
from netsdk.cisco.ios import CiscoIOS

creds = Credentials(username="netops-ro", password="...")
with CiscoIOS("10.0.0.1", creds) as device:
    print(device.send_command("show version"))
```

Every session is recorded under `session_logs/`. New SSH host keys are
saved to `~/.netsdk/known_hosts`; a changed key is rejected.
