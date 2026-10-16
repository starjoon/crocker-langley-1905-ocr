"""Baseline: transcribe each answer-key chunk with Tesseract into predictions/tesseract/.

Tesseract can't mark bold, so its bold score is blank.

Usage: .venv/bin/python scripts/run_tesseract.py
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "predictions" / "tesseract"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "answer_key" / "manifest.json").read_text())
    for chunk, info in manifest.items():
        # psm 6: one uniform block of text, which a single column chunk is.
        text = subprocess.run(
            ["tesseract", str(ROOT / info["image"]), "-", "--psm", "6", "-l", "eng"],
            capture_output=True, text=True, check=True,
        ).stdout
        (OUT / f"{chunk}.txt").write_text(text)
        print(f"{chunk}: {len(text.splitlines())} lines")


if __name__ == "__main__":
    main()
