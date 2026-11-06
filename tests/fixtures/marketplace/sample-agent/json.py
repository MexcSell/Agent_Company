import os
from pathlib import Path

# Would prove that package code ran on the host: it must never be imported.
Path(os.environ.get("MARKETPLACE_MARKER", "/tmp/marketplace-marker")).write_text("ran")
