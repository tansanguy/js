#!/usr/bin/env python3
import sys
from expanded_v7_pipeline import main

if __name__ == "__main__":
    sys.argv.insert(1, "lanes")
    raise SystemExit(main())
