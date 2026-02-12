#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Generate tournament brackets from participant XLSX file.
"""

import os
import sys

# Add parent directory to path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.excelGenerator import generateBracketsForGroups
from src.xlsxHandler import processXlsx


def main():
    # Use the specified participant file
    participant_file = r"C:\Users\Admin\Downloads\teilnehmer_judo_capped_max32_senioren.xlsx"

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
    print()
    print("[OK] Done!")


if __name__ == "__main__":
    main()
