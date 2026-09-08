#!/usr/bin/env python3
"""
GLM-OCR Inference Engine (standalone launcher)
==============================================
Points to the Lightning-OCR integration module.
Run directly from anywhere.

Usage:
    python3 glm_ocr_engine.py path/to/document.pdf --output output_prefix
"""

import sys
import os

# Add lightning-ocr to path
LIGHTNING_OCR_DIR = os.path.expanduser("~/Downloads/image-ocr/lightning-ocr")
if os.path.isdir(LIGHTNING_OCR_DIR):
    sys.path.insert(0, LIGHTNING_OCR_DIR)

# Import and run the engine
import logging
logging.basicConfig(level=logging.INFO)

from app.glm_ocr_backend import main

if __name__ == "__main__":
    main()
