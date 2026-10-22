import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        f"notebook-test needs Python 3.10 or newer; this is Python {sys.version_info[0]}.{sys.version_info[1]}. "
        "Install it with a newer interpreter, e.g. `python3.12 -m pip install .`.")
