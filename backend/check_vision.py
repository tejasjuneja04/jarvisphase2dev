"""
Checks that picture understanding works.   python check_vision.py

Prints which Python is running, whether mlx-vlm and Pillow are installed, then loads the vision model and
describes a test picture. Copy the output to Claude if something fails.
"""

import platform
import sys
import traceback

print("Python     :", sys.executable)
print("Version    :", sys.version.split()[0], "|", platform.platform())

for pkg in ("mlx", "mlx_lm", "mlx_vlm", "PIL", "transformers"):
    try:
        mod = __import__(pkg)
        print(f"{pkg:<11}: OK {getattr(mod, '__version__', '')}")
    except Exception as e:
        print(f"{pkg:<11}: MISSING ({e})")

print("\nFix for anything missing (run exactly this):")
print(f"  {sys.executable} -m pip install -U mlx-vlm Pillow\n")

try:
    import config
    import vision
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (480, 200), "white")
    ImageDraw.Draw(img).text((40, 80), "HELLO JARVIS 2026", fill="black")
    path = config.DATA_DIR / "vision_test.png"
    img.save(path)
    print("OCR (macOS) :", vision.ocr_text(path) or "(nothing / not macOS)")
    print("Loading the vision model, the first time downloads a few GB...")
    if vision._vision.ensure(say=print):
        print("Model      :", vision._vision.name)
        print("Answer     :", vision._vision.ask(str(path), "What text is in this picture?"))
        print("\n✅ Vision works.")
    else:
        print("\n❌ Vision model failed:", vision._vision.error)
except Exception:
    traceback.print_exc()
