"""Run the validation checks: ``uv run --locked python validation/run.py --help``.

The checks live in :mod:`wbvalidate.cli`; Python puts this folder on the
import path when it runs this file, so ``wbvalidate`` imports from here.
"""

from wbvalidate.cli import main

if __name__ == "__main__":
    main()
