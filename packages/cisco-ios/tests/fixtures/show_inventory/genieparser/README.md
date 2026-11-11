# Upstream `show inventory` samples

Copied unchanged from genieparser
(<https://github.com/CiscoTestAutomation/genieparser>, commit
`f11ee1199f7a8ef5221b94d1287b19864c68324b`), under the Apache License 2.0 in
[LICENSE](LICENSE). Renamed `<os>_<name>.txt` from
`src/genie/libs/parser/<os>/tests/ShowInventory/cli/equal/<name>_output.txt`.
Parsed with ntc-templates and our corrected template (D36), not with Genie.

The expected values in `test_ios_inventory.py` are ours, read off these
files, not genieparser's expected results.
