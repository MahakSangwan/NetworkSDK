# Upstream `show interfaces switchport` samples

Copied unchanged from genieparser
(<https://github.com/CiscoTestAutomation/genieparser>, commit
`f11ee1199f7a8ef5221b94d1287b19864c68324b`), under the Apache License 2.0 in
[LICENSE](LICENSE). Renamed `<os>_<name>.txt` from
`src/genie/libs/parser/<os>/tests/ShowInterfacesSwitchport/cli/equal/<name>_output.txt`.

`iosxe_golden_output_3` is single-interface output
(`show interfaces Gi1/1 switchport`) of a port-channel member; Genie rejects
it because the port-channel's own block is missing. The getter always sends
the full command, which prints it.

The expected values in `test_ios_switchports.py` are ours, read off these
files, not genieparser's expected results.
