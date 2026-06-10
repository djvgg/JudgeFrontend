/**
 * JudgeFrontend - Modular Referee Application Logic
 */

// --- CONFIGURATION ---
const Config = {
    API_HOST: window.location.hostname || 'localhost',
    get API_BASE() { return `http://${this.API_HOST}:5001`; },
    get DATA_SOURCE() { return `${this.API_BASE}/api/matches`; },
    get WS_URL() { return `ws://${this.API_HOST}:5001/ws`; },
    DEV_MODE: window.location.port === '5500' || window.location.hostname === 'localhost'
};

// --- STATE MANAGEMENT ---
const State = {
    activeMatches: [],
    currentScoringMatch: null,
    currentBracketCategory: null,
    currentMatchId: null,
    draggedMatchId: null,
    isTableFilterActive: true,
    nextUpMatchId: null,           // which match is queued ("⏭ Als nächster")
    autoSendEnabled: false,        // toggle state, persisted to localStorage
    hideCompletedLists: false,     // hide fully-finished brackets, persisted to localStorage
    scoreHistory: [],
    timer: {
        interval: null,
        remainingSeconds: 240,
        isRunning: false
    },
    restTimerInterval: null
};

// --- NETWORK & WEBSOCKETS ---
const Network = {
    socket: null,

    async fetchMatches() {
        try {
            const response = await fetch(Config.DATA_SOURCE);
            if (!response.ok) throw new Error('Network response was not ok');
            const data = await response.json();
            State.activeMatches = data.matches.map(m => ({
                ...m,
                restSeconds: m.restSeconds || (m.restTimeMin * 60)
            }));
            State.currentMatchId = data.currentMatchId ?? null;
            UI.updateTournamentTitle(data.tournamentName);
            // Reihenfolge kommt aus dem Backend (`order` = persistiertes fight_number).
            // Die Auto-Reihenfolge (Chunked-RR) wird NICHT mehr bei jedem Fetch
            // angewandt — sonst würden manuelle Drag&Drop-Umsortierungen überschrieben.
            // Sie wird bewusst per Button ausgelöst (UI.applyAutoOrder).
        } catch (error) {
            console.error('Failed to fetch matches:', error);
            UI.displayError('Turnierdaten konnten nicht geladen werden.');
        }
    },

    initWebSocket() {
        this.socket = new WebSocket(Config.WS_URL);
        this.socket.onopen = () => UI.updateConnectionStatus(true);
        this.socket.onmessage = (e) => this.handleMessage(JSON.parse(e.data));
        this.socket.onclose = () => {
            UI.updateConnectionStatus(false);
            setTimeout(() => this.initWebSocket(), 5000);
        };
    },

    handleMessage(data) {
        if (data.type === 'SCORE_SYNC') {
            const idx = State.activeMatches.findIndex(m => m.matchId === data.matchId);
            if (idx !== -1) {
                const prev = State.activeMatches[idx];
                const wasNotFinished = prev.status !== 'finished';
                // Keep the stable running fight number (a single-match rebuild can't
                // know the tournament-wide order) and the rest timer.
                State.activeMatches[idx] = { ...data.match, restSeconds: prev.restSeconds, fightNr: prev.fightNr ?? data.match.fightNr, p1From: data.match.p1From ?? prev.p1From, p2From: data.match.p2From ?? prev.p2From };
                if (State.currentScoringMatch?.matchId === data.matchId) {
                    State.currentScoringMatch = State.activeMatches[idx];
                    UI.updateScoreDisplay();
                }

                const justFinished = wasNotFinished && data.match.status === 'finished';
                if (justFinished && State.autoSendEnabled) {
                    // Explicit ⏭ marker wins; otherwise default to the next fight in
                    // line on the same mat (the finished one is already filtered out).
                    let queuedId = State.nextUpMatchId;
                    if (!queuedId || queuedId === data.matchId) {
                        const onMat = UI.orderedActiveOnMat(data.match.tableId);
                        queuedId = onMat[0]?.matchId ?? null;
                    }
                    State.nextUpMatchId = null;
                    localStorage.removeItem('nextUpMatchId');
                    if (queuedId && queuedId !== data.matchId) sendToIpponboard(queuedId);
                }

                UI.renderFightList();
                UI.renderBracketVisualization();
            }
        } else if (data.type === 'SIGNAL') {
            Scoring.triggerTimerSignal(data.signalType, false);
        } else if (data.type === 'CURRENT_MATCH_SET') {
            State.currentMatchId = data.matchId ?? null;
            UI.renderFightList();
            UI.renderBracketVisualization(); // rote "aktueller Kampf"-Umrandung im Baum mitziehen
        } else if (data.type === 'REFRESH_LIST') {
            App.init();
        }
    },

    send(data) {
        if (this.socket?.readyState === WebSocket.OPEN) {
            this.socket.send(JSON.stringify(data));
        }
    }
};

