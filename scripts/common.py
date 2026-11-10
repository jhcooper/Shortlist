"""Command-line wrapper. The code lives in shortlist/common.py; `python3 common.py ...` and `import common` work as before."""
import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root, so `shortlist` imports

if __name__ == "__main__":
    runpy.run_module("shortlist.common", run_name="__main__", alter_sys=True)
else:
    import importlib

    sys.modules[__name__] = importlib.import_module("shortlist.common")  # one module object: `common` is `shortlist.common`
