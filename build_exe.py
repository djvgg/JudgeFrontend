# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Script to build the exe with PyInstaller."""
import subprocess
import sys

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--windowed",
    "--name", "JudgeFrontend",
    "--clean",
    "main.py",
]

print(f"Running: {' '.join(cmd)}")
subprocess.run(cmd, check=True)
print("\nDone! Exe is in the dist/ folder.")