// --- UI RENDERER ---
const UI = {
    switchView(view) {
        const isFights = view === 'fights';
        document.getElementById('fights-view').style.display = isFights ? 'block' : 'none';
        document.getElementById('brackets-view').style.display = isFights ? 'none' : 'flex';
        document.getElementById('tab-fights').classList.toggle('active', isFights);
        document.getElementById('tab-brackets').classList.toggle('active', !isFights);
        if (!isFights) this.updateBracketSidebar();
    },

    updateTournamentTitle(title) {
        document.getElementById('tournament-title').textContent = title;
    },


    showVictoryPopup(matchOrName, winnerSlot) {
        const overlay = document.getElementById('victory-overlay');
        const subtitle = document.getElementById('victory-subtitle');
        const p1Box = document.getElementById('victory-p1');
        const p2Box = document.getElementById('victory-p2');

        // Legacy: called with just a name string → fall back to single-line message.
        if (typeof matchOrName === 'string') {
            subtitle.textContent = `${matchOrName} hat den Kampf gewonnen!`;
            if (p1Box) p1Box.style.display = 'none';
            if (p2Box) p2Box.style.display = 'none';
            overlay.style.display = 'flex';
            return;
        }

        const m = matchOrName;
        const p1Name = `${m.p1.firstName || ''} ${m.p1.lastName || ''}`.trim() || '—';
        const p2Name = `${m.p2.firstName || ''} ${m.p2.lastName || ''}`.trim() || '—';
        const p1Score = m.p1.score?.points ?? 0;
        const p2Score = m.p2.score?.points ?? 0;

        document.getElementById('victory-p1-name').textContent = p1Name;
        document.getElementById('victory-p2-name').textContent = p2Name;
        document.getElementById('victory-p1-score').textContent = p1Score;
        document.getElementById('victory-p2-score').textContent = p2Score;

        // Reset state, then mark winner
        p1Box.classList.remove('victory-fighter--winner');
        p2Box.classList.remove('victory-fighter--winner');
        p1Box.style.display = '';
        p2Box.style.display = '';
        document.getElementById('victory-p1-badge').textContent = '';
        document.getElementById('victory-p2-badge').textContent = '';

        if (winnerSlot === 'p1') {
            p1Box.classList.add('victory-fighter--winner');
            document.getElementById('victory-p1-badge').textContent = 'SIEGER';
            subtitle.textContent = `${p1Name} gewinnt`;
        } else if (winnerSlot === 'p2') {
            p2Box.classList.add('victory-fighter--winner');
            document.getElementById('victory-p2-badge').textContent = 'SIEGER';
            subtitle.textContent = `${p2Name} gewinnt`;
        } else {
            subtitle.textContent = 'Unentschieden';
        }

        overlay.style.display = 'flex';
    },

    updateConnectionStatus(isOnline) {
        const indicator = document.getElementById('connection-status');
        if (indicator) indicator.className = `status-indicator ${isOnline ? 'online' : 'offline'}`;
    },

    renderFightList() {
        const container = document.getElementById('fight-list');
        const tableNum = document.getElementById('table-select')?.value || "1";
        const assignedTable = localStorage.getItem('assignedTable');

        // Self-heal a stale "⏭ Als nächster" marker: if the queued match has
        // vanished from the list or is already done, drop it so no outdated
        // assignment lingers (e.g. it was played without auto-send).
        if (State.nextUpMatchId !== null) {
            const queued = State.activeMatches.find(m => m.matchId === State.nextUpMatchId);
            if (!queued || queued.status === 'finished' || queued.status === 'bye') {
                State.nextUpMatchId = null;
                localStorage.removeItem('nextUpMatchId');
            }
        }

        container.innerHTML = '';

        // Hide eager-materialized TBD-vs-TBD phantoms (full-tree display only) —
        // the queue shows real fightable bouts + already-decided ones.
        let displayMatches = [...State.activeMatches].filter(m => this.queueEligible(m));

        // Hide whole lists (brackets) that are completely done, when the user
        // opted in. Completeness is judged over the entire bracket (all mats),
        // not the table-filtered slice, so a list only disappears once every one
        // of its real bouts is finished/bye.
        if (State.hideCompletedLists) {
            const doneBrackets = this.completedGroups(m => m.bracketId);
            displayMatches = displayMatches.filter(m => !doneBrackets.has(m.bracketId));
        }

        // Apply table filter if active AND we are not admin
        if (State.isTableFilterActive && tableNum !== 'admin') {
            displayMatches = displayMatches.filter(m => String(m.tableId) === String(tableNum));
        }

        // Always respect the 'order' property, but force finished matches to the bottom
        displayMatches.sort((a, b) => {
            const aFinished = (a.status === 'finished' || a.status === 'bye') ? 1 : 0;
            const bFinished = (b.status === 'finished' || b.status === 'bye') ? 1 : 0;
            if (aFinished !== bFinished) return aFinished - bFinished;
            return (a.order || 0) - (b.order || 0);
        });
        // Identify the true "Next" match per table (only the first 'upcoming' per tableId)
        const nextMatchByTable = new Map();
        [...State.activeMatches].sort((a, b) => a.order - b.order).forEach(m => {
            if (this.isStartable(m) && (m.status === 'upcoming' || m.status === 'pending')
                && !nextMatchByTable.has(m.tableId)) {
                nextMatchByTable.set(m.tableId, m.matchId);
            }
        });
        const nextMatchIds = new Set(nextMatchByTable.values());

        // Per-mat queue rank: 1 = next up on that mat, counting only upcoming
        // fights in running order — so you can see when each fight is due.
        const rankByMatch = this.computeMatRanks();

        // "Als nächstes" = the on-deck fight = rank 2 in each mat's running order
        // (rank 1 is the one going to the mat now). A manual ⏭ marker overrides
        // its mat's default — markAsNext() also reorders it into position 2, so the
        // marker and the running order stay in sync.
        const nextUpIds = this.computeNextUpIds();

        const isAdminMode = tableNum === 'admin';
        displayMatches.forEach(m => container.appendChild(
            this.createFightCard(m, assignedTable, nextMatchIds, isAdminMode,
                rankByMatch.get(m.matchId), nextUpIds)));
        document.getElementById('match-count').textContent = `${displayMatches.length} Kämpfe angezeigt`;
    },

    // List membership: real fightable bouts + already-decided ones. The eager
    // full-tree materialization creates TBD-vs-TBD phantoms for *display* in the
    // bracket tree — those must never clutter the Kampfliste / mat queue.
    queueEligible(m) {
        return (m.p1?.gpId != null && m.p2?.gpId != null)
            || m.status === 'finished' || m.status === 'bye';
    },

    // Set of group keys (via `keyOf`) whose every queue-eligible bout is
    // finished/bye — i.e. the lists that are completely done. TBD phantoms are
    // excluded (not queueEligible), so an eager-materialized tree with open
    // future rounds is NOT counted as complete. A group with no eligible bout
    // yet is omitted. Used with `m => m.bracketId` for the Kampfliste and
    // `m => m.category` for the Live-Turnierbaum sidebar.
    completedGroups(keyOf) {
        const tally = new Map(); // key -> { total, done }
        State.activeMatches.forEach(m => {
            const k = keyOf(m);
            if (k == null || !this.queueEligible(m)) return;
            const e = tally.get(k) || { total: 0, done: 0 };
            e.total += 1;
            if (m.status === 'finished' || m.status === 'bye') e.done += 1;
            tally.set(k, e);
        });
        const done = new Set();
        tally.forEach((e, k) => { if (e.total > 0 && e.done === e.total) done.add(k); });
        return done;
    },

    // Active AND startable (both fighters known, not yet done) — the set the mat
    // queue ranks and orders over (excludes finished/bye AND TBD phantoms).
    isStartable(m) {
        return m.p1?.gpId != null && m.p2?.gpId != null
            && m.status !== 'finished' && m.status !== 'bye';
    },

    // Per-mat queue rank: matchId -> position on its mat (1 = next up), counting
    // only startable fights in running order. Single source for the fight list AND
    // the live bracket tree's "Matte X, Kampf N" badges.
    computeMatRanks() {
        const rankByMatch = new Map();
        const perMatCount = new Map();
        [...State.activeMatches]
            .filter(m => this.isStartable(m))
            .sort((a, b) => (a.order || 0) - (b.order || 0))
            .forEach(m => {
                const k = String(m.tableId);
                const r = (perMatCount.get(k) || 0) + 1;
                perMatCount.set(k, r);
                rankByMatch.set(m.matchId, r);
            });
        return rankByMatch;
    },

    // Compact "Matte X, Kampf N" badge HTML for a bracket-tree node, or '' if the
    // fight has no mat rank (finished/bye/not in the active queue).
    matRankBadge(matchId, rankByMatch, tableId) {
        const r = rankByMatch.get(matchId);
        if (!r || tableId == null) return '';
        return `<span class="match-node-mat" title="Reihenfolge auf der Matte">Matte ${tableId}, Kampf ${r}</span>`;
    },

    // Startable fights on a mat, in running order (excludes finished/bye + TBD).
    orderedActiveOnMat(tableKey) {
        return [...State.activeMatches]
            .filter(m => String(m.tableId) === String(tableKey) && this.isStartable(m))
            .sort((a, b) => (a.order || 0) - (b.order || 0));
    },

    // Reorder `matchId` so it sits right behind the first active fight on its mat
    // (i.e. becomes "Kampf 2" = on deck). Persists the new order via REORDER.
    moveMatchToSecondOnMat(matchId) {
        const list = State.activeMatches;
        const fromIdx = list.findIndex(m => m.matchId === matchId);
        if (fromIdx === -1) { this.renderFightList(); return; }
        const target = list[fromIdx];
        const first = this.orderedActiveOnMat(target.tableId)
            .find(m => m.matchId !== matchId);
        if (!first) { this.renderFightList(); return; } // already the only/first fight
        list.splice(fromIdx, 1);
        const firstIdx = list.findIndex(m => m.matchId === first.matchId);
        list.splice(firstIdx + 1, 0, target);
        list.forEach((m, i) => m.order = i + 1);
        const orders = {};
        list.forEach(m => orders[m.matchId] = m.order);
        Network.send({ type: 'REORDER', orders });
        this.renderFightList();
    },

    // The on-deck fight id per mat: rank 2 by default, overridden by a manual ⏭ marker.
    computeNextUpIds() {
        const ids = new Set();
        const manual = State.nextUpMatchId;
        const manualMatch = manual != null
            ? State.activeMatches.find(m => m.matchId === manual
                && m.status !== 'finished' && m.status !== 'bye')
            : null;
        const manualMat = manualMatch ? String(manualMatch.tableId) : null;
        const mats = new Set(State.activeMatches.map(m => String(m.tableId)));
        mats.forEach(matKey => {
            if (matKey === manualMat) { ids.add(manual); return; }
            const onMat = this.orderedActiveOnMat(matKey);
            if (onMat.length >= 2) ids.add(onMat[1].matchId); // rank 2 = on deck
        });
        return ids;
    },

    // Abkämpf-Reihenfolge auf einer Matte: Chunked-Round-Robin über die Listen
    // (`bracketId`) je Matte (`tableId`), siehe fightOrder.js + CLAUDE.md-Invariant
    // (Decision 2026-06-02). Ersetzt das frühere Geschlechter-Interleave. Byes/
    // finished stehen nie zur Disposition — immer unten, als gewonnen markiert.
    autoInterleaveMatches() {
        const matches = State.activeMatches;
        const finished = matches
            .filter(m => m.status === 'finished' || m.status === 'bye')
            .sort((a, b) => (a.order ?? a.fightNr) - (b.order ?? b.fightNr));
        const open = matches.filter(m => m.status !== 'finished' && m.status !== 'bye');

        const ordered = chunkedRoundRobinOrder(open, FIGHT_ORDER_CHUNK_SIZE);

        const finalOrder = [...ordered, ...finished];
        finalOrder.forEach((m, i) => m.order = i + 1);
        State.activeMatches = finalOrder;
    },

    // Wendet die Auto-Reihenfolge (Chunked-RR) bewusst per Button an und
    // persistiert sie via REORDER (Backend schreibt fight_number) — gilt damit
    // für alle Tablets und überlebt den nächsten Fetch. Überschreibt manuelle
    // Drag&Drop-Umsortierungen (das ist der Zweck: zurück zur Auto-Ordnung).
    applyAutoOrder() {
        this.autoInterleaveMatches();
        // Auto-Reihenfolge baut die Sequenz neu — ein manuell gesetztes "als nächster"
        // würde sonst auf dem alten Kampf kleben und den neuen Rang 2 überstimmen.
        // Marker zurücksetzen, damit er aus der frischen Ordnung neu abgeleitet wird.
        State.nextUpMatchId = null;
        localStorage.removeItem('nextUpMatchId');
        const orders = {};
        State.activeMatches.forEach(m => orders[m.matchId] = m.order);
        Network.send({ type: 'REORDER', orders });
        this.renderFightList();
    },

    createFightCard(match, assignedTable, nextMatchIds, isAdminMode, queueRank, nextUpIds) {
        let isReadOnly = assignedTable && String(match.tableId) !== String(assignedTable);
        if (isAdminMode) isReadOnly = false;
        const isCurrent = State.currentMatchId === match.matchId;
        const card = document.createElement('div');
        card.className = `fight-card ${match.status === 'live' ? 'active-match' : ''} ${isReadOnly ? 'read-only' : ''} ${isCurrent ? 'current-match' : ''}`;
        card.dataset.matchId = match.matchId;

        const restTag = (match.status !== 'finished' && match.restSeconds > 0)
            ? `<div class="rest-tag" id="rest-timer-${match.matchId}">${this.formatTime(match.restSeconds)}</div>`
            : '';

        const isNextOnTable = nextMatchIds && nextMatchIds.has(match.matchId);
        const isUpcoming = match.status === 'upcoming' || match.status === 'pending';
        const statusLabel = isCurrent ? '⚡ AKTIV' :
            (isUpcoming ? (isNextOnTable ? 'NÄCHSTE' : 'WARTEND') :
            (match.status === 'live' ? 'LIVE' : 'BEENDET'));

        const rankLabel = (queueRank && (match.status === 'upcoming' || match.status === 'pending'))
            ? `<div class="queue-rank-badge" title="Reihenfolge auf dieser Matte — so wird abgekämpft">Matte ${match.tableId}, Kampf ${queueRank}</div>`
            : '';

        // Endkampf-Marker (Finale / Kampf um Platz 3) — neben "Matte X, Kampf N".
        const stageLabel = match.stageLabel
            ? `<div class="stage-label-badge" title="Entscheidungskampf">${match.stageLabel}</div>`
            : '';

        // A Freilos/Walkover is stored as p1==p2 (or both empty). Show the real
        // fighter once vs "Freilos" — never the same name twice (consistent with
        // renderKoTree's isBye handling).
        const isBye = match.status === 'bye'
            || (match.p1.gpId != null && match.p1.gpId === match.p2.gpId);
        const p1HasName = match.p1.firstName || match.p1.lastName;
        const p2HasName = match.p2.firstName || match.p2.lastName;
        let p1Display, p2Display, p1Club, p2Club;
        if (isBye) {
            const realName = p1HasName ? `${match.p1.firstName} ${match.p1.lastName}`.trim()
                : (p2HasName ? `${match.p2.firstName} ${match.p2.lastName}`.trim() : 'Freilos');
            p1Display = realName;
            p2Display = 'Freilos';
            p1Club = p1HasName ? (match.p1.club || '') : (match.p2.club || '');
            p2Club = '';
        } else {
            p1Display = `${match.p1.firstName} ${match.p1.lastName}`;
            p2Display = `${match.p2.firstName} ${match.p2.lastName}`;
            p1Club = match.p1.club;
            p2Club = match.p2.club;
        }

        card.innerHTML = `
            <div class="fight-nr-badge"><div class="fight-num-circle" title="Kampfnummer">${match.fightNr}</div></div>
            <div class="category-box">
                <span class="table-label">Tisch ${match.tableId}</span>
                ${rankLabel}
                ${stageLabel}
                <span class="category-name">${match.categoryLabel || match.category}</span>
                <a href="#" class="bracket-link" onclick="UI.handleBracketClick(event, ${match.matchId})">Live-Turnierbaum</a>
            </div>
            <div class="fighters-display">
                <div class="fighter p1">
                    <span class="fighter-name">${p1Display}</span>
                    <span class="fighter-club">${p1Club}</span>
                </div>
                <div class="vs-divider">VS</div>
                <div class="fighter p2${isBye ? ' fighter--freilos' : ''}">
                    <span class="fighter-name">${p2Display}</span>
                    <span class="fighter-club">${p2Club}</span>
                </div>
            </div>
            <div class="status-box">
                <div class="status-badge ${match.status} ${isCurrent ? 'current' : ''}">${statusLabel}</div>
                ${restTag}
            </div>
            <div class="action-area">
                ${(match.status !== 'finished' && match.status !== 'bye') ?
                `<button class="btn-start" ${isReadOnly ? 'disabled' : ''} onclick="event.stopPropagation(); sendToIpponboard(${match.matchId})" title="An Ipponboard senden">Start</button>
                 <button class="btn-next-up ${nextUpIds && nextUpIds.has(match.matchId) ? 'active' : ''}" ${isReadOnly ? 'disabled' : ''} onclick="event.stopPropagation(); markAsNext(${match.matchId})" title="Als nächsten markieren — rutscht auf Position 2 (Kampf 2) der Matte und wird bei Auto-Send automatisch ans Ipponboard gesendet, sobald der aktuelle Kampf endet.">${nextUpIds && nextUpIds.has(match.matchId) ? '⏭ NÄCHSTER' : '⏭ Als nächster'}</button>
                 <button class="btn-result" ${isReadOnly ? 'disabled' : ''} onclick="event.stopPropagation(); openResultDialog(${match.matchId})">Ergebnis setzen</button>` :
                (match.status === 'bye'
                    ? `<span class="winner-badge bye-badge" title="Freilos">🏆 ${match.winnerName || 'Freilos'} <span class="bye-tag">Freilos</span></span>`
                    : `${match.winnerName
                        ? `<span class="winner-badge" title="Sieger">🏆 ${match.winnerName}</span>`
                        : `<span class="draw-badge" title="Unentschieden">Unentschieden</span>`}
                       <div class="btn-row">
                         <button class="btn-result btn-icon" ${isReadOnly ? 'disabled' : ''} onclick="event.stopPropagation(); openResultDialog(${match.matchId})" title="Ergebnis ändern" aria-label="Ergebnis ändern">✏️</button>
                         <button class="btn-reopen btn-icon" ${isReadOnly ? 'disabled' : ''} onclick="event.stopPropagation(); reopenMatch(${match.matchId})" title="Erneut starten" aria-label="Erneut starten">🔄</button>
                       </div>`)}
            </div>
        `;

        if (!isReadOnly && match.status !== 'finished' && match.status !== 'bye') {
            this.setupDragAndDrop(card, match.matchId);
        }

        return card;
    },

    formatTime(seconds) {
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return `${m}:${s.toString().padStart(2, '0')}`;
    },

    handleBracketClick(e, matchId) {
        e.stopPropagation();
        const m = State.activeMatches.find(m => m.matchId === matchId);
        if (!m) return;

        // Relocate logic: Instead of opening Excel, we show the internal visualization
        State.currentBracketCategory = m.category;
        this.switchView('brackets');
        this.renderBracketVisualization();
    },

    updateBracketSidebar() {
        const list = document.getElementById('bracket-category-list');
        if (!list) return;
        list.innerHTML = '';

        // Only list categories whose fights run on the selected mat — mirrors the
        // "Nur mein Tisch" filter of the Kampfliste. Admin (or filter off) → all lists.
        const tableNum = document.getElementById('table-select')?.value || "1";
        const filterByMat = State.isTableFilterActive && tableNum !== 'admin';
        const visible = State.activeMatches.filter(m =>
            !filterByMat || String(m.tableId) === String(tableNum));

        // Same opt-in as the Kampfliste: drop lists whose every bout is done.
        // Keyed by category here (the sidebar's grouping unit).
        const doneCats = State.hideCompletedLists ? this.completedGroups(m => m.category) : null;

        const byKey = new Map();
        visible.forEach(m => {
            if (doneCats && doneCats.has(m.category)) return;
            const key = m.category;
            if (!byKey.has(key)) {
                const base = m.groupLabel || m.categoryLabel || m.category;
                byKey.set(key, m.bracketTypeLabel ? `${base} · ${m.bracketTypeLabel}` : base);
            }
        });

        // If the active category no longer runs on this mat, fall back to the
        // first visible list so the tree never shows an off-mat category.
        if (State.currentBracketCategory && !byKey.has(State.currentBracketCategory)) {
            State.currentBracketCategory = byKey.size > 0 ? byKey.keys().next().value : null;
            this.renderBracketVisualization();
        }

        byKey.forEach((label, key) => {
            const div = document.createElement('div');
            div.className = `category-item ${State.currentBracketCategory === key ? 'active' : ''}`;
            div.textContent = label;
            div.onclick = () => { State.currentBracketCategory = key; this.renderBracketVisualization(); this.updateBracketSidebar(); };
            list.appendChild(div);
        });
        if (!State.currentBracketCategory && byKey.size > 0) {
            State.currentBracketCategory = byKey.keys().next().value;
            this.renderBracketVisualization();
        }
    },

    renderBracketVisualization() {
        const viz = document.getElementById('bracket-visualization');
        const titleEl = document.getElementById('current-bracket-title');

        if (!viz || !State.currentBracketCategory) return;
        viz.innerHTML = '';
        const sample = State.activeMatches.find(m => m.category === State.currentBracketCategory);
        const baseTitle = (sample && (sample.groupLabel || sample.categoryLabel)) || State.currentBracketCategory;
        const mode = sample && sample.bracketTypeLabel;
        if (titleEl) titleEl.textContent = mode ? `${baseTitle} · ${mode}` : baseTitle;

       
        const matches = State.activeMatches.filter(m => m.category === State.currentBracketCategory);
        if (matches.length === 0) return;

        
        const isDouble = sample && sample.bracketType === 'double';
        if (isDouble) {
            this.renderDoublePool(viz, matches);
            return;
        }

        // Single pool: render the DJB score sheet (fighters in rows, bouts in
        // columns) instead of the KO tree, matching the edv Excel pool list.
        if (matches.length > 0 && matches.every(m => m.phase === 'pool')) {
            viz.style.display = 'block';
            viz.style.position = 'static';
            viz.style.minWidth = '';
            viz.style.minHeight = '';
            const container = document.createElement('div');
            container.className = 'pools-container';
            container.appendChild(this.renderRoundRobinPool(0, matches));
            viz.appendChild(container);
            return;
        }

        // Standalone KO bracket → Los-seeded tree (matches the Excel Doppel-KO form).
        this.renderKoTree(viz, matches);
    },

    /**
     * Render a KO bracket as a Los-seeded tree into `container`. First-round
     * leaves are ordered by pos_in_round (Los/seed order); empty leaf slots show
     * "Freilos". Reused by the standalone KO view and the double-pool KO stage.
     */
    renderKoTree(container, matches) {
        const rankByMatch = this.computeMatRanks();
        // Same on-deck set the Kampfliste uses, so the tree's gold "in
        // Vorbereitung" border matches the ⏭-marker / running order exactly.
        const nextUpIds = this.computeNextUpIds();
        // Build a map of matches and their children (matches that feed INTO them)
        const matchMap = new Map();
        matches.forEach(m => matchMap.set(m.matchId, { ...m, children: [] }));

        matches.forEach(m => {
            if (m.nextMatchId && matchMap.has(m.nextMatchId)) {
                const parent = matchMap.get(m.nextMatchId);
                parent.children.push(m.matchId);
            }
        });

        
        const feedersBySlot = new Map();
        matches.forEach(m => {
            if (m.nextMatchId && m.nextMatchPos && matchMap.has(m.nextMatchId)) {
                const f = feedersBySlot.get(m.nextMatchId) || { p1: null, p2: null };
                f[m.nextMatchPos] = m;
                feedersBySlot.set(m.nextMatchId, f);
            }
        });

        
        function projectedName(match, slotKey) {
            const slot = match[slotKey];
            const hasRealFighter = slot && slot.gpId && slot.lastName && slot.lastName !== 'TBD';
            if (hasRealFighter) return null;
            const feeders = feedersBySlot.get(match.matchId);
            const feeder = feeders ? feeders[slotKey] : null;
            if (!feeder || feeder.winnerId == null) return null;
            const winnerSide = feeder.p1 && feeder.p1.gpId === feeder.winnerId ? feeder.p1
                              : feeder.p2 && feeder.p2.gpId === feeder.winnerId ? feeder.p2
                              : null;
            if (!winnerSide) return null;
            return `${winnerSide.firstName || ''} ${winnerSide.lastName || ''}`.trim() || null;
        }

        // Identify roots (matches that don't feed into any other match in this category)
        const roots = [];
        matches.forEach(m => {
            if (!m.nextMatchId || !matchMap.has(m.nextMatchId)) {
                roots.push(m);
            }
        });

        // Sort roots by matchId ascending (Winner bracket has lower IDs than Loser bracket)
        roots.sort((a, b) => a.matchId - b.matchId);

        const MATCH_WIDTH = 220;
        const MATCH_HEIGHT = 120;
        const X_SPACING = 300;
        const Y_SPACING = 140;

        let currentY = 0;
        const positions = new Map();
        const visited = new Set();

        function calculatePosDAG(matchId) {
            if (visited.has(matchId)) return positions.get(matchId);
            visited.add(matchId);

            const node = matchMap.get(matchId);
            const children = node.children.map(cid => matchMap.get(cid));

            // Order children top-to-bottom by Los/seed (pos_in_round); this makes
            // the first-round leaves come out in Los order like the Excel sheet.
            // Fall back to the feeder's slot (p1 above p2), then matchId.
            children.sort((a, b) => {
                const pa = a.posInRound ?? 0, pb = b.posInRound ?? 0;
                if (pa !== pb) return pa - pb;
                if (a.nextMatchPos === 'p1' && b.nextMatchPos === 'p2') return -1;
                if (a.nextMatchPos === 'p2' && b.nextMatchPos === 'p1') return 1;
                return a.matchId - b.matchId;
            });

            const childPositions = [];
            for (const child of children) {
                childPositions.push(calculatePosDAG(child.matchId));
            }

            let myY;
            if (childPositions.length === 0) {
                myY = currentY;
                currentY += Y_SPACING;
            } else {
                const sumY = childPositions.reduce((acc, pos) => acc + pos.y, 0);
                myY = sumY / childPositions.length;
            }

            const myX = (node.round - 1) * X_SPACING;
            const pos = { x: myX, y: myY };
            positions.set(matchId, pos);
            return pos;
        }

        for (const root of roots) {
            calculatePosDAG(root.matchId);
            currentY += Y_SPACING;
        }

        let maxX = 0;
        let maxY = 0;
        positions.forEach(pos => {
            if (pos.x > maxX) maxX = pos.x;
            if (pos.y > maxY) maxY = pos.y;
        });

        const OFFSET_X = 40;
        const OFFSET_Y = 40;

        container.style.position = 'relative';
        container.style.minWidth = `${maxX + MATCH_WIDTH + OFFSET_X * 2}px`;
        container.style.minHeight = `${maxY + MATCH_HEIGHT + OFFSET_Y * 2}px`;
        // Cleanup old flex properties
        container.style.display = 'block';

        // Leaf matches (first round) have no feeders → an empty slot is a Freilos,
        // not a yet-to-be-decided TBD.
        const leafIds = new Set([...matchMap.values()].filter(n => n.children.length === 0).map(n => n.matchId));

        // Draw SVG lines first
        const svgNS = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(svgNS, "svg");
        svg.style.position = 'absolute';
        svg.style.top = '0';
        svg.style.left = '0';
        svg.style.width = '100%';
        svg.style.height = '100%';
        svg.style.pointerEvents = 'none';

        matches.forEach(m => {
            if (m.nextMatchId && positions.has(m.nextMatchId)) {
                const pos = positions.get(m.matchId);
                const parentPos = positions.get(m.nextMatchId);

                const startX = pos.x + MATCH_WIDTH + OFFSET_X;
                const startY = pos.y + MATCH_HEIGHT / 2 + OFFSET_Y;
                const endX = parentPos.x + OFFSET_X;
                const endY = parentPos.y + MATCH_HEIGHT / 2 + OFFSET_Y;

                const path = document.createElementNS(svgNS, "path");
                const midX = startX + (endX - startX) / 2;
                path.setAttribute('d', `M ${startX} ${startY} H ${midX} V ${endY} H ${endX}`);
                path.setAttribute('stroke', '#22AAF0'); // accent color from branding palette
                path.setAttribute('stroke-width', '2');
                path.setAttribute('fill', 'none');
                svg.appendChild(path);
            }
        });
        container.appendChild(svg);

        // Draw nodes
        matches.forEach(m => {
            const pos = positions.get(m.matchId);
            if (!pos) return;
            const isLB = m.phase === 'lb';
            const isPool = m.phase === 'pool';
            const node = document.createElement('div');
            const phaseClass = isLB ? ' bracket-match-node--lb'
                              : isPool ? ' bracket-match-node--pool'
                              : '';
            node.className = `bracket-match-node absolute-node${phaseClass}`;
            node.style.position = 'absolute';
            node.style.left = `${pos.x + OFFSET_X}px`;
            node.style.top = `${pos.y + OFFSET_Y}px`;
            node.style.width = `${MATCH_WIDTH}px`;

            const p1Score = m.p1.score.points || 0;
            const p2Score = m.p2.score.points || 0;
            // A KO Freilos is stored as a self-pairing (p1==p2) with status 'bye' and
            // the present fighter already set as winner_id → mark it as won.
            const isBye = m.status === 'bye'
                || (m.p1.gpId != null && m.p1.gpId === m.p2.gpId);
            let p1Won = m.status === 'finished' && m.winnerId != null && m.winnerId === m.p1.gpId;
            let p2Won = m.status === 'finished' && m.winnerId != null && m.winnerId === m.p2.gpId;

            const p1Real = (m.p1.firstName || m.p1.lastName) ? `${m.p1.firstName || ''} ${m.p1.lastName || ''}`.trim() : '';
            const p2Real = (m.p2.firstName || m.p2.lastName) ? `${m.p2.firstName || ''} ${m.p2.lastName || ''}`.trim() : '';
            const p1Proj = projectedName(m, 'p1');
            const p2Proj = projectedName(m, 'p2');
            // Where an empty slot's fighter comes from: "Sieger/Verlierer aus #N"
            // (N = the Kampfnummer shown on the source node), from the backend's
            // p1From/p2From. Falls back to the projected winner name, then TBD/Freilos.
            const sourceLabel = (from) => from
                ? (from.kind === 'loser' ? `Verlierer aus #${from.fightNr}` : `Sieger aus #${from.fightNr}`)
                : '';
            const emptyLabel = leafIds.has(m.matchId) ? 'Freilos' : 'TBD';
            let p1Display = p1Real || sourceLabel(m.p1From) || p1Proj || emptyLabel;
            let p2Display = p2Real || sourceLabel(m.p2From) || p2Proj || emptyLabel;
            // Any non-real slot reads as tentative (italic + dimmed).
            let p1ProjectedClass = !p1Real ? ' projected' : '';
            let p2ProjectedClass = !p2Real ? ' projected' : '';
            // Projected winner name (if the feeder is already decided) as a hover hint.
            const p1Title = (!p1Real && p1Proj) ? `voraussichtlich: ${p1Proj}` : '';
            const p2Title = (!p2Real && p2Proj) ? `voraussichtlich: ${p2Proj}` : '';

            if (isBye) {
                // Freilos: the present fighter advances kampflos → winner on top,
                // an empty "Freilos" seat below; no score boxes.
                p1Display = p1Real || p2Real || 'Freilos';
                p2Display = 'Freilos';
                p1Won = true;
                p2Won = false;
                p1ProjectedClass = '';
                p2ProjectedClass = ' projected';
            }
            const p1ScoreBox = isBye ? '' : p1Score;
            const p2ScoreBox = isBye ? '' : p2Score;

            const badge = isBye ? '<span class="match-node-badge match-node-badge--bye">Freilos</span>'
                         : isLB ? '<span class="match-node-badge match-node-badge--lb">LB</span>'
                         : isPool ? `<span class="match-node-badge match-node-badge--pool">Pool ${(m.poolIndex ?? 0) + 1}</span>`
                         : '';
            // Endkampf-Marker (Finale / Bronze) aus dem Backend-stageLabel — der Baum
            // zeigt jeden dritten Platz als eigenen, beschrifteten Strang.
            const stageBadge = m.stageLabel
                ? `<span class="match-node-badge match-node-badge--stage">${
                    m.stageLabel === 'Finale' ? '🏆 Finale' : '🥉 3. Platz'}</span>`
                : '';

            // Click on a bracket node opens the Result-picker dialog (Teil B).
            // Only fightable matches react — TBD / bye / future-only nodes show a hint.
            const p1HasFighter = m.p1.gpId != null;
            const p2HasFighter = m.p2.gpId != null;
            const isReady = p1HasFighter && p2HasFighter;
            const isFinished = m.status === 'finished';
            if (isBye) {
                node.classList.add('bye-node');
                node.title = `Kampf #${m.fightNr} — Freilos: ${p1Display} kampflos weiter`;
            } else if (isReady && !isFinished) {
                node.classList.add('clickable');
                node.title = `Kampf #${m.fightNr} — Aktionen (senden / als nächster / Ergebnis)`;
                node.onclick = (e) => this.showFightActionMenu(m.matchId, e);
            } else if (isReady && isFinished) {
                node.classList.add('clickable');
                node.title = `Kampf #${m.fightNr} (beendet) — Aktionen (Ergebnis / wiederholen)`;
                node.onclick = (e) => this.showFightActionMenu(m.matchId, e);
            } else if (!isReady && !isFinished) {
                node.classList.add('not-ready');
                node.title = 'Kampf noch nicht startbar — beide Kämpfer fehlen';
            }

            // Live-Status-Umrandung: aktueller Kampf (rot) bzw. in Vorbereitung
            // (gold). Nicht für Freilose — die sind kampflos entschieden.
            if (!isBye) {
                if (State.currentMatchId === m.matchId) node.classList.add('bracket-match-node--current');
                if (nextUpIds.has(m.matchId)) node.classList.add('bracket-match-node--next');
            }

            node.innerHTML = `
                <div class="match-node-header">
                    <span class="match-node-num">#${m.fightNr}</span>
                    ${this.matRankBadge(m.matchId, rankByMatch, m.tableId)}
                    ${badge}
                    ${stageBadge}
                </div>
                <div class="match-node-p ${p1Won ? 'winner' : ''}${p1ProjectedClass}">
                    <span class="p-name"${p1Title ? ` title="${p1Title}"` : ''}>${p1Display}</span>
                    <span class="p-score-box">${p1ScoreBox}</span>
                </div>
                <div class="match-node-p ${p2Won ? 'winner' : ''}${p2ProjectedClass}">
                    <span class="p-name"${p2Title ? ` title="${p2Title}"` : ''}>${p2Display}</span>
                    <span class="p-score-box">${p2ScoreBox}</span>
                </div>
            `;
            container.appendChild(node);
        });
    },

    // Floating action menu for a fight tile in the Live-Turnierbaum. Offers the
    // same actions as the Kampfliste card buttons (senden / als nächster /
    // Ergebnis / wiederholen), filtered to what the fight's state allows.
    // Reuses the existing global handlers so the WS/REORDER paths stay identical.
    showFightActionMenu(matchId, ev) {
        ev.stopPropagation();
        document.querySelectorAll('.fight-action-menu').forEach(el => el.remove());
        const m = State.activeMatches.find(x => x.matchId === matchId);
        if (!m) return;
        const isReady = m.p1?.gpId != null && m.p2?.gpId != null;
        const isFinished = m.status === 'finished';
        if (m.status === 'bye') return; // Freilos: nichts zu tun

        const items = [];
        if (!isFinished && isReady) {
            items.push({ act: 'send', label: '▶ An Ipponboard senden' });
            const isNext = State.nextUpMatchId === matchId;
            items.push({ act: 'next', label: isNext ? '⏭ Nicht mehr als nächster' : '⏭ Als nächster' });
        }
        if (isReady) items.push({ act: 'result', label: '✏️ Ergebnis setzen' });
        if (isFinished) items.push({ act: 'reopen', label: '🔄 Kampf wiederholen' });
        if (!items.length) return;

        const menu = document.createElement('div');
        menu.className = 'fight-action-menu';
        menu.innerHTML = `<div class="fam-title">Kampf #${m.fightNr}</div>`
            + items.map(it => `<button class="fam-item" data-act="${it.act}">${it.label}</button>`).join('');
        document.body.appendChild(menu);

        // Position near the cursor, clamped to the viewport.
        const mw = menu.offsetWidth, mh = menu.offsetHeight;
        let x = ev.clientX, y = ev.clientY;
        if (x + mw > window.innerWidth) x = window.innerWidth - mw - 8;
        if (y + mh > window.innerHeight) y = window.innerHeight - mh - 8;
        menu.style.left = `${Math.max(8, x)}px`;
        menu.style.top = `${Math.max(8, y)}px`;

        const close = () => {
            menu.remove();
            document.removeEventListener('click', close);
            document.removeEventListener('keydown', onKey);
        };
        const onKey = (e) => { if (e.key === 'Escape') close(); };
        menu.querySelectorAll('.fam-item').forEach(btn => btn.onclick = (e) => {
            e.stopPropagation();
            const act = btn.dataset.act;
            close();
            if (act === 'send') sendToIpponboard(matchId);
            else if (act === 'next') markAsNext(matchId);
            else if (act === 'result') openResultDialog(matchId);
            else if (act === 'reopen') reopenMatch(matchId);
        });
        // Defer so this very click doesn't immediately re-close the menu.
        setTimeout(() => {
            document.addEventListener('click', close);
            document.addEventListener('keydown', onKey);
        }, 0);
    },


    renderDoublePool(viz, matches) {
        const poolFights = matches.filter(m => m.phase === 'pool');
        const koFights   = matches.filter(m => m.phase !== 'pool');

        // Group pool fights by pool_index.
        const poolsMap = new Map(); // poolIndex -> list of fights
        poolFights.forEach(m => {
            const k = m.poolIndex ?? 0;
            if (!poolsMap.has(k)) poolsMap.set(k, []);
            poolsMap.get(k).push(m);
        });

        const poolsContainer = document.createElement('div');
        poolsContainer.className = 'pools-container';

        [...poolsMap.keys()].sort((a, b) => a - b).forEach(poolIndex => {
            const poolFightsHere = poolsMap.get(poolIndex);
            poolsContainer.appendChild(this.renderRoundRobinPool(poolIndex, poolFightsHere));
        });

        viz.style.display = 'block';
        viz.style.position = 'static';
        viz.style.minWidth = '';
        viz.style.minHeight = '';
        viz.appendChild(poolsContainer);

        // KO stage (pool winners) — render as the HF→Finale tree, same diagram as
        // the standalone KO, instead of the old flat cards.
        if (koFights.length > 0) {
            const koSection = document.createElement('div');
            koSection.className = 'pool-ko-section';
            const heading = document.createElement('h3');
            heading.className = 'pool-section-title';
            heading.textContent = 'KO-Phase';
            koSection.appendChild(heading);

            const koTree = document.createElement('div');
            koSection.appendChild(koTree);
            viz.appendChild(koSection);
            this.renderKoTree(koTree, koFights);
        }
    },

    /**
     * Canonical DJB pool fight order — mirrors edv
     * pool_renderer._generate_fight_schedule(n). Returns [a,b] slot pairs
     * (0-indexed) in run order. n===2 is best-of-three (the lone pair, 3x).
     * (Cross-repo contract, see WSP/CLAUDE.md.)
     */
    poolFightSchedule(n) {
        if (n < 2) return [];
        if (n === 2) return [[0, 1], [0, 1], [0, 1]];
        if (n === 3) return [[0, 2], [1, 2], [0, 1]];
        if (n === 4) return [[0, 3], [1, 2], [0, 2], [1, 3], [0, 1], [2, 3]];
        if (n === 5) return [[0, 3], [1, 4], [0, 2], [1, 3], [2, 4], [0, 1], [2, 3], [0, 4], [1, 2], [3, 4]];
        // circle method for larger pools
        const m = n % 2 === 0 ? n : n + 1;
        let players = Array.from({ length: m }, (_, i) => i);
        const out = [];
        for (let r = 0; r < m - 1; r++) {
            for (let i = 0; i < m / 2; i++) {
                const p1 = players[i], p2 = players[m - 1 - i];
                if (p1 < n && p2 < n) out.push([p1, p2]);
            }
            players = [players[0], players[players.length - 1], ...players.slice(1, -1)];
        }
        return out;
    },

    /**
     * Recover the real pool Start-Nr order (slots 0..n-1) from the DB fights.
     * edv `_build_pool_pairs` assigns pos_in_round in combinations(range(n),2)
     * order with p1 = the lower slot, so fight at pos p sits on slot pair combo[p]
     * (p1→combo[p][0], p2→combo[p][1]). A complete round-robin contains every pair,
     * so this is exact (the schedule alone can't recover it — every permutation
     * reproduces the full pair set). Fallback: the given (name-sorted) order.
     */
    solvePoolSlots(gpIds, poolFights) {
        const n = gpIds.length;
        const combos = [];
        for (let a = 0; a < n; a++) for (let b = a + 1; b < n; b++) combos.push([a, b]);
        const slotOfGp = new Map();
        let consistent = true;
        poolFights.forEach(f => {
            const p = f.posInRound;
            if (p == null || p >= combos.length || f.p1?.gpId == null || f.p2?.gpId == null) return;
            const [sa, sb] = combos[p];
            if (slotOfGp.has(f.p1.gpId) && slotOfGp.get(f.p1.gpId) !== sa) consistent = false;
            if (slotOfGp.has(f.p2.gpId) && slotOfGp.get(f.p2.gpId) !== sb) consistent = false;
            slotOfGp.set(f.p1.gpId, sa);
            slotOfGp.set(f.p2.gpId, sb);
        });
        if (!consistent || slotOfGp.size !== n) return gpIds.slice();
        const slotGpIds = new Array(n);
        slotOfGp.forEach((slot, gp) => { if (slot < n) slotGpIds[slot] = gp; });
        return slotGpIds.every(x => x != null) ? slotGpIds : gpIds.slice();
    },

    /**
     * Build one pool as the official DJB score sheet: fighters in ROWS,
     * one COLUMN per bout in canonical schedule order (best-of-three = 3 columns
     * for a 2-fighter pool). Mirrors edv excel_form_filler. Returns a DOM element.
     */
    renderRoundRobinPool(poolIndex, poolFights) {
        const rankByMatch = this.computeMatRanks();
        // Collect unique fighters.
        const fighterByGp = new Map();
        const collect = (slot) => {
            if (!slot || slot.gpId == null) return;
            if (!fighterByGp.has(slot.gpId)) {
                const name = `${slot.firstName || ''} ${slot.lastName || ''}`.trim() || `#${slot.gpId}`;
                fighterByGp.set(slot.gpId, { gpId: slot.gpId, name, club: slot.club || '' });
            }
        };
        poolFights.forEach(m => { collect(m.p1); collect(m.p2); });

        // Base order by name, then solve the pool slot order so the bout columns
        // follow the canonical schedule (= the order on the Excel sheet).
        const baseGpIds = [...fighterByGp.keys()].sort(
            (a, b) => fighterByGp.get(a).name.localeCompare(fighterByGp.get(b).name, 'de'));
        const slotGpIds = this.solvePoolSlots(baseGpIds, poolFights);
        const fighters = slotGpIds.map(gp => fighterByGp.get(gp));
        const n = fighters.length;
        const schedule = this.poolFightSchedule(n);

        // Resolve each scheduled bout to a concrete fight. Best-of-three shares the
        // pair, so consume the pair's fights in fightNr order (one per column).
        const pairKey = (a, b) => [a, b].sort((x, y) => x - y).join('|');
        const fightsByPair = new Map();
        poolFights.forEach(m => {
            if (m.p1?.gpId == null || m.p2?.gpId == null) return;
            const k = pairKey(m.p1.gpId, m.p2.gpId);
            if (!fightsByPair.has(k)) fightsByPair.set(k, []);
            fightsByPair.get(k).push(m);
        });
        fightsByPair.forEach(list => list.sort((a, b) => (a.fightNr ?? a.matchId) - (b.fightNr ?? b.matchId)));
        const consumed = new Map();
        const bouts = schedule.map(([a, b]) => {
            const k = pairKey(slotGpIds[a], slotGpIds[b]);
            const list = fightsByPair.get(k) || [];
            const idx = consumed.get(k) || 0;
            consumed.set(k, idx + 1);
            return { slotA: a, slotB: b, fight: list[idx] || null };
        });

        // Live-Status je Bout-Spalte: aktueller Kampf (gold) bzw. in Vorbereitung
        // (rot). Im Pool wird die GANZE Spalte umrandet (kein Einzel-Kachel-Rand) —
        // gleiche Logik wie der Tree-Rand (currentMatchId / computeNextUpIds).
        const nextUpIds = this.computeNextUpIds();
        const boutHL = bouts.map(b => {
            if (!b.fight) return null;
            if (State.currentMatchId === b.fight.matchId) return 'current';
            if (nextUpIds.has(b.fight.matchId)) return 'next';
            return null;
        });

        const wrapper = document.createElement('div');
        wrapper.className = 'pool-table-wrapper';

        const title = document.createElement('h3');
        title.className = 'pool-section-title';
        title.textContent = `Pool ${poolIndex + 1}`;
        wrapper.appendChild(title);

        const table = document.createElement('table');
        table.className = 'pool-table';

        // Header: "" + one column per bout, numbered 1..k (run order).
        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        const corner = document.createElement('th');
        corner.className = 'pool-header-cell';
        headRow.appendChild(corner);
        bouts.forEach((bout, i) => {
            const th = document.createElement('th');
            th.className = 'pool-header-cell pool-bout-header';
            const fa = fighters[bout.slotA], fb = fighters[bout.slotB];
            // Top: tournament-wide fight number (same as list/tree); fall back to the
            // in-pool run order if the fight isn't created yet. Below: the mat-rank
            // ("Matte X, Kampf N") so you can see when/where the bout is due.
            const fNum = bout.fight ? bout.fight.fightNr : (i + 1);
            const rank = bout.fight ? rankByMatch.get(bout.fight.matchId) : null;
            const matLine = (bout.fight && rank && bout.fight.tableId != null)
                ? `<span class="pool-bout-mat">M${bout.fight.tableId}·${rank}</span>` : '';
            th.innerHTML = `<span class="pool-bout-num">${fNum}</span>${matLine}`;
            th.title = `Kampf-Nr. ${bout.fight ? bout.fight.fightNr : '—'} · Reihenfolge ${i + 1}`
                + `${rank ? ` · Matte ${bout.fight.tableId}, Kampf ${rank}` : ''}`
                + `: ${fa?.name || '?'} vs ${fb?.name || '?'}`;
            if (boutHL[i]) th.classList.add(`pool-col--${boutHL[i]}`, 'pool-col-top');
            headRow.appendChild(th);
        });
        thead.appendChild(headRow);
        table.appendChild(thead);

        // One row per fighter; a bout column carries the fighter's own score cell
        // when they are in that bout, otherwise it is blocked (mirrors the shading).
        const tbody = document.createElement('tbody');
        fighters.forEach((rowFighter, slot) => {
            const tr = document.createElement('tr');
            const rowHeader = document.createElement('th');
            rowHeader.className = 'pool-header-cell pool-row-header';
            rowHeader.title = rowFighter.club ? `${rowFighter.name} (${rowFighter.club})` : rowFighter.name;
            rowHeader.textContent = rowFighter.name;
            tr.appendChild(rowHeader);

            const isLastRow = slot === fighters.length - 1;
            // Tag a cell with its column's live-status border (left+right on every
            // row; the header carries the top edge, the last row the bottom edge).
            const applyCol = (el, i) => {
                if (!boutHL[i]) return;
                el.classList.add(`pool-col--${boutHL[i]}`);
                if (isLastRow) el.classList.add('pool-col-bottom');
            };
            bouts.forEach((bout, i) => {
                const td = document.createElement('td');
                const inBout = bout.slotA === slot || bout.slotB === slot;
                if (!inBout) {
                    td.className = 'pool-cell pool-cell--blocked';
                    applyCol(td, i);
                    tr.appendChild(td);
                    return;
                }
                td.className = 'pool-cell pool-cell--match';
                const fight = bout.fight;
                if (!fight) {
                    td.textContent = '·';
                    td.classList.add('pool-cell--missing');
                    applyCol(td, i);
                    tr.appendChild(td);
                    return;
                }
                const mySide = fight.p1?.gpId === rowFighter.gpId ? fight.p1 : fight.p2;
                const oppName = (fight.p1?.gpId === rowFighter.gpId ? fight.p2 : fight.p1)?.lastName || '?';
                const isFinished = fight.status === 'finished';
                const myPts = mySide?.score?.points ?? 0;
                if (isFinished) {
                    td.textContent = myPts;
                    if (fight.winnerId === rowFighter.gpId) td.classList.add('pool-cell--row-won');
                    else if (fight.winnerId != null) td.classList.add('pool-cell--col-won');
                } else {
                    td.textContent = fight.status === 'live' ? '…' : '';
                }
                td.title = `Kampf-Nr. ${fight.fightNr} (Reihenfolge ${i + 1}) — ${rowFighter.name} vs ${oppName}`;
                // Same action menu as the KO tree nodes: senden / als nächster /
                // Ergebnis (state-gated in showFightActionMenu), instead of jumping
                // straight to the result dialog — so pool fights can be pushed too.
                td.classList.add('clickable');
                td.onclick = (e) => UI.showFightActionMenu(fight.matchId, e);
                applyCol(td, i);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        wrapper.appendChild(table);

        return wrapper;
    },


    renderPoolKoCard(m) {
        const card = document.createElement('div');
        const isFinished = m.status === 'finished';
        const ready = m.p1.gpId != null && m.p2.gpId != null;
        card.className = `pool-ko-card${ready ? ' clickable' : ' not-ready'}`;
        if (ready) card.onclick = () => openResultDialog(m.matchId);
        const p1Name = `${m.p1.firstName || ''} ${m.p1.lastName || ''}`.trim() || 'TBD';
        const p2Name = `${m.p2.firstName || ''} ${m.p2.lastName || ''}`.trim() || 'TBD';
        const p1S = m.p1.score?.points ?? 0;
        const p2S = m.p2.score?.points ?? 0;
        const p1Won = isFinished && m.winnerId === m.p1.gpId;
        const p2Won = isFinished && m.winnerId === m.p2.gpId;
        card.innerHTML = `
            <div class="pool-ko-header">#${m.fightNr}${m.phase === 'lb' ? ' · LB' : ''}</div>
            <div class="pool-ko-row${p1Won ? ' winner' : ''}"><span>${p1Name}</span><span>${p1S}</span></div>
            <div class="pool-ko-row${p2Won ? ' winner' : ''}"><span>${p2Name}</span><span>${p2S}</span></div>
        `;
        return card;
    },

    setupDragAndDrop(card, matchId) {
        card.setAttribute('draggable', 'true');
        card.ondragstart = (e) => {
            State.draggedMatchId = matchId;
            card.classList.add('dragging');
        };
        card.ondragend = () => {
            card.classList.remove('dragging');
            document.querySelectorAll('.drag-over-list').forEach(el => el.classList.remove('drag-over-list'));
        };
        card.ondragover = (e) => { e.preventDefault(); card.classList.add('drag-over-list'); };
        card.ondragleave = () => card.classList.remove('drag-over-list');
        card.ondrop = (e) => {
            e.preventDefault();
            card.classList.remove('drag-over-list');
            if (State.draggedMatchId === matchId) return;

            const list = State.activeMatches;
            const fromIdx = list.findIndex(m => m.matchId === State.draggedMatchId);
            const toIdx = list.findIndex(m => m.matchId === matchId);

            if (fromIdx !== -1 && toIdx !== -1) {
                const [movedItem] = list.splice(fromIdx, 1);
                list.splice(toIdx, 0, movedItem);
                // Assign new global order to preserve the user's manual sequence
                list.forEach((m, i) => m.order = i + 1);

                // Sync to backend
                const orders = {};
                list.forEach(m => orders[m.matchId] = m.order);
                Network.send({ type: 'REORDER', orders });

                this.renderFightList();
            }
        };
    },

    updateScoreDisplay() {
        if (!State.currentScoringMatch) return;
        const { p1, p2 } = State.currentScoringMatch;
        document.getElementById('p1-points-display').textContent = p1.score.points || 0;
        document.getElementById('p2-points-display').textContent = p2.score.points || 0;
        document.getElementById('undo-btn').disabled = State.scoreHistory.length === 0;
    },

    displayError(msg) {
        document.getElementById('fight-list').innerHTML = `<div class="status-msg" style="color: var(--error-color)">${msg}</div>`;
    }
};

// --- SCORING & LOGIC ---
const Scoring = {
    openModal(matchId) {
        const m = State.activeMatches.find(m => m.matchId === matchId);
        if (!m) return;

        if (m.status !== 'finished' && m.restSeconds > 0) {
            // Confirm with the user before starting a match that hasn't finished its rest period
            const confirmOverride = confirm("Die Ruhezeit für diesen Kampf ist noch nicht abgelaufen. Möchten Sie den Kampf trotzdem starten?");
            if (!confirmOverride) return;
        }

        State.currentScoringMatch = m;
        State.scoreHistory = [];
        this.resetTimer();

        // Ensure victory popup is hidden when reopening
        const overlay = document.getElementById('victory-overlay');
        if (overlay) overlay.style.display = 'none';

        document.getElementById('p1-name').textContent = `${m.p1.firstName} ${m.p1.lastName}`;
        document.getElementById('p2-name').textContent = `${m.p2.firstName} ${m.p2.lastName}`;
        document.getElementById('modal-category').textContent = m.category;

        const timerBtn = document.getElementById('timer-toggle-btn');
        timerBtn.disabled = m.restSeconds > 0;

        UI.updateScoreDisplay();
        document.getElementById('scoring-modal').style.display = 'flex';
    },

    closeModal() {
        this.stopTimer();
        document.getElementById('scoring-modal').style.display = 'none';
        State.currentScoringMatch = null;
    },

    addPoints(playerNum, pointsValue) {
        if (!State.currentScoringMatch) return;
        const player = playerNum === 1 ? State.currentScoringMatch.p1 : State.currentScoringMatch.p2;
        const type = 'points';

        if (typeof player.score[type] === 'undefined') {
            player.score[type] = 0;
        }

        State.scoreHistory.push({ playerNum, type, prevValue: player.score[type] });
        player.score[type] += pointsValue;

        Network.send({
            type: 'SCORE_UPDATE',
            matchId: State.currentScoringMatch.matchId,
            playerNum,
            scoreType: type,
            value: player.score[type]
        });
        UI.updateScoreDisplay();

        // Auto win condition
        if (player.score[type] >= 10) {
            winByDecision(playerNum);
        }
    },

    undo() {
        if (State.scoreHistory.length === 0) return;
        const last = State.scoreHistory.pop();
        const player = last.playerNum === 1 ? State.currentScoringMatch.p1 : State.currentScoringMatch.p2;
        player.score[last.type] = last.prevValue;

        Network.send({
            type: 'SCORE_UPDATE',
            matchId: State.currentScoringMatch.matchId,
            playerNum: last.playerNum,
            scoreType: last.type,
            value: player.score[last.type]
        });
        UI.updateScoreDisplay();
    },

    toggleTimer() {
        if (State.timer.isRunning) { this.stopTimer(); this.triggerTimerSignal('mate'); }
        else { this.startTimer(); this.triggerTimerSignal('hajime'); }
    },

    startTimer() {
        if (State.timer.isRunning) return;
        State.timer.isRunning = true;
        const btn = document.getElementById('timer-toggle-btn');
        btn.textContent = 'Stop'; btn.className = 'timer-btn stop';
        State.timer.interval = setInterval(() => {
            if (State.timer.remainingSeconds > 0) {
                State.timer.remainingSeconds--;
                this.updateTimerUI();
            } else {
                this.stopTimer(); this.triggerTimerSignal('mate'); alert('Zeit abgelaufen!');
            }
        }, 1000);
    },

    stopTimer() {
        State.timer.isRunning = false;
        clearInterval(State.timer.interval);
        const btn = document.getElementById('timer-toggle-btn');
        if (btn) { btn.textContent = 'Start'; btn.className = 'timer-btn start'; }
    },

    resetTimer() {
        this.stopTimer(); State.timer.remainingSeconds = 240; this.updateTimerUI();
    },

    updateTimerUI() {
        document.getElementById('match-timer').textContent = UI.formatTime(State.timer.remainingSeconds);
    },

    triggerTimerSignal(type, shouldBroadcast = true) {
        const modal = document.querySelector('.modal-content');
        if (!modal) return;
        const cls = type === 'hajime' ? 'hajime-flash' : 'mate-flash';
        modal.classList.add(cls);
        setTimeout(() => modal.classList.remove(cls), 500);
        this.playBeep(type === 'hajime' ? 880 : 440);
        if (shouldBroadcast) Network.send({ type: 'SIGNAL', signalType: type });
    },

    playBeep(freq) {
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.connect(gain); gain.connect(ctx.destination);
            osc.type = 'sine'; osc.frequency.value = freq;
            gain.gain.setValueAtTime(0.1, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.2);
            osc.start(); osc.stop(ctx.currentTime + 0.2);
        } catch (e) { }
    },

    finishMatch() {
        if (!State.currentScoringMatch) return;
        State.currentScoringMatch.status = 'finished';
        Network.send({ type: 'STATUS_UPDATE', matchId: State.currentScoringMatch.matchId, status: 'finished' });
        this.closeModal();
        UI.renderFightList();
    }
};

// --- AUTH & INITIALIZATION ---
const App = {
    init() {
        this.checkAuth();
        this.setupEventListeners();
        Network.fetchMatches().then(() => {
            UI.renderFightList();
            UI.updateBracketSidebar();
        });
        Network.initWebSocket();
        this.startRestTimer();
        if (!Config.DEV_MODE) this.registerSW();
    },

    checkAuth() {
        const table = localStorage.getItem('assignedTable');
        if (table) {
            document.getElementById('login-screen').style.display = 'none';
            document.getElementById('app-content').style.display = 'block';
            document.getElementById('table-select').value = table;
        }
    },

    handleTableSelection() {
        const table = document.getElementById('login-table').value;
        localStorage.setItem('assignedTable', table);
        location.reload();
    },

    handleLogout() {
        localStorage.clear();
        location.reload();
    },

    setupEventListeners() {
        document.getElementById('my-table-filter').onchange = (e) => {
            State.isTableFilterActive = e.target.checked;
            UI.renderFightList();
            UI.updateBracketSidebar();
        };


        const autoToggle = document.getElementById('auto-send-toggle');
        if (autoToggle) {
            State.autoSendEnabled = localStorage.getItem('autoSendEnabled') === 'true';
            autoToggle.checked = State.autoSendEnabled;
            autoToggle.onchange = (e) => {
                State.autoSendEnabled = e.target.checked;
                localStorage.setItem('autoSendEnabled', String(State.autoSendEnabled));
            };
        }

        const hideDoneToggle = document.getElementById('hide-completed-toggle');
        if (hideDoneToggle) {
            State.hideCompletedLists = localStorage.getItem('hideCompletedLists') === 'true';
            hideDoneToggle.checked = State.hideCompletedLists;
            hideDoneToggle.onchange = (e) => {
                State.hideCompletedLists = e.target.checked;
                localStorage.setItem('hideCompletedLists', String(State.hideCompletedLists));
                UI.renderFightList();
                UI.updateBracketSidebar();
            };
        }

        const savedNext = localStorage.getItem('nextUpMatchId');
        if (savedNext) State.nextUpMatchId = parseInt(savedNext, 10) || null;
    },

    startRestTimer() {
        State.restTimerInterval = setInterval(() => {
            State.activeMatches.forEach(m => {
                if (m.status !== 'finished' && m.restSeconds > 0) {
                    m.restSeconds--;
                    const el = document.getElementById(`rest-timer-${m.matchId}`);
                    if (el) el.textContent = UI.formatTime(m.restSeconds);
                    if (m.restSeconds === 0) UI.renderFightList();
                }
            });
        }, 1000);
    },

    registerSW() {
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('sw.js').catch(console.error);
        }
    }
};

document.addEventListener('DOMContentLoaded', () => App.init());

// Global exposed functions for inline HTML handlers
window.switchTable = function (tableId) {
    if (tableId === 'admin') {
        const filterToggle = document.getElementById('my-table-filter');
        if (filterToggle) {
            filterToggle.checked = false;
            State.isTableFilterActive = false;
        }
    }
    // Tisch-IP-Admin-Button nur im Admin-Modus zeigen
    const matIpsBtn = document.getElementById('mat-ips-btn');
    if (matIpsBtn) matIpsBtn.style.display = (tableId === 'admin') ? '' : 'none';
    UI.renderFightList();
    UI.updateBracketSidebar();
};

// --- Tisch-IP / Ipponboard-Routing (Admin) -------------------------------- #
window.openMatIpDialog = async function () {
    const rows = document.getElementById('mat-ip-rows');
    const status = document.getElementById('mat-ip-status');
    status.textContent = '';
    rows.innerHTML = 'Lade…';
    try {
        const resp = await fetch(`${Config.API_BASE}/api/ipponboard-mats`);
        const data = await resp.json();
        const mats = data.mats || {};
        // Immer Tisch 1-4 anbieten (= table-select-Dropdown), plus etwaige höhere
        // table_ids aus Fights/Map. Union, sortiert (numerisch-stabil).
        const tables = [...new Set(['1', '2', '3', '4', ...(data.tables || []), ...Object.keys(mats)])]
            .sort((a, b) => (a.length - b.length) || a.localeCompare(b));
        rows.innerHTML = tables.map(t =>
            `<div class="mat-ip-row" style="display:flex;align-items:center;gap:8px;margin:6px 0;">
                <label style="min-width:84px;color:#fff;">Tisch ${t}</label>
                <input type="text" class="mat-ip-input" data-table="${t}"
                    value="${(mats[t] || '').replace(/"/g, '&quot;')}"
                    placeholder="z.B. 192.168.0.79:8080"
                    style="flex:1;padding:6px;color:#fff;">
            </div>`).join('');
        status.textContent = `Fallback (nicht zugeordnet): ${data.fallback || '—'}`;
        document.getElementById('mat-ip-modal').style.display = 'flex';
    } catch (e) {
        rows.innerHTML = `<p class="error-msg">Konnte Tisch-IPs nicht laden: ${e}</p>`;
        document.getElementById('mat-ip-modal').style.display = 'flex';
    }
};

window.saveMatIpDialog = async function () {
    const status = document.getElementById('mat-ip-status');
    const mats = {};
    document.querySelectorAll('#mat-ip-rows .mat-ip-input').forEach(inp => {
        const v = inp.value.trim();
        if (v) mats[inp.dataset.table] = v;   // leere Felder = Tisch nicht zugeordnet (Fallback)
    });
    status.textContent = 'Speichere…';
    try {
        const resp = await fetch(`${Config.API_BASE}/api/ipponboard-mats`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mats })
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            status.textContent = `Fehler: ${err.detail || resp.statusText}`;
            return;
        }
        closeMatIpDialog();
    } catch (e) {
        status.textContent = `Fehler: ${e}`;
    }
};

