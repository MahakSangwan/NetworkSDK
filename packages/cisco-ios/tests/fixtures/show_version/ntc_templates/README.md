# Upstream `show version` samples

Copied unchanged from ntc-templates
(<https://github.com/networktocode/ntc-templates>, `tests/cisco_ios/show_version/`,
commit `77768366e7f5833275bdb45c31156bd3e1777378`), under the Apache License 2.0
in [LICENSE](LICENSE).

They cover IOS 12.2 to 16.x from many platforms, so getters are tested against
releases we don't own rather than tailored to one. Several are switch stacks,
which `get_facts()` must reject (D26).
