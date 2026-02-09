"""
PDF bracket generator for a 16-player single elimination tournament.
Renders an A3 landscape PDF with fighter boxes, elbow connectors,
and an empty results table for manual entry.
"""

from fpdf import FPDF
from bracket_data import seedingOrder


# ── Helpers ──────────────────────────────────────────────────────────

def _fighterByLos(fighters, losNr):
    """Return the fighter dict whose 'los' matches losNr, or None."""
    for f in fighters:
        if f["los"] == losNr:
            return f
    return None


# ── Custom PDF class ─────────────────────────────────────────────────

class BracketPdf(FPDF):
    """FPDF subclass with bracket-drawing helpers."""

    def drawBox(self, x, y, w, h, text="", bold=False):
        """Draw a bordered rectangle with optional centered text."""
        self.rect(x, y, w, h)
        if text:
            self.set_xy(x, y)
            self.set_font("Helvetica", "B" if bold else "", 7)
            self.cell(w, h, text, align="C")

    def drawElbow(self, xFrom, yFrom, xTo, yTo, midX):
        """
        L-shaped connector: horizontal → vertical → horizontal.
        (xFrom,yFrom) ──► midX ──▼──► (xTo,yTo)
        """
        self.line(xFrom, yFrom, midX, yFrom)
        self.line(midX, yFrom, midX, yTo)
        self.line(midX, yTo, xTo, yTo)


# ── Main generation function ─────────────────────────────────────────

def generateBracketPdf(filepath, eventInfo, fighters):
    """
    Build and save the bracket PDF.

    Args:
        filepath:  destination path (.pdf)
        eventInfo: dict with title, weightClass, art, ort, datum, sportlLtg
        fighters:  list of 16 fighter dicts (id, name, vorname, los, …)
    """
    pdf = BracketPdf(orientation="L", unit="mm", format="A3")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.3)

    # ── Title & subtitle ─────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_xy(10, 8)
    pdf.cell(400, 8, eventInfo["title"], align="C")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(10, 17)
    subtitle = (
        f"Gewichtsklasse: {eventInfo['weightClass']}   |   "
        f"Art: {eventInfo['art']}   |   "
        f"Ort: {eventInfo['ort']}   |   "
        f"Datum: {eventInfo['datum']}   |   "
        f"Sportl. Ltg: {eventInfo['sportlLtg']}"
    )
    pdf.cell(400, 6, subtitle, align="C")

    # ── Layout constants ─────────────────────────────────────────────
    boxW    = 32   # box width
    boxH    = 6    # box height
    colGap  = 12   # horizontal gap between rounds
    pairGap = 4    # vertical gap between two fighters in one fight
    fightGap = 10  # vertical gap between fights
    startX  = 15
    startY  = 32

    # ── Section header ───────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_xy(startX, startY - 5)
    pdf.cell(200, 4, "Hauptrunde (Winners Bracket)")

    # ── Round 1: 8 fights, 16 fighters ───────────────────────────────
    r1X = startX
    r1Pos = []  # [(topCenterY, botCenterY, midY), …]

    for i in range(8):
        topLos = seedingOrder[i * 2]
        botLos = seedingOrder[i * 2 + 1]
        topF   = _fighterByLos(fighters, topLos)
        botF   = _fighterByLos(fighters, botLos)

        topY = startY + i * (boxH * 2 + pairGap + fightGap)
        botY = topY + boxH + pairGap

        topText = f"({topF['id']}) {topF['name']}, {topF['vorname']}" if topF else ""
        botText = f"({botF['id']}) {botF['name']}, {botF['vorname']}" if botF else ""

        pdf.drawBox(r1X, topY, boxW, boxH, topText)
        pdf.drawBox(r1X, botY, boxW, boxH, botText)

        topCenter = topY + boxH / 2
        botCenter = botY + boxH / 2
        r1Pos.append((topCenter, botCenter, (topCenter + botCenter) / 2))

    # ── Generic round drawer (R2 → QF → SF) ─────────────────────────
    def _drawNextRound(prevX, prevPos, count):
        """
        Draw `count` fights for the next round, connected to `prevPos`.
        Returns (newX, newPositions).
        """
        newX   = prevX + boxW + colGap
        newPos = []

        for i in range(count):
            topMid = prevPos[i * 2][2]
            botMid = prevPos[i * 2 + 1][2]
            midY   = (topMid + botMid) / 2
            boxY   = midY - boxH / 2

            pdf.drawBox(newX, boxY, boxW, boxH)

            elbowMidX = prevX + boxW + (newX - prevX - boxW) / 2
            pdf.drawElbow(prevX + boxW, prevPos[i * 2][0],     newX, midY, elbowMidX)
            pdf.drawElbow(prevX + boxW, prevPos[i * 2 + 1][1], newX, midY, elbowMidX)

            newPos.append((boxY + boxH / 2, boxY + boxH / 2, midY))

        return newX, newPos

    r2X, r2Pos = _drawNextRound(r1X, r1Pos, 4)   # Round 2 – 4 fights
    qfX, qfPos = _drawNextRound(r2X, r2Pos, 2)   # Quarterfinal – 2 fights
    sfX, sfPos = _drawNextRound(qfX, qfPos, 1)    # Semifinal – 1 fight

    # ── Winner box (yellow) ──────────────────────────────────────────
    winX = sfX + boxW + colGap
    winY = sfPos[0][2] - boxH / 2

    pdf.set_fill_color(255, 255, 0)
    pdf.rect(winX, winY, boxW + 6, boxH, "FD")
    pdf.set_xy(winX, winY)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(boxW + 6, boxH, "SIEGER", align="C")

    elbowMidX = sfX + boxW + (winX - sfX - boxW) / 2
    pdf.drawElbow(sfX + boxW, sfPos[0][2], winX, sfPos[0][2], elbowMidX)

    # ── Results table (empty, for handwriting) ───────────────────────
    resX = winX + boxW + 20
    resY = 32

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(resX, resY)
    pdf.cell(80, 6, "Ergebnisse / Platzierung", align="C")

    for i in range(8):
        y = resY + 8 + i * 6
        label = f"{i + 1}. Platz"

        pdf.rect(resX, y, 20, 6)
        pdf.set_xy(resX, y)
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(20, 6, label, align="C")

        pdf.rect(resX + 20, y, 60, 6)  # empty cell for manual entry

    # ── Save ─────────────────────────────────────────────────────────
    pdf.output(filepath)
    return filepath
