#!/usr/bin/env bash
# exit on error
set -o errexit

# Install system dependencies for Tesseract OCR
apt-get update && apt-get install -y tesseract-ocr

# Install Python dependencies from requirements.txt
pip install -r requirements.txt