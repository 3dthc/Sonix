#!/usr/bin/env python3
"""Recompresses a built skin's pictures losslessly: every pixel and every PNG colour type stay exactly as they were
(8-bit RGBA / RGB, what MPC loads today); only the compression gets better (about 22% smaller for Sonix's skin).

    python3 tools/shrink_skin.py vst/build/skin

Needs the oxipng Python package (pip install pyoxipng); without it the skin is left as it is. vst/build.sh runs this
after the build.

(Tried and dropped: reducing pictures to 256 colours halves them, but bands the knob caps and wipes out the LCD's
scan lines.)"""
import glob
import os
import subprocess
import sys


def load_oxipng():
    try:
        import oxipng
        return oxipng
    except ImportError:
        pass
    for extra in ([], ["--break-system-packages"]):
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--user", "-q", "pyoxipng"] + extra,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode == 0:
            break
    try:
        import importlib
        import site
        importlib.invalidate_caches()
        sys.path.append(site.getusersitepackages())
        import oxipng
        return oxipng
    except ImportError:
        return None


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "vst/build/skin"
    oxipng = load_oxipng()
    if oxipng is None:
        print("skin pictures: left as they are (oxipng not available: pip install pyoxipng)")
        return
    files = sorted(glob.glob(os.path.join(root, "**", "*.png"), recursive=True))
    before = after = 0
    for p in files:
        before += os.path.getsize(p)
        oxipng.optimize(p, level=3, strip=oxipng.StripChunks.safe(), bit_depth_reduction=False,
                        color_type_reduction=False, palette_reduction=False, grayscale_reduction=False)
        after += os.path.getsize(p)
    print("skin pictures: %.1f MB -> %.1f MB, lossless (%d files)" % (before / 1e6, after / 1e6, len(files)))


if __name__ == "__main__":
    main()
