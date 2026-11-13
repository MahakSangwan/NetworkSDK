"""NX-OS commands the getters send, exactly as sent (D16).

Every command asks for JSON (D38); each one's `| json` support was checked
by hand on the bench before it was added.
"""

SHOW_VERSION = "show version | json"
SHOW_INVENTORY = "show inventory | json"
SHOW_INTERFACE = "show interface | json"
# Every VRF: plain `show ip interface` covers `default` only (D38).
SHOW_IP_INTERFACE = "show ip interface vrf all | json"
