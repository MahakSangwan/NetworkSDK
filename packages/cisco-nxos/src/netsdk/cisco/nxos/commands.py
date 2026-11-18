"""NX-OS commands the getters send, exactly as sent (D16).

Every command asks for JSON (D38); each one's `| json` support was checked
by hand on the bench before it was added.
"""

SHOW_VERSION = "show version | json"
SHOW_INVENTORY = "show inventory | json"
SHOW_INTERFACE = "show interface | json"
# Every VRF: plain `show ip interface` covers `default` only (D38).
SHOW_IP_INTERFACE = "show ip interface vrf all | json"
SHOW_VLAN_BRIEF = "show vlan brief | json"
SHOW_INTERFACE_SWITCHPORT = "show interface switchport | json"
SHOW_INTERFACE_TRUNK = "show interface trunk | json"
SHOW_VTP_STATUS = "show vtp status | json"
