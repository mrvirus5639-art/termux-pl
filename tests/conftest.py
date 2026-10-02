import os
import sys
import tempfile
from pathlib import Path

# Isolate config/history/playlists from the real ~/.termuxpl before termuxpl is imported.
os.environ["TERMUXPL_HOME"] = tempfile.mkdtemp(prefix="termuxpl-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
