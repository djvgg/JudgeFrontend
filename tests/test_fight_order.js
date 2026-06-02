// SPDX-FileCopyrightText: 2026 TOP Team Combat Control
// SPDX-License-Identifier: GPL-3.0-or-later

// Node-Test für fightOrder.js (Chunked-RR der Abkämpf-Reihenfolge auf einer Matte).
// Lauf:  node tests/test_fight_order.js   (kein npm/package.json nötig)
// Pytest ignoriert .js — bleibt vom Python-Suite getrennt.

const assert = require('assert');
const { chunkedRoundRobinOrder, FIGHT_ORDER_CHUNK_SIZE } = require('../fightOrder.js');

// Erstrunden-Kämpfe (erste Spalte) eines Doppel-KO-Winner-Brackets, in Los-Reihenfolge.
function koFirstColumn(bracketId, tableId, n, fighterPrefix) {
    const fights = [];
    for (let pos = 0; pos < n; pos++) {
        fights.push({
            matchId: ++matchIdSeq,
            tableId,
            bracketId,
            bracketType: 'ko',
            phase: 'wb',
            round: 1,           // Backend: DB-Runde 0 → payload 1
            posInRound: pos,
            status: 'pending',
            f1: `${fighterPrefix}${2 * pos}`,
            f2: `${fighterPrefix}${2 * pos + 1}`,
        });
    }
    return fights;
}

// Kanonische DJB-4er-Reihenfolge (0-indexiert), aus edv _POOL_FIGHT_ORDER[4].
const POOL4 = [[0, 3], [1, 2], [0, 2], [1, 3], [0, 1], [2, 3]];

let matchIdSeq = 0;
// Baut die Kämpfe eines 4er-Pools in DJB-Ankunftsreihenfolge.
function pool4(bracketId, tableId, fighterPrefix) {
    return POOL4.map(([a, b]) => ({
        matchId: ++matchIdSeq,
        tableId,
        bracketId,
        status: 'pending',
        f1: `${fighterPrefix}${a}`,
        f2: `${fighterPrefix}${b}`,
    }));
}

// Für jeden Kämpfer: kleinste Lücke zwischen aufeinanderfolgenden Bouts.
// diff = Index-Differenz; "≥2 fremde Kämpfe dazwischen" ⇒ diff ≥ 3.
function minDiffPerFighter(ordered) {
    const seen = new Map();
    ordered.forEach((m, idx) => {
        for (const f of [m.f1, m.f2]) {
            if (!seen.has(f)) seen.set(f, []);
            seen.get(f).push(idx);
        }
    });
    let worst = Infinity;
    for (const idxs of seen.values()) {
        for (let i = 1; i < idxs.length; i++) {
            worst = Math.min(worst, idxs[i] - idxs[i - 1]);
        }
    }
    return worst;
}

let passed = 0;
function test(name, fn) {
    fn();
    passed++;
    console.log(`  ok - ${name}`);
}

// 1) Zwei 4er-Pools auf derselben Matte ⇒ A,A,B,B,A,A,B,B,A,A,B,B
test('two 4er pools on one mat → chunked A,A,B,B pattern', () => {
    matchIdSeq = 0;
    const a = pool4(10, '1', 'A');
    const b = pool4(20, '1', 'B');
    const ordered = chunkedRoundRobinOrder([...a, ...b], FIGHT_ORDER_CHUNK_SIZE);

    assert.strictEqual(ordered.length, 12, 'alle 12 Kämpfe erhalten');
    assert.ok(!ordered.includes(undefined), 'kein undefined');
    const pattern = ordered.map(m => m.bracketId);
    assert.deepStrictEqual(pattern, [10, 10, 20, 20, 10, 10, 20, 20, 10, 10, 20, 20]);
});

// 2) ≥2 fremde Kämpfe Pause pro Kämpfer (diff ≥ 3) bei zwei 4er-Pools
test('each fighter gets >=2 foreign fights of rest', () => {
    matchIdSeq = 0;
    const a = pool4(10, '1', 'A');
    const b = pool4(20, '1', 'B');
    const ordered = chunkedRoundRobinOrder([...a, ...b], FIGHT_ORDER_CHUNK_SIZE);
    assert.ok(minDiffPerFighter(ordered) >= 3,
        `kürzeste Lücke ${minDiffPerFighter(ordered)} < 3 (=mind. 2 Kämpfe dazwischen)`);
});