window.closeMatIpDialog = function () {
    document.getElementById('mat-ip-modal').style.display = 'none';
};

window.winByDecision = function (playerNum) {
    if (!State.currentScoringMatch) return;
    const player = playerNum === 1 ? State.currentScoringMatch.p1 : State.currentScoringMatch.p2;
    const currentPoints = player.score.points || 0;

    // Ensure the winner has at least 10 points to satisfy auto-progression condition
    if (currentPoints < 10) {
        const type = 'points';
        State.scoreHistory.push({ playerNum, type, prevValue: currentPoints });
        player.score[type] = 10;

        Network.send({
            type: 'SCORE_UPDATE',
            matchId: State.currentScoringMatch.matchId,
            playerNum,
            scoreType: type,
            value: player.score[type]
        });
        UI.updateScoreDisplay();
    }


    UI.showVictoryPopup(State.currentScoringMatch, playerNum === 1 ? 'p1' : 'p2');
};

window.markWinner = function (matchId, playerNum) {
    const loserNum = playerNum === 1 ? 2 : 1;
    Network.send({ type: 'SCORE_UPDATE', matchId, playerNum, scoreType: 'points', value: 1 });
    Network.send({ type: 'SCORE_UPDATE', matchId, playerNum: loserNum, scoreType: 'points', value: 0 });
    Network.send({ type: 'STATUS_UPDATE', matchId, status: 'finished' });
};

