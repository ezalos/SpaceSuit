# ABOUTME: pytest config — adds the scripts/ dir to sys.path so tests import modules directly.
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))
