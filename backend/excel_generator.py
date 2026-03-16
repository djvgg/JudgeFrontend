# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
from datetime import datetime

import xlrd
import xlwt
from xlutils.copy import copy as xl_copy


def copyCellStyle(readBook, cell):
    """Extracts cell formatting and returns xlwt.XFStyle object"""
    xf = readBook.xf_list[cell.xf_index]
    font = readBook.font_list[xf.font_index]

    style = xlwt.XFStyle()

    fnt = xlwt.Font()
    fnt.name = font.name
    fnt.height = font.height
    fnt.bold = font.bold
    fnt.italic = font.italic
    fnt.underline = font.underline_type
    fnt.colour_index = font.colour_index
    style.font = fnt

    alignment = xlwt.Alignment()
    alignment.horz = xf.alignment.hor_align
    alignment.vert = xf.alignment.vert_align
    alignment.wrap = xf.alignment.text_wrapped
    style.alignment = alignment

    borders = xlwt.Borders()
    borders.left = xf.border.left_line_style
    borders.right = xf.border.right_line_style
    borders.top = xf.border.top_line_style
    borders.bottom = xf.border.bottom_line_style
    style.borders = borders

    pattern = xlwt.Pattern()
    pattern.pattern = xf.background.fill_pattern
    pattern.pattern_fore_colour = xf.background.pattern_colour_index
    pattern.pattern_back_colour = xf.background.background_colour_index
    style.pattern = pattern

    return style


def generate8Bracket(group, templatePath, outputFolder):
    """
    Generates 8-person bracket from template.
    Writes: B3 (weight class), N3 (sport type), N5 (date), B12-B19 (names)
    """
    fighters = group["fighters"]

    if len(fighters) > 8:
        print(f"  [SKIP] Group has {len(fighters)} fighters, need <=8 for this template")
        return None

    genderLabel = {"M": "Maennlich", "W": "Weiblich"}.get(group["gender"], group["gender"])
    ageLabel = group["ageGroup"] if group["ageGroup"] else "Alle"
    weightLabel = group["weightClass"].replace(" ", "_")

    filename = f"8_Doppel-KO-System_{genderLabel}_{ageLabel}_{weightLabel}.xls"
    outputPath = os.path.join(outputFolder, filename)

    readBook = xlrd.open_workbook(templatePath, formatting_info=True)
    writeBook = xl_copy(readBook)
    writeSheet = writeBook.get_sheet(1)
    readSheet = readBook.sheet_by_index(1)

    styleB3 = copyCellStyle(readBook, readSheet.cell(2, 1))
    styleB3.font.height = 20 * 20
    writeSheet.write(2, 1, group["weightClass"], styleB3)

    styleN3 = copyCellStyle(readBook, readSheet.cell(2, 13))
    writeSheet.write(2, 13, "Judo", styleN3)

    styleN5 = copyCellStyle(readBook, readSheet.cell(4, 13))
    writeSheet.write(4, 13, datetime.now().strftime("%d.%m.%Y"), styleN5)

    for i, fighter in enumerate(fighters):
        if i < 8:
            fullName = f"{fighter['name']}, {fighter['vorname']}".strip()
            style = copyCellStyle(readBook, readSheet.cell(11 + i, 1))
            writeSheet.write(11 + i, 1, fullName, style)

    writeBook.save(outputPath)
    print(f"  [OK] Generated: {filename}")

    return outputPath


def generate16Bracket(group, templatePath, outputFolder):
    """
    Generates 16-person bracket from template.
    Writes: B3 (weight class), N3 (sport type), N5 (date), B12-B27 (names)
    """
    fighters = group["fighters"]

    if len(fighters) > 16:
        print(f"  [SKIP] Group has {len(fighters)} fighters, need <=16 for this template")
        return None

    genderLabel = {"M": "Maennlich", "W": "Weiblich"}.get(group["gender"], group["gender"])
    ageLabel = group["ageGroup"] if group["ageGroup"] else "Alle"
    weightLabel = group["weightClass"].replace(" ", "_")

    filename = f"16_Doppel-KO-System_{genderLabel}_{ageLabel}_{weightLabel}.xls"
    outputPath = os.path.join(outputFolder, filename)

    readBook = xlrd.open_workbook(templatePath, formatting_info=True)
    writeBook = xl_copy(readBook)
    writeSheet = writeBook.get_sheet(1)
    readSheet = readBook.sheet_by_index(1)

    styleB3 = copyCellStyle(readBook, readSheet.cell(2, 1))
    styleB3.font.height = 20 * 20
    writeSheet.write(2, 1, group["weightClass"], styleB3)

    styleN3 = copyCellStyle(readBook, readSheet.cell(2, 13))
    writeSheet.write(2, 13, "Judo", styleN3)

    styleN5 = copyCellStyle(readBook, readSheet.cell(4, 13))
    writeSheet.write(4, 13, datetime.now().strftime("%d.%m.%Y"), styleN5)

    for i, fighter in enumerate(fighters):
        if i < 16:
            fullName = f"{fighter['name']}, {fighter['vorname']}".strip()
            style = copyCellStyle(readBook, readSheet.cell(11 + i, 1))
            writeSheet.write(11 + i, 1, fullName, style)

    writeBook.save(outputPath)
    print(f"  [OK] Generated: {filename}")

    return outputPath


