"""
JudgeFrontend – 16-player single elimination bracket generator.
Tkinter GUI: load XLSX → export PDF.
"""

import os
import sys
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox


# ── Helpers ──────────────────────────────────────────────────────────

def _openFile(filepath):
    """Open a file with the OS default application."""
    if sys.platform == "win32":
        os.startfile(filepath)
    elif sys.platform == "darwin":
        subprocess.run(["open", filepath])
    else:
        subprocess.run(["xdg-open", filepath])


# ── GUI ──────────────────────────────────────────────────────────────

def runGui():
    """Main application window."""
    import bracket_data
    from bracket_pdf import generateBracketPdf
    from xlsx_handler import processXlsx

    root = tk.Tk()
    root.title("JudgeFrontend")
    root.geometry("520x400")
    root.configure(bg="#1e1e1e")
    root.resizable(False, False)

    # ── Title ────────────────────────────────────────────────────────
    tk.Label(root, text="JudgeFrontend", font=("Consolas", 18, "bold"),
             bg="#1e1e1e", fg="white").pack(pady=(20, 5))
    tk.Label(root, text="16-Teilnehmer Bracket",
             font=("Consolas", 11), bg="#1e1e1e", fg="#aaa").pack(pady=(0, 5))

    infoVar = tk.StringVar(value="[Warte auf XLSX-Datei...]")
    tk.Label(root, textvariable=infoVar, font=("Consolas", 9),
             bg="#1e1e1e", fg="#888").pack(pady=(0, 20))

    # ── Status bar ───────────────────────────────────────────────────
    statusVar   = tk.StringVar(value="Bereit.")
    statusLabel = tk.Label(root, textvariable=statusVar, font=("Consolas", 10),
                           bg="#1e1e1e", fg="#4ec94e", wraplength=480)
    statusLabel.pack(side="bottom", pady=15)

    lastFile = {"path": None}

    def setStatus(msg, color="#4ec94e"):
        statusVar.set(msg)
        statusLabel.config(fg=color)
        root.update_idletasks()

    # ── Upload XLSX ──────────────────────────────────────────────────
    def uploadXlsx():
        filepath = filedialog.askopenfilename(
            title="Teilnehmerliste XLSX waehlen",
            filetypes=[("Excel Dateien", "*.xlsx *.xls"), ("Alle Dateien", "*.*")],
        )
        if not filepath:
            return

        try:
            setStatus("Lese XLSX-Datei ...", "#aaa")
            result = processXlsx(filepath)

            if not result:
                setStatus("Fehler: Keine gueltigen Teilnehmer gefunden.", "#e74c3c")
                return

            bracket_data.fighters = result
            count = len(result)
            infoVar.set(f"✓ {count} Teilnehmer geladen")
            setStatus(f"Bereit! {count} Teilnehmer in Bracket.")

        except Exception as e:
            setStatus(f"Fehler beim Lesen: {e}", "#e74c3c")

    # ── Export PDF ───────────────────────────────────────────────────
    def exportPdf():
        if not bracket_data.fighters:
            setStatus("Erst XLSX-Datei laden!", "#e74c3c")
            return

        filepath = filedialog.asksaveasfilename(
            title="Bracket als PDF speichern",
            defaultextension=".pdf",
            filetypes=[("PDF Dateien", "*.pdf"), ("Alle Dateien", "*.*")],
            initialfile="16_Bracket.pdf",
        )
        if not filepath:
            return

        try:
            setStatus("Generiere PDF ...", "#aaa")
            generateBracketPdf(filepath, bracket_data.eventInfo, bracket_data.fighters)
            lastFile["path"] = filepath
            setStatus(f"PDF gespeichert: {os.path.basename(filepath)}")
        except Exception as e:
            setStatus(f"Fehler: {e}", "#e74c3c")

    # ── Open last file ───────────────────────────────────────────────
    def openLast():
        if lastFile["path"] and os.path.exists(lastFile["path"]):
            try:
                _openFile(lastFile["path"])
                setStatus(f"Geoeffnet: {os.path.basename(lastFile['path'])}")
            except Exception as e:
                setStatus(f"Fehler beim Oeffnen: {e}", "#e74c3c")
        else:
            setStatus("Keine Datei vorhanden. Erst exportieren.", "#e74c3c")

    # ── Buttons ──────────────────────────────────────────────────────
    btnStyle = dict(
        bg="#2d5aa0", fg="white", font=("Consolas", 12, "bold"),
        relief="flat", padx=20, pady=8,
        activebackground="#3a6bc5", activeforeground="white", cursor="hand2",
    )
    secBtnStyle = dict(
        bg="#333", fg="white", font=("Consolas", 11),
        relief="flat", padx=15, pady=6,
        activebackground="#555", activeforeground="white", cursor="hand2",
    )

    tk.Button(root, text="1. Teilnehmerliste laden (.xlsx)",
              command=uploadXlsx, **btnStyle).pack(pady=8, fill="x", padx=40)
    tk.Button(root, text="2. Bracket PDF exportieren",
              command=exportPdf, **btnStyle).pack(pady=8, fill="x", padx=40)
    tk.Button(root, text="Letzte Datei oeffnen",
              command=openLast, **secBtnStyle).pack(pady=8)

    root.mainloop()


# ── CLI dependency check ─────────────────────────────────────────────

def _testImports():
    """Smoke-test every required library. Returns list of (name, ok, info)."""
    libs = [
        ("fastapi",    lambda: __import__("fastapi").__version__),
        ("uvicorn",    lambda: __import__("uvicorn").__version__),
        ("cv2",        lambda: __import__("cv2").__version__),
        ("pyzbar",     lambda: (__import__("pyzbar"), "OK")[1]),
        ("pytesseract",lambda: (__import__("pytesseract"), "OK")[1]),
        ("pyserial",   lambda: __import__("serial").__version__),
        ("openpyxl",   lambda: __import__("openpyxl").__version__),
        ("fpdf2",      lambda: (__import__("fpdf"), "OK")[1]),
        ("tkinter",    lambda: str(__import__("tkinter").TkVersion)),
        ("pytest",     lambda: __import__("pytest").__version__),
    ]
    results = []
    for name, fn in libs:
        try:
            results.append((name, True, fn()))
        except Exception as e:
            results.append((name, False, str(e)))
    return results


def runCliCheck():
    """Print dependency check results and exit."""
    results = _testImports()
    print("\n=== JudgeFrontend Dependency Check ===\n")
    allOk = True
    for name, ok, info in results:
        tag = "OK  " if ok else "FAIL"
        print(f"  [{tag}]  {name:30s}  {info}")
        if not ok:
            allOk = False
    print("\n" + ("All dependencies OK!" if allOk else "Some dependencies FAILED."))
    sys.exit(0 if allOk else 1)


# ── Entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--cli" in sys.argv:
        runCliCheck()
    else:
        runGui()
