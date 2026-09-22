"""Non-interactive validation for a packed Sammie-Roto 2 environment."""

from __future__ import annotations

import importlib
import os
import platform
import sys


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    modules = (
        "accelerate",
        "av",
        "cv2",
        "diffusers",
        "einops",
        "numpy",
        "OpenEXR",
        "PIL",
        "requests",
        "safetensors",
        "torch",
        "torchvision",
        "tqdm",
    )
    for module in modules:
        importlib.import_module(module)

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app.platformName(), "Qt did not load a platform plugin"

    # Import representative application entry points without launching the GUI.
    importlib.import_module("sammie.core")
    importlib.import_module("sammie.matting")
    importlib.import_module("sammie.removal")
    importlib.import_module("sammie.sammie")

    import torch

    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {platform.platform()}")
    print(f"Qt platform plugin: {app.platformName()}")
    print(f"PyTorch: {torch.__version__}")
    print("Sammie-Roto 2 dependency smoke test: OK")


if __name__ == "__main__":
    main()
