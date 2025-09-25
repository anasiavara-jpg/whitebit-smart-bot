#!/bin/bash
set -e  # зупинятись при помилках
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
exec python3 main.py
