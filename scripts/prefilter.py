"""Command-line wrapper. The code lives in shortlist/core/prefilter.py; `python3 prefilter.py ...` and `import prefilter` work as before."""
import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root, so `shortlist` imports

if __name__ == "__main__":
    runpy.run_module("shortlist.core.prefilter", run_name="__main__", alter_sys=True)
else:
    import importlib

    sys.modules[__name__] = importlib.import_module("shortlist.core.prefilter")  # one module object: `prefilter` is `shortlist.core.prefilter`
