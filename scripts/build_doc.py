"""Command-line wrapper. The code lives in shortlist/core/build_doc.py; `python3 build_doc.py ...` and `import build_doc` work as before."""
import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root, so `shortlist` imports

if __name__ == "__main__":
    runpy.run_module("shortlist.core.build_doc", run_name="__main__", alter_sys=True)
else:
    import importlib

    sys.modules[__name__] = importlib.import_module("shortlist.core.build_doc")  # one module object: `build_doc` is `shortlist.core.build_doc`
