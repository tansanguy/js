#!/usr/bin/env python3
import sys
from compact_v9_pipeline import main

if __name__ == "__main__":
    sys.argv.insert(1, "all")
    raise SystemExit(main())
