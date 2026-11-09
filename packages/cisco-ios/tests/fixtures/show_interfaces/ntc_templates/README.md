# Upstream `show interfaces` samples

Copied unchanged from ntc-templates
(<https://github.com/networktocode/ntc-templates>, `tests/cisco_ios/show_interfaces/`,
commit `77768366e7f5833275bdb45c31156bd3e1777378`), under the Apache License 2.0
in [LICENSE](LICENSE).

Captures from many switches and routers, so getters are tested against
platforms and releases we don't own rather than tailored to one.
`cisco_ios_show_interfaces.raw` is two captures spliced together
(GigabitEthernet0/2 appears twice, in contradictory states);
`get_interfaces()` must reject it.
