#!/usr/bin/env python3
"""Run the source installer with its managed Python, before installing the package."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from telegram_search_mcp.installation import main

if __name__ == "__main__":
    main()