window.markDraw = function (matchId) {
    Network.send({ type: 'SCORE_UPDATE', matchId, playerNum: 1, scoreType: 'points', value: 0 });
    Network.send({ type: 'SCORE_UPDATE', matchId, playerNum: 2, scoreType: 'points', value: 0 });
    Network.send({ type: 'STATUS_UPDATE', matchId, status: 'finished' });
};

let currentResultMatchId = null;

window.openResultDialog = function (matchId) {
    const m = State.activeMatches.find(x => x.matchId === matchId);
    if (!m) return;
    currentResultMatchId = matchId;
    document.getElementById('result-modal-subtitle').textContent =
        `${m.p1.firstName} ${m.p1.lastName} (${m.p1.club}) vs ${m.p2.firstName} ${m.p2.lastName} (${m.p2.club})`;
    document.getElementById('result-btn-p1').textContent = `Sieger ${m.p1.firstName} ${m.p1.lastName}`;
    document.getElementById('result-btn-p2').textContent = `Sieger ${m.p2.firstName} ${m.p2.lastName}`;
    document.getElementById('result-modal').style.display = 'flex';
};

window.closeResultDialog = function () {
    document.getElementById('result-modal').style.display = 'none';
    currentResultMatchId = null;
};

window.confirmResult = function (kind) {
    if (currentResultMatchId === null) return;
    // Capture match before closing the dialog (which nulls currentResultMatchId).
    const matchObj = State.activeMatches.find(x => x.matchId === currentResultMatchId);

    if (kind === 'p1') markWinner(currentResultMatchId, 1);
    else if (kind === 'p2') markWinner(currentResultMatchId, 2);
    else markDraw(currentResultMatchId);
    closeResultDialog();

    // Show victory overlay with Ipponboard colors. Build a synthetic match
    // reflecting the just-sent scores (the WS broadcast will catch up shortly,
    // but the overlay should not flicker).
    if (matchObj) {
        const synthetic = {
            p1: { ...matchObj.p1, score: { points: kind === 'p1' ? 1 : 0 } },
            p2: { ...matchObj.p2, score: { points: kind === 'p2' ? 1 : 0 } },
        };
        UI.showVictoryPopup(synthetic, kind === 'draw' ? 'draw' : kind);
    }
};

