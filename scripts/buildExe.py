# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os

import PyInstaller.__main__


def buildExe():
    """Builds standalone Windows executable using PyInstaller"""
    scriptDir = os.path.dirname(os.path.abspath(__file__))
    projectDir = os.path.dirname(scriptDir)
    mainScript = os.path.join(projectDir, "main.py")
    excelFilesDir = os.path.join(projectDir, "excel_files")
    srcDir = os.path.join(projectDir, "src")

    PyInstaller.__main__.run([
        mainScript,
        '--name=JudgeFrontend',
        '--onefile',
        '--windowed',
        f'--add-data={excelFilesDir};excel_files',
        f'--add-data={srcDir};src',
        '--icon=NONE',
        '--clean',
        '--noconfirm',
    ])

    print("\n" + "="*60)
    print("Build complete!")
    print("Executable: dist/JudgeFrontend.exe")
    print("="*60)


if __name__ == "__main__":
    buildExe()
