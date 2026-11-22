"""Put the validation folder on the import path, so the tests import ``wbvalidate``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
