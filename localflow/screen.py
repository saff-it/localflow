"""Screen capture for the assistant's eyes.

`screencapture` (built-in macOS) grabs the active display to a PNG. Images live
in ~/.localflow/vision/ and are purged after 24h (privacy). The capture is
downscaled + base64-encoded for the vision model.
"""
import base64
import datetime
import glob
import os
import pathlib
import subprocess
import time
from typing import Optional

VISION_DIR = pathlib.Path.home() / ".localflow" / "vision"
MAX_AGE_SECONDS = 24 * 3600
MAX_WIDTH = 1280  # downscale: enough for text/UI, keeps VLM fast and tokens low


def purge_old() -> None:
    now = time.time()
    for path in glob.glob(str(VISION_DIR / "shot-*.png")):
        try:
            if now - os.path.getmtime(path) > MAX_AGE_SECONDS:
                os.unlink(path)
        except OSError:
            pass


def capture() -> Optional[pathlib.Path]:
    """Grab the current screen to a timestamped PNG. Returns the path, or None."""
    VISION_DIR.mkdir(parents=True, exist_ok=True)
    purge_old()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = VISION_DIR / ("shot-%s.png" % stamp)
    # -x silent, -C capture cursor off by default; main display only.
    result = subprocess.run(["screencapture", "-x", "-t", "png", str(path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0 or not path.exists():
        return None
    _downscale(path)
    return path


def _downscale(path: pathlib.Path) -> None:
    """Shrink to MAX_WIDTH via macOS `sips` (built-in) to cut VLM cost/latency."""
    try:
        subprocess.run(["sips", "--resampleWidth", str(MAX_WIDTH), str(path)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def as_base64(path: pathlib.Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")
