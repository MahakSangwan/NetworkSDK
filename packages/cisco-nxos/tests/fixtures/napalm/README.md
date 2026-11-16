# Upstream NX-OS JSON samples

Copied unchanged from NAPALM (<https://github.com/napalm-automation/napalm>,
commit `820a06b2069eb1d7b0cbe8943ee2dea6e2949d1a`), under the Apache License
2.0 in [LICENSE](LICENSE). Renamed from `test/<driver>/mocked_data/<test>/<case>/<file>`:

| File | From |
|---|---|
| `nxapi_normal_show_version.json` | `nxos/.../normal/show_version.json` (NX-OSv 7.3(1)D1(1)) |
| `nxapi_normal_show_inventory.json` | `nxos/.../normal/show_inventory.json` |
| `ssh_N93180_show_inventory_json.txt` | `nxos_ssh/.../N93180/show_inventory___json.txt` (chassis name printed with its quotes) |
| `ssh_does_not_support_json_show_inventory_json.txt` | `nxos_ssh/.../does_not_support_json/show_inventory___json.txt` |
| `nxapi_normal_show_interface.json` | `nxos/.../test_get_interfaces/normal/show_interface.json` |
| `nxapi_vlan_interface_show_interface.json` | `nxos/.../test_get_interfaces/vlan_interface/show_interface.json` |
| `nxapi_admin_up_oper_down_show_interface.json` | `nxos/.../test_get_interfaces/admin_up_oper_down/show_interface.json` |
| `ssh_Nexus3548_show_interface_json.txt` | `nxos_ssh/.../test_get_interfaces_counters/Nexus3548/show_interface___json.txt` |
| `nxapi_ipv6_n9k_show_ip_interface.json` | `nxos/.../test_get_interfaces_ip/ipv6_n9k/show_ip_interface.json` (IPv4 only, `default` VRF only) |
| `ssh_6000_show_vlan_brief_json.txt` | `nxos_ssh/.../test_get_vlans/6000/show_vlan_brief___json.txt` (port ranges; a list-form port list) |
| `ssh_normal_show_vlan_brief_json.txt` | `nxos_ssh/.../test_get_vlans/normal/show_vlan_brief___json.txt` |
| `ssh_issue_1490_utf_show_vlan_brief_json.txt` | `nxos_ssh/.../test_get_vlans/issue_1490_utf/show_vlan_brief___json.txt` (garbled plain VLAN IDs next to the `-utf` ones) |

The `nxapi_` files are NX-API responses; NX-API returns the same structure
as `| json` over SSH. The expected values in the tests are ours, read off
these files, not NAPALM's.
