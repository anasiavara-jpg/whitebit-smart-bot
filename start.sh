#!/usr/bin/env bash
set -e

echo "== Boot: applying wb_patch.diff if present =="
if [ -f wb_patch.diff ]; then
  patch -p0 -N -r - < wb_patch.diff || true
  echo "== Patch applied (or already in place) =="
else
  echo "== No wb_patch.diff found, skipping =="
fi

echo "== Starting bot =="
python main.py