def generate32Bracket(group, templatePath, outputFolder):
    """
    Generates 32-person bracket from template.
    ONLY writes D10-D41 (contestant names). Template cells remain unchanged.
    Uses seedToRow32 mapping based on 'los' (seeding position).
    """
    fighters = group["fighters"]

    if len(fighters) > 32:
        print(f"  [SKIP] Group has {len(fighters)} fighters, need <=32 for this template")
        return None

    seedToRow32 = {
        1: 10,
        2: 26,
        3: 18,
        4: 34,
        5: 14,
        6: 30,
        7: 22,
        8: 38,
        9: 12,
        10: 28,
        11: 20,
        12: 36,
        13: 16,
        14: 32,
        15: 24,
        16: 40,
        17: 11,
        18: 27,
        19: 19,
        20: 35,
        21: 15,
        22: 31,
        23: 23,
        24: 39,
        25: 13,
        26: 29,
        27: 21,
        28: 37,
        29: 17,
        30: 33,
        31: 25,
        32: 41,
    }

    genderLabel = {"M": "Maennlich", "W": "Weiblich"}.get(group["gender"], group["gender"])
    ageLabel = group["ageGroup"] if group["ageGroup"] else "Alle"
    weightLabel = group["weightClass"].replace(" ", "_")

    filename = f"32_mod.Doppel-KO-System_{genderLabel}_{ageLabel}_{weightLabel}.xls"
    outputPath = os.path.join(outputFolder, filename)

    readBook = xlrd.open_workbook(templatePath, formatting_info=True)
    writeBook = xl_copy(readBook)
    writeSheet = writeBook.get_sheet(0)
    readSheet = readBook.sheet_by_index(0)

    for fighter in fighters:
        los = fighter.get("los", 0)
        if los < 1 or los > 32:
            continue

        row = seedToRow32.get(los)
        if row:
            nachname = fighter["name"]
            vorname = fighter["vorname"]
            fullName = f"{nachname}, {vorname}".strip()
            style = copyCellStyle(readBook, readSheet.cell(row - 1, 3))
            writeSheet.write(row - 1, 3, fullName, style)

    writeBook.save(outputPath)
    print(f"  [OK] Generated: {filename}")

    return outputPath


def generateBracketsForGroups(groups, outputFolder="generated_brackets"):
    """Generates bracket Excel files for all groups (8/16/32-person brackets)"""
    os.makedirs(outputFolder, exist_ok=True)

    projectRoot = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    excelFilesDir = os.path.join(projectRoot, "excel_files")

    template8 = os.path.join(excelFilesDir, "8_Doppel-KO-System.xls")
    template16 = os.path.join(excelFilesDir, "16_Doppel-KO-System.xls")
    template32 = os.path.join(excelFilesDir, "32_mod.Doppel-KO-System.xls")

    if not os.path.exists(template8):
        print(f"[ERROR] Template not found: {template8}")

    if not os.path.exists(template16):
        print(f"[ERROR] Template not found: {template16}")

    if not os.path.exists(template32):
        print(f"[ERROR] Template not found: {template32}")

    generatedFiles = []

    print("\n" + "=" * 60)
    print("GENERATING BRACKET EXCEL FILES")
    print("=" * 60)

    for group in groups:
        numFighters = len(group["fighters"])

        if numFighters <= 8 and os.path.exists(template8):
            print(f"\nProcessing: {group['label']} ({numFighters} fighters)")
            outputPath = generate8Bracket(group, template8, outputFolder)
            if outputPath:
                generatedFiles.append(outputPath)
        elif numFighters <= 16 and os.path.exists(template16):
            print(f"\nProcessing: {group['label']} ({numFighters} fighters)")
            outputPath = generate16Bracket(group, template16, outputFolder)
            if outputPath:
                generatedFiles.append(outputPath)
        elif numFighters <= 32 and os.path.exists(template32):
            print(f"\nProcessing: {group['label']} ({numFighters} fighters)")
            outputPath = generate32Bracket(group, template32, outputFolder)
            if outputPath:
                generatedFiles.append(outputPath)
        else:
            print(
                f"\n[SKIP] {group['label']}: {numFighters} fighters (need template for this size)"
            )

    print("\n" + "=" * 60)
    print(f"Generated {len(generatedFiles)} bracket file(s)")
    print("=" * 60)

    return generatedFiles
