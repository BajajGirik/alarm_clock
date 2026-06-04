"""Entry point so the package runs with ``python -m alarm``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
