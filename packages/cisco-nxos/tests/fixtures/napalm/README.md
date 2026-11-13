# Upstream NX-OS JSON samples

Copied unchanged from NAPALM (<https://github.com/napalm-automation/napalm>,
commit `820a06b2069eb1d7b0cbe8943ee2dea6e2949d1a`), under the Apache License
2.0 in [LICENSE](LICENSE). Renamed from `test/<driver>/mocked_data/test_get_facts/<case>/<file>`:

| File | From |
|---|---|
| `nxapi_normal_show_version.json` | `nxos/.../normal/show_version.json` (NX-OSv 7.3(1)D1(1)) |
| `nxapi_normal_show_inventory.json` | `nxos/.../normal/show_inventory.json` |
| `ssh_N93180_show_inventory_json.txt` | `nxos_ssh/.../N93180/show_inventory___json.txt` (chassis name printed with its quotes) |
| `ssh_does_not_support_json_show_inventory_json.txt` | `nxos_ssh/.../does_not_support_json/show_inventory___json.txt` |

The `nxapi_` files are NX-API responses; NX-API returns the same structure
as `| json` over SSH. The expected values in the tests are ours, read off
these files, not NAPALM's.
