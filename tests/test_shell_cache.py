"""The shell must fetch the inventory once per session and drop it on a tenant change."""
import pathlib
import sys, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_core as core

fetches = []
core.list_data_files = lambda t, k, log=None: (fetches.append(t), {"a.qvd": "d"})[1]

# Exercise the accessor's logic without booting Qt.
class Shell:
    data_file_map = None
    def __init__(self):
        self.tenant, self.api_key, self.data_files = "t1", "k", None
        self.script_facts = {}

import studio_app
Shell.data_file_map = studio_app.MainWindow.data_file_map
sh = Shell()
sh.data_file_map(); sh.data_file_map(); sh.data_file_map()
assert fetches == ["t1"], fetches
print("PASS: three calls, one fetch.")

# Tenant change clears it, mirroring script_facts.
sh.data_files = None
sh.tenant = "t2"
sh.data_file_map()
assert fetches == ["t1", "t2"], fetches
print("PASS: a new tenant refetches.")
