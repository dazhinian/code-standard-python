"""允许 `python -m code_standard_v2`。

@calls: -
"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
