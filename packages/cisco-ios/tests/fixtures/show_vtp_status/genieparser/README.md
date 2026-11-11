# Upstream `show vtp status` samples

Copied unchanged from genieparser
(<https://github.com/CiscoTestAutomation/genieparser>, commit
`f11ee1199f7a8ef5221b94d1287b19864c68324b`), under the Apache License 2.0 in
[LICENSE](LICENSE). Renamed `<os>_<name>.txt` from
`src/genie/libs/parser/<os>/tests/ShowVtpStatus/cli/equal/<name>_output.txt`.

They cover the oldest flat layout (`ios_golden_output`, `iosxe_golden_output_2`),
VTP v1 on a v3-capable switch (`iosxe_golden_output`) and VTP v3
(`*_3`, `*_4`, `iosxe_golden_output_5`). `ios_golden_output_3` and
`iosxe_golden_output_3` are the same output.

The expected values in `test_ios_vtp.py` are ours, read off these files, not
genieparser's expected results.
