"""Allow `python -m jfind`."""

import sys

from .cli import main

sys.exit(main())