// 3) Nur eine Liste auf der Matte ⇒ reine DJB-Ankunftsreihenfolge, unverändert
test('single list on a mat → arrival (DJB) order preserved', () => {
    matchIdSeq = 0;
    const a = pool4(10, '1', 'A');
    const ordered = chunkedRoundRobinOrder([...a], FIGHT_ORDER_CHUNK_SIZE);
    assert.deepStrictEqual(ordered.map(m => m.matchId), a.map(m => m.matchId));
});

// 4) Ungleich lange Listen ⇒ erschöpfte Queue übersprungen, Rest hängt sauber an
test('unequal list lengths → no undefined, remainder appended', () => {
    matchIdSeq = 0;
    const a = pool4(10, '1', 'A');                       // 6 Kämpfe
    const b = pool4(20, '1', 'B').slice(0, 2);           // nur 2 Kämpfe
    const ordered = chunkedRoundRobinOrder([...a, ...b], FIGHT_ORDER_CHUNK_SIZE);

    assert.strictEqual(ordered.length, 8);
    assert.ok(!ordered.includes(undefined), 'kein undefined');
    assert.deepStrictEqual(ordered.map(m => m.bracketId),
        [10, 10, 20, 20, 10, 10, 10, 10]);
});

// 5) Zwei Matten ⇒ keine Verschränkung über Matten hinweg (erst Matte 1, dann 2)
test('two mats → no cross-mat interleave', () => {
    matchIdSeq = 0;
    const a = pool4(10, '1', 'A');
    const b = pool4(20, '2', 'B');
    const ordered = chunkedRoundRobinOrder([...a, ...b], FIGHT_ORDER_CHUNK_SIZE);
    const tables = ordered.map(m => m.tableId);
    assert.deepStrictEqual(tables, [...Array(6).fill('1'), ...Array(6).fill('2')]);
});

// 6) tableId == null ⇒ eine Gruppe, kein Crash, alle Kämpfe erhalten
test('null tableId → single group, no crash', () => {
    matchIdSeq = 0;
    const a = pool4(10, null, 'A');
    const b = pool4(20, null, 'B');
    const ordered = chunkedRoundRobinOrder([...a, ...b], FIGHT_ORDER_CHUNK_SIZE);
    assert.strictEqual(ordered.length, 12);
    assert.deepStrictEqual(ordered.map(m => m.bracketId),
        [10, 10, 20, 20, 10, 10, 20, 20, 10, 10, 20, 20]);
});

// 7) Doppel-KO erste Spalte wird am Stück abgekämpft, nicht in 2er-Chunks
test('doppel-ko first column is emitted as one uninterrupted block', () => {
    matchIdSeq = 0;
    const ko = koFirstColumn(30, '1', 4, 'K');   // 4 Erstrundenkämpfe
    const pool = pool4(10, '1', 'A');            // 6 Pool-Kämpfe, gleiche Matte
    // KO-Liste zuerst in Ankunftsreihenfolge
    const ordered = chunkedRoundRobinOrder([...ko, ...pool], FIGHT_ORDER_CHUNK_SIZE);

    // Die 4 KO-Erstrundenkämpfe stehen zusammenhängend (als Block) am Anfang.
    const koIds = ko.map(m => m.matchId);
    assert.deepStrictEqual(ordered.slice(0, 4).map(m => m.matchId), koIds,
        'erste Spalte zuerst, am Stück');
    // Danach laufen die Pool-Kämpfe (KO hat keine weiteren offenen Runden).
    assert.deepStrictEqual(ordered.slice(4).map(m => m.bracketId),
        [10, 10, 10, 10, 10, 10]);
    assert.strictEqual(ordered.length, 10);
});

// 8) KO-Block bleibt zusammenhängend, auch wenn ein Pool in der Gruppe vorne steht
test('doppel-ko first column stays contiguous even when a pool sorts first', () => {
    matchIdSeq = 0;
    const pool = pool4(10, '1', 'A');            // Pool zuerst
    const ko = koFirstColumn(30, '1', 4, 'K');
    const ordered = chunkedRoundRobinOrder([...pool, ...ko], FIGHT_ORDER_CHUNK_SIZE);

    // Der KO-Erstspalten-Block ist nirgends zerstückelt: alle 4 KO-IDs liegen
    // an aufeinanderfolgenden Positionen.
    const positions = ko.map(m => ordered.findIndex(x => x.matchId === m.matchId));
    const min = Math.min(...positions), max = Math.max(...positions);
    assert.strictEqual(max - min, 3, 'KO-Erstspalte zusammenhängend (4 in Folge)');
    assert.ok(!ordered.includes(undefined));
    assert.strictEqual(ordered.length, 10);
});

console.log(`\n${passed} passed`);
