# Upstream `show vlan` sample

Copied unchanged from ntc-templates
(<https://github.com/networktocode/ntc-templates>, `tests/cisco_ios/show_vlan/`,
commit `77768366e7f5833275bdb45c31156bd3e1777378`), under the Apache License 2.0
in [LICENSE](LICENSE).

It is full `show vlan` output; `get_vlans()` sends `show vlan brief`, which
prints only the first table of it. The same template reads both.
