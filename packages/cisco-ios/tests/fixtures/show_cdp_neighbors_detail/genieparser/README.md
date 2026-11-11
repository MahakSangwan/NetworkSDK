# Upstream `show cdp neighbors detail` samples

Copied unchanged from genieparser
(<https://github.com/CiscoTestAutomation/genieparser>, commit
`f11ee1199f7a8ef5221b94d1287b19864c68324b`), under the Apache License 2.0 in
[LICENSE](LICENSE). Renamed `<os>_<name>.txt` from
`src/genie/libs/parser/<os>/tests/ShowCdpNeighborsDetail/cli/equal/<name>_output.txt`.
Parsed with Genie (D35).

Some end with a "Total ... entries displayed" line and some don't, as on
real devices (some IOS releases don't print it). The expected values in `test_ios_neighbors.py` are ours, read off these
files, not genieparser's expected results.
