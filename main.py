# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog

from src.excelGenerator import generateBracketsForGroups
from src.xlsxHandler import processXlsx


def openFolder(folderPath):
    """Opens folder using platform-specific command (Windows/Mac/Linux)"""
    if sys.platform == "win32":
        os.startfile(folderPath)
    elif sys.platform == "darwin":
        subprocess.run(["open", folderPath])
    else:
        subprocess.run(["xdg-open", folderPath])


def main():
    root = tk.Tk()
    root.title("JudgeFrontend")
    root.geometry("520x440")
    root.configure(bg="#1e1e1e")
    root.resizable(False, False)

    tk.Label(root, text="JudgeFrontend",
             font=("Consolas", 18, "bold"),
             bg="#1e1e1e", fg="white").pack(pady=(20, 5))
    tk.Label(root, text="Dynamischer Bracket-Generator",
             font=("Consolas", 11),
             bg="#1e1e1e", fg="#aaa").pack(pady=(0, 5))

    infoVar = tk.StringVar(value="[Warte auf XLSX-Datei...]")
    tk.Label(root, textvariable=infoVar,
             font=("Consolas", 9),
             bg="#1e1e1e", fg="#888").pack(pady=(0, 20))

    statusVar = tk.StringVar(value="Bereit.")
    statusLabel = tk.Label(root, textvariable=statusVar,
                           font=("Consolas", 10),
                           bg="#1e1e1e", fg="#4ec94e",
                           wraplength=480)
    statusLabel.pack(side="bottom", pady=15)

    lastFolder = {"path": None}

    def setStatus(msg, color="#4ec94e"):
        statusVar.set(msg)
        statusLabel.config(fg=color)
        root.update_idletasks()

    def loadAndGenerate():
        filepath = filedialog.askopenfilename(
            title="Teilnehmerliste XLSX waehlen",
            filetypes=[("Excel Dateien", "*.xlsx"), ("Alle Dateien", "*.*")]
        )
        if not filepath:
            return

        try:
            setStatus("Lese XLSX-Datei ...", "#aaa")
            groups = processXlsx(filepath)

            if not groups:
                setStatus("Fehler: Keine gueltigen Gruppen gefunden.", "#e74c3c")
                return

            totalFighters = sum(len(g["fighters"]) for g in groups)
            infoVar.set(f"✓ {totalFighters} Teilnehmer in {len(groups)} Gruppe(n)")

            setStatus("Generiere Brackets ...", "#aaa")
            outputDir = "generated_brackets"
            paths = generateBracketsForGroups(groups, outputDir)

            lastFolder["path"] = os.path.abspath(outputDir)

            setStatus(
                f"Fertig! {len(paths)} Bracket-Datei(en) generiert.",
                "#4ec94e"
            )

            openFolder(lastFolder["path"])

        except Exception as e:
            setStatus(f"Fehler: {e}", "#e74c3c")

    def openLastFolder():
        if lastFolder["path"] and os.path.exists(lastFolder["path"]):
            try:
                openFolder(lastFolder["path"])
                setStatus("Ordner geoeffnet: generated_brackets/")
            except Exception as e:
                setStatus(f"Fehler: {e}", "#e74c3c")
        else:
            setStatus("Noch keine Brackets generiert.", "#e74c3c")

    btnStyle = {
        "bg": "#2d5aa0", "fg": "white",
        "font": ("Consolas", 12, "bold"),
        "relief": "flat", "padx": 20, "pady": 8,
        "activebackground": "#3a6bc5",
        "activeforeground": "white",
        "cursor": "hand2"
    }

    secBtnStyle = {
        "bg": "#333", "fg": "white",
        "font": ("Consolas", 11),
        "relief": "flat", "padx": 15, "pady": 6,
        "activebackground": "#555",
        "activeforeground": "white",
        "cursor": "hand2"
    }

    tk.Button(root, text="Teilnehmerliste laden & Brackets generieren",
              command=loadAndGenerate, **btnStyle).pack(pady=12, fill="x", padx=40)

    tk.Button(root, text="Ordner oeffnen (generated_brackets)",
              command=openLastFolder, **secBtnStyle).pack(pady=8)

    root.mainloop()


if __name__ == "__main__":
    main()
