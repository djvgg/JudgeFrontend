// SPDX-FileCopyrightText: 2026 TOP Team Combat Control
// SPDX-License-Identifier: GPL-3.0-or-later

/**
 * Abkämpf-Reihenfolge auf einer Matte — dependency-free, DOM-free, testbar.
 *
 * Zwei Ebenen (siehe WSP/CLAUDE.md Cross-repo-Invariant, Decision 2026-06-02):
 *   (1) intra-Liste: die Ankunftsreihenfolge der Kämpfe IST bereits die
 *       kanonische DJB-/KO-Ordnung (`/api/matches` liefert nach `fight_number`
 *       sortiert) — hier NICHT umsortiert, nur stabil gruppiert.
 *   (2) inter-Liste: pro Matte (`tableId`) werden die Listen (`bracketId`)
 *       im Chunked-Round-Robin verschränkt — je CHUNK_SIZE Kämpfe einer Liste,
 *       dann die nächste (A,A,B,B,A,A,…). Ein 4er-Pool gibt so jedem Kämpfer
 *       ≥2 fremde Kämpfe Pause, sofern ≥1 weitere Liste auf der Matte aktiv ist.
 */

const FIGHT_ORDER_CHUNK_SIZE = 2;

// Stabiler Schlüssel für eine (möglicherweise fehlende) Matten-Zuweisung.
const FIGHT_ORDER_NO_TABLE = '∅';

/**
 * Gruppiert `items` nach `keyFn`, bewahrt die Reihenfolge des ersten Auftretens
 * jedes Schlüssels (Map-Insertion-Order). Gibt ein Array von Gruppen zurück.
 */
function stableGroupBy(items, keyFn) {
    const groups = new Map();
    for (const item of items) {
        const key = keyFn(item);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(item);
    }
    return [...groups.values()];
}

/**
 * Erste Spalte einer Doppel-KO-Liste = Erstrunde des Winner-Brackets
 * (`bracketType==='ko'`, `phase==='wb'`, `round===1`; Backend liefert
 * `round` = DB-Runde +1). Diese Kämpfe werden am Stück abgekämpft, nicht
 * in 2er-Chunks zerstückelt — Dazwischenschieben bleibt manuell (Drag&Drop).
 */
function isKoFirstColumn(m) {
    return m.bracketType === 'ko' && m.phase === 'wb' && m.round === 1;
}

/**
 * Wie viele Elemente diese Queue in ihrem Zug zieht: normalerweise `chunkSize`,
 * aber die erste Spalte einer Doppel-KO-Liste wird als ein zusammenhängender
 * Block (volle Länge) gezogen.
 */
function pullSize(items, cursor, chunkSize) {
    if (!isKoFirstColumn(items[cursor])) return chunkSize;
    let run = 0;
    while (cursor + run < items.length && isKoFirstColumn(items[cursor + run])) run++;
    return run;
}

/**
 * Zieht reihum aus jeder nicht-erschöpften Queue, bis alle leer sind.
 * Pro Zug `chunkSize` Elemente — außer die erste Spalte einer Doppel-KO-Liste,
 * die am Stück gezogen wird. Erschöpfte Queues werden übersprungen (keine
 * Lücken, kein `undefined`).
 */
function roundRobinChunks(queues, chunkSize) {
    const out = [];
    const cursors = queues.map(items => ({ items, i: 0 }));
    while (cursors.some(c => c.i < c.items.length)) {
        for (const c of cursors) {
            if (c.i >= c.items.length) continue;
            const pull = pullSize(c.items, c.i, chunkSize);
            for (let k = 0; k < pull && c.i < c.items.length; k++) {
                out.push(c.items[c.i++]);
            }
        }
    }
    return out;
}

/**
 * Ordnet die OFFENEN (nicht finished/bye) Kämpfe für Anzeige + Dispatch:
 * gruppiert nach Matte, innerhalb jeder Matte Chunked-RR über die Listen.
 * Finished/Bye gehören NICHT in `openMatches` (Aufrufer hängt sie ans Ende).
 *
 * @param {Array} openMatches  Kämpfe mit `tableId` und `bracketId`,
 *                             in Ankunftsreihenfolge (= kanonische DJB-Ordnung).
 * @param {number} chunkSize   Kämpfe pro Liste je Runde (Default 2).
 * @returns {Array}            neu geordnete Kämpfe.
 */
function chunkedRoundRobinOrder(openMatches, chunkSize = FIGHT_ORDER_CHUNK_SIZE) {
    const ordered = [];
    const byTable = stableGroupBy(openMatches, m => String(m.tableId ?? FIGHT_ORDER_NO_TABLE));
    for (const tableGroup of byTable) {
        const lists = stableGroupBy(tableGroup, m => m.bracketId);
        ordered.push(...roundRobinChunks(lists, chunkSize));
    }
    return ordered;
}

// Dual-Export: Browser lädt die Funktionen als Globals (script-Tag vor app.js),
// node/Tests greifen via CommonJS zu.
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        chunkedRoundRobinOrder,
        stableGroupBy,
        roundRobinChunks,
        isKoFirstColumn,
        FIGHT_ORDER_CHUNK_SIZE,
        FIGHT_ORDER_NO_TABLE,
    };
}