window.reopenMatch = async function (matchId) {
    if (!confirm('Diesen Kampf wirklich erneut starten?\nDas bisherige Ergebnis wird zurückgesetzt.')) return;
    try {
        const resp = await fetch(`${Config.API_BASE}/api/reopen-match/${matchId}`, { method: 'POST' });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            alert(`Fehler: ${err.detail || resp.statusText}`);
        }
    } catch (e) {
        alert(`Verbindungsfehler: ${e.message}`);
    }
};

window.markAsNext = function (matchId) {
    // Toggle off: clear the manual marker; the mat's default (Kampf 2) takes over again.
    if (State.nextUpMatchId === matchId) {
        State.nextUpMatchId = null;
        localStorage.removeItem('nextUpMatchId');
        UI.renderFightList();
        UI.renderBracketVisualization(); // goldene "in Vorbereitung"-Umrandung mitziehen
        return;
    }
    // Manual pick: mark it AND pull it up to position 2 (Kampf 2) of its mat,
    // so the "als nächstes" marker and the running order stay consistent.
    State.nextUpMatchId = matchId;
    localStorage.setItem('nextUpMatchId', String(matchId));
    UI.moveMatchToSecondOnMat(matchId);
    UI.renderBracketVisualization(); // goldene "in Vorbereitung"-Umrandung mitziehen
};

window.sendToIpponboard = async function (matchId) {
    try {
        const resp = await fetch(`${Config.API_BASE}/api/push-to-ipponboard/${matchId}`, { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) {
            alert(`Ipponboard-Fehler: ${data.detail || resp.statusText}`);
            return;
        }
        const p = data.payload || {};
        const f1 = p.fighter1 || {};
        const f2 = p.fighter2 || {};
        alert(`An Ipponboard gesendet:\n${f1.firstname} ${f1.lastname} vs ${f2.firstname} ${f2.lastname}\n(${f1.gender}${f1.agegroup} ${f1.weightclass})`);
    } catch (e) {
        alert(`Verbindungsfehler: ${e.message}`);
    }
};
