#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Generate tournament brackets from participant XLSX file.
"""

import os
import sys
import requests

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.excelGenerator import generateBracketsForGroups
from src.xlsxHandler import processXlsx


def main():
    # Use the local participant file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    participant_file = os.path.join(project_root, "teilnehmer_judo_capped_max32_senioren.xlsx")

    if not os.path.exists(participant_file):
        print(f"[ERROR] Participant file not found: {participant_file}")
        return

    print("="*70)
    print("TOURNAMENT BRACKET GENERATOR")
    print("="*70)
    print(f"Participant file: {participant_file}")
    print()

    # Process the xlsx file
    print("="*70)
    print("PROCESSING PARTICIPANT LIST")
    print("="*70)
    groups = processXlsx(participant_file)

    if not groups:
        print("\n[ERROR] No groups generated!")
        return

    print()

    # Generate brackets
    output_folder = "generated_brackets"
    generated_files = generateBracketsForGroups(groups, output_folder)

    print()
    print("="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Output folder: {output_folder}")
    print(f"Generated {len(generated_files)} bracket file(s):")
    for filepath in generated_files:
        print(f"  - {os.path.basename(filepath)}")
    
    # New: Automated push to Judge Interface
    print()
    print("="*70)
    print("PUSHING TO JUDGE INTERFACE")
    print("="*70)
    try:
        url = "http://localhost:5001/api/import-brackets"
        response = requests.post(url, json=groups, timeout=5)
        if response.status_code == 200:
            print(f"[OK] Successfully pushed {response.json()['matches_imported']} matches to real-time server.")
        else:
            print(f"[WARNING] Server returned status code {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("[SKIP] Could not connect to Tournament Server (is it running on port 5001?)")
    except Exception as e:
        print(f"[ERROR] Failed to push data: {e}")

    print()
    print("[OK] Done!")


if __name__ == "__main__":
    main()
