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
    draggedMatchId: null,
    isTableFilterActive: true,
    scoreHistory: [],
    timer: {
        interval: null,
        remainingSeconds: 240,
        isRunning: false
    },
    restTimerInterval: null,
    poolStandings: {},  // bracketId -> standings array
    bracketViewMode: 'live',   // 'live' | 'finished'
    bracketPhaseView: 'wb',    // 'wb'  | 'lb'
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
            UI.updateTournamentTitle(data.tournamentName);
            UI.autoInterleaveMatches(); // Set initial order
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
                State.activeMatches[idx] = { ...data.match, restSeconds: State.activeMatches[idx].restSeconds };
                if (State.currentScoringMatch?.matchId === data.matchId) {
                    State.currentScoringMatch = State.activeMatches[idx];
                    UI.updateScoreDisplay();
                }
                UI.renderFightList();
                UI.renderBracketVisualization();
            }
        } else if (data.type === 'POOL_STANDINGS') {
            State.poolStandings[data.bracketId] = data.standings;
            if (State.currentBracketCategory === data.bracketId) {
                UI.renderBracketVisualization();
            }
        } else if (data.type === 'IPPON_UPDATE') {
            const m = State.currentScoringMatch;
            if (m && m.matchId === data.matchId && data.data) {
                const d = data.data;

                // Board sends fighter1/fighter2 objects
                const s1 = d.fighter1 ?? d.p1 ?? d.blue ?? null;
                const s2 = d.fighter2 ?? d.p2 ?? d.white ?? null;

                // Convert judo scores to points (0/7/10)
                // 3 shidos = hansoku-make → opponent gets 10
                const judoToPoints = (s) => {
                    if (!s) return 0;
                    const ippon  = Number(s.ippon   ?? 0);
                    const waza   = Number(s.wazaari ?? s.waza_ari ?? 0);
                    const yuko   = Number(s.yuko    ?? 0);
                    const shido  = Number(s.shido   ?? 0);
                    if (ippon >= 1 || waza >= 2) return 10;
                    if (shido >= 3) return -1; // hansoku-make: this fighter loses
                    if (waza >= 1) return 7;
                    if (yuko >= 1) return 5;
                    return 0;
                };

                let pts1 = s1 ? judoToPoints(s1) : null;
                let pts2 = s2 ? judoToPoints(s2) : null;

                // Hansoku-make: if one fighter has -1, opponent gets 10
                if (pts1 === -1) { pts1 = 0; pts2 = 10; }
                if (pts2 === -1) { pts2 = 0; pts1 = 10; }

                if (pts1 !== null) {
                    m.p1.score = { points: pts1 };
                    Network.send({ type: 'SCORE_UPDATE', matchId: m.matchId, playerNum: 1, value: pts1 });
                }
                if (pts2 !== null) {
                    m.p2.score = { points: pts2 };
                    Network.send({ type: 'SCORE_UPDATE', matchId: m.matchId, playerNum: 2, value: pts2 });
                }

                // Parse time string "3:45" → seconds
                if (d.time !== undefined) {
                    const t = String(d.time);
                    if (t.includes(':')) {
                        const [min, sec] = t.split(':').map(Number);
                        Scoring.setTimerSeconds(min * 60 + sec);
                    } else {
                        Scoring.setTimerSeconds(Number(t));
                    }
                }

                UI.updateScoreDisplay();
            }
        } else if (data.type === 'SIGNAL') {
            Scoring.triggerTimerSignal(data.signalType, false);
        } else if (data.type === 'REFRESH_LIST') {
            Network.fetchMatches().then(() => {
                UI.renderFightList();
                UI.renderBracketVisualization(); // Refresh bracket as well
            });
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
        const isBrackets = view === 'brackets';

        document.getElementById('fights-view').style.display = isFights ? 'block' : 'none';
        document.getElementById('brackets-view').style.display = isBrackets ? 'flex' : 'none';

        if (isBrackets) this.updateBracketSidebar();
    },

    switchToBrackets(mode) {
        State.bracketViewMode = mode;
        State.bracketPhaseView = 'wb';
        State.currentBracketCategory = null;
        this.switchView('brackets');
    },

    setPhaseView(phase) {
        State.bracketPhaseView = phase;
        document.getElementById('phase-btn-wb')?.classList.toggle('active', phase === 'wb');
        document.getElementById('phase-btn-lb')?.classList.toggle('active', phase === 'lb');
        this.renderBracketVisualization();
    },


    updateTournamentTitle(title) {
        document.getElementById('tournament-title').textContent = title;
    },

    showVictoryPopup(winnerName) {
        document.getElementById('victory-subtitle').textContent = `${winnerName} hat den Kampf gewonnen!`;
        const overlay = document.getElementById('victory-overlay');
        const titleEl = overlay.querySelector('h2');
        if (titleEl) titleEl.textContent = 'KAMPF BEENDET!';
        const closeBtn = overlay.querySelector('.victory-close-btn');
        if (closeBtn) closeBtn.textContent = 'SCHLIESSEN';
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

        container.innerHTML = '';

        let displayMatches = [...State.activeMatches];

        // Hide finished and bye fights — they are done, no longer actionable
        displayMatches = displayMatches.filter(m => m.status !== 'finished' && m.status !== 'bye');

        // Apply table filter if active AND we are not admin
        if (State.isTableFilterActive && tableNum !== 'admin') {
            displayMatches = displayMatches.filter(m => String(m.tableId) === String(tableNum));
        }

        // Sort by order/fight number
        displayMatches.sort((a, b) => (a.order || 0) - (b.order || 0));
        // Identify the true "Next" match per table (only the first 'upcoming' per tableId)
        const nextMatchByTable = new Map();
        [...State.activeMatches].sort((a, b) => a.order - b.order).forEach(m => {
            if ((m.status === 'upcoming' || m.status === 'pending') && !nextMatchByTable.has(m.tableId)) {
                nextMatchByTable.set(m.tableId, m.matchId);
            }
        });
        const nextMatchIds = new Set(nextMatchByTable.values());

        const isAdminMode = tableNum === 'admin';
        displayMatches.forEach(m => container.appendChild(this.createFightCard(m, assignedTable, nextMatchIds, isAdminMode)));
        document.getElementById('match-count').textContent = `${displayMatches.length} Kämpfe angezeigt`;

        if (isAdminMode) {
            this.renderAdminDashboard();
        }
    },

    renderAdminDashboard() {
        const cols = {
            "1": document.getElementById('admin-col-1'),
            "2": document.getElementById('admin-col-2'),
            "3": document.getElementById('admin-col-3'),
            "4": document.getElementById('admin-col-4'),
            "none": document.getElementById('admin-col-none')
        };
        Object.values(cols).forEach(col => { if (col) col.innerHTML = ''; });

        const groups = {};
        State.activeMatches.forEach(m => {
            const bId = m.bracketId || m.category; // fallback to category string if no ID
            if (!groups[bId]) {
                groups[bId] = {
                    title: m.category,
                    tableId: m.tableId || 'none',
                    total: 0,
                    finished: 0,
                    bracketId: bId
                };
            }
            groups[bId].total++;
            if (m.status === 'finished' || m.status === 'bye') groups[bId].finished++;

            // Re-assign table if a later match in the same bracket has a valid table
            if (m.tableId && String(m.tableId) !== "0") {
                groups[bId].tableId = m.tableId;
            }
        });

        Object.values(groups).forEach(g => {
            const tableKey = (g.tableId && g.tableId !== "0") ? String(g.tableId) : "none";
            const col = cols[tableKey] || cols["none"];
            if (!col) return;

            const isDone = g.total > 0 && g.finished === g.total;
            const pct = g.total > 0 ? (g.finished / g.total) * 100 : 0;
            const colorClass = isDone ? 'var(--success-color)' : `var(--table-${tableKey === 'none' ? 'muted' : tableKey})`;

            const card = document.createElement('div');
            card.className = 'bracket-card';
            card.draggable = true;
            card.dataset.bracketId = g.bracketId;
            if (tableKey !== 'none') card.style.borderLeftColor = colorClass;

            card.innerHTML = `
                <div class="bracket-title">${g.title}</div>
                <div class="bracket-stats">
                    <span>Fortschritt</span>
                    <span>${g.finished} / ${g.total} ${isDone ? '(Fertig)' : ''}</span>
                </div>
                <div class="progress-bar-bg"><div class="progress-bar-fill" style="width: ${pct}%; background-color: ${colorClass};"></div></div>
            `;

            // Click to open detailed bracket tree
            card.onclick = () => {
                State.currentBracketCategory = g.title;
                UI.switchView('brackets');
                UI.renderBracketVisualization();
            };

            // Drag logic
            card.addEventListener('dragstart', (e) => {
                e.dataTransfer.setData('text/plain', g.bracketId);
                setTimeout(() => card.classList.add('dragging'), 0);
            });
            card.addEventListener('dragend', () => {
                card.classList.remove('dragging');
                document.querySelectorAll('.kanban-content').forEach(c => c.classList.remove('drag-over'));
            });

            col.appendChild(card);
        });

        this.setupAdminDropZones();
    },

    setupAdminDropZones() {
        document.querySelectorAll('.kanban-content').forEach(column => {
            // Remove old listeners to prevent stacking
            const newCol = column.cloneNode(true);
            column.parentNode.replaceChild(newCol, column);

            newCol.addEventListener('dragover', e => {
                e.preventDefault();
                newCol.classList.add('drag-over');
            });
            newCol.addEventListener('dragleave', () => {
                newCol.classList.remove('drag-over');
            });
            newCol.addEventListener('drop', e => {
                e.preventDefault();
                newCol.classList.remove('drag-over');
                const bracketId = e.dataTransfer.getData('text/plain');
                let newTableId = newCol.dataset.table;
                if (newTableId === 'none') newTableId = "0";

                console.log(`Reassigning bracket ${bracketId} to table ${newTableId}`);
                Network.send({
                    type: "REASSIGN_BRACKET",
                    bracketId: bracketId,
                    newTableId: newTableId
                });
            });
        });
    },

    autoInterleaveMatches() {
        const matches = State.activeMatches;
        const finished = matches.filter(m => m.status === 'finished' || m.status === 'bye').sort((a, b) => a.order - b.order);
        const upcoming = matches.filter(m => m.status !== 'finished' && m.status !== 'bye');

        const males = upcoming.filter(m => m.category.toLowerCase().includes('maennlich')).sort((a, b) => a.matchId - b.matchId);
        const females = upcoming.filter(m => m.category.toLowerCase().includes('frauen') || m.category.toLowerCase().includes('weiblich')).sort((a, b) => a.matchId - b.matchId);
        const others = upcoming.filter(m => !m.category.toLowerCase().includes('maennlich') && !m.category.toLowerCase().includes('frauen') && !m.category.toLowerCase().includes('weiblich')).sort((a, b) => a.matchId - b.matchId);

        const interleaved = [];
        const maxLen = Math.max(males.length, females.length);
        for (let i = 0; i < maxLen; i++) {
            if (i < males.length) interleaved.push(males[i]);
            if (i < females.length) interleaved.push(females[i]);
        }

        const finalOrder = [...interleaved, ...others, ...finished];
        finalOrder.forEach((m, i) => m.order = i + 1);
        State.activeMatches = finalOrder;
    },

    createFightCard(match, assignedTable, nextMatchIds, isAdminMode) {
        let isReadOnly = assignedTable && String(match.tableId) !== String(assignedTable);
        if (isAdminMode) isReadOnly = false;
        const card = document.createElement('div');
        card.className = `fight-card ${match.status === 'live' ? 'active-match' : ''} ${isReadOnly ? 'read-only' : ''}`;
        card.dataset.matchId = match.matchId;

        const restTag = (match.status !== 'finished' && match.restSeconds > 0)
            ? `<div class="rest-tag" id="rest-timer-${match.matchId}">${this.formatTime(match.restSeconds)}</div>`
            : '';

        const isNextOnTable = nextMatchIds && nextMatchIds.has(match.matchId);
        const isUpcoming = match.status === 'upcoming' || match.status === 'pending';
        const statusLabel = isUpcoming ? (isNextOnTable ? 'NÄCHSTE' : 'WARTEND') :
            (match.status === 'live' ? 'LIVE' : (match.status === 'bye' ? 'FREILOS' : 'BEENDET'));

        card.innerHTML = `
            <div class="fight-nr-badge"><div class="fight-num-circle">${match.fightNr}</div></div>
            <div class="category-box">
                <span class="table-label">Tisch ${match.tableId}</span>
                <span class="category-name">${match.category}${match.phase === 'lb' ? ' <span class="lb-badge">(L)</span>' : ''}</span>
                <a href="#" class="bracket-link" onclick="UI.handleBracketClick(event, ${match.matchId})">Live-Turnierbaum</a>
            </div>
            <div class="fighters-display">
                ${match.status === 'bye' ? `
                    <div class="fighter p1" style="flex: 1; text-align: center;">
                        <span class="fighter-name">${match.p1.lastName} ${match.p1.firstName}</span>
                        <span class="fighter-club">${match.p1.club}</span>
                        <div style="margin-top: 10px; font-weight: 800; color: var(--accent-color); letter-spacing: 2px;">FREILOS</div>
                    </div>
                ` : `
                    <div class="fighter p1">
                        <span class="fighter-name">${match.p1.lastName}</span>
                        <span class="fighter-club">${match.p1.club}</span>
                    </div>
                    <div class="vs-divider">VS</div>
                    <div class="fighter p2">
                        <span class="fighter-name">${match.p2.lastName}</span>
                        <span class="fighter-club">${match.p2.club}</span>
                    </div>
                `}
            </div>
            <div class="status-box">
                <div class="status-badge ${match.status}">${statusLabel}</div>
                ${restTag}
            </div>
            <div class="action-area">
                ${(match.status !== 'finished' && match.status !== 'bye') ?
                `<button class="btn-start" ${isReadOnly ? 'disabled' : ''} onclick="event.stopPropagation(); Scoring.openModal(${match.matchId})">${match.status === 'live' ? 'WEITER' : 'START'}</button>` :
                `<span class="text-muted">${match.status === 'bye' ? 'FREILOS' : 'FERTIG'}</span>`}
            </div>
        `;

        if (!isReadOnly && match.status !== 'finished' && match.status !== 'bye') {
            card.onclick = () => Scoring.openModal(match.matchId);
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
        const titleEl = document.getElementById('bracket-sidebar-title');
        if (!list) return;
        list.innerHTML = '';

        const isCatFinished = (cat) => {
            const ms = State.activeMatches.filter(m => m.category === cat);
            return ms.length > 0 && ms.every(m => m.status === 'finished' || m.status === 'bye');
        };

        const allCats = [...new Set(State.activeMatches.map(m => m.category))];
        const isFinishedMode = State.bracketViewMode === 'finished';
        const categories = isFinishedMode ? allCats.filter(isCatFinished) : allCats;

        if (titleEl) titleEl.textContent = isFinishedMode ? 'Fertige Brackets' : 'Aktive Brackets';

        if (categories.length === 0 && isFinishedMode) {
            list.innerHTML = '<div style="color:var(--text-muted);font-size:0.8rem;padding:1rem 0;">Noch keine fertigen Brackets</div>';
        }

        categories.forEach(cat => {
            const done = isCatFinished(cat);
            const div = document.createElement('div');
            div.className = `category-item ${State.currentBracketCategory === cat ? 'active' : ''}`;
            div.innerHTML = `<span>${cat}</span>${done ? '<span class="cat-done-badge">&#10003;</span>' : ''}`;
            div.onclick = () => { State.currentBracketCategory = cat; this.renderBracketVisualization(); this.updateBracketSidebar(); };
            list.appendChild(div);
        });

        // Auto-select first if nothing selected
        if (!categories.includes(State.currentBracketCategory)) {
            State.currentBracketCategory = categories[0] ?? null;
            if (State.currentBracketCategory) this.renderBracketVisualization();
        }
    },

    // Generates virtual advancement rounds for WB brackets.
    // Computes the expected full structure from WB R0 count (ceil(log2(n)) total rounds),
    // then fills in virtual fights for any missing positions. This handles partially-progressed
    // brackets where some later rounds exist in the DB but others don't.
    _expandBracket(wbMatches) {
        if (wbMatches.length <= 1) return wbMatches;

        // Shallow-copy so we don't mutate State.activeMatches
        const result = wbMatches.map(m => ({ ...m }));
        const resultMap = new Map(result.map(m => [m.matchId, m]));

        const base = wbMatches[0];
        let virtualId = -1000;

        const getAdvancer = (m) => {
            if (!m) return null;
            const isBye = m.status === 'bye' ||
                (m.p1.id !== 'WAIT' && m.p2.id !== 'WAIT' && String(m.p1.id) === String(m.p2.id));
            if (isBye) return { ...m.p1, score: { points: 0 } };
            if (m.winnerId) {
                if (String(m.p1.id) === String(m.winnerId)) return { ...m.p1, score: { points: 0 } };
                if (String(m.p2.id) === String(m.winnerId)) return { ...m.p2, score: { points: 0 } };
            }
            return null;
        };
        const tbd = () => ({ id: 'WAIT', firstName: '', lastName: 'TBD', club: '', score: { points: 0 } });

        // Position lookup: "round-posInRound" → matchId (includes virtual fights as we add them)
        const posKey = (r, p) => `${r}-${p}`;
        const posLookup = new Map(result.map(m => [posKey(m.round, m.posInRound ?? 0), m.matchId]));

        const r0count = wbMatches.filter(m => m.round === 1).length;
        if (r0count < 2) return result;

        // Expected rounds: log2(r0count) + 1 (e.g. 8 R0 fights → 4 total rounds incl. finale)
        const totalRounds = Math.ceil(Math.log2(r0count)) + 1;

        for (let round = 2; round <= totalRounds; round++) {
            const currCount = Math.round(r0count / Math.pow(2, round - 1));
            for (let pos = 0; pos < currCount; pos++) {
                const key = posKey(round, pos);
                const c0mid = posLookup.get(posKey(round - 1, pos * 2));
                const c1mid = posLookup.get(posKey(round - 1, pos * 2 + 1));
                const c0 = c0mid != null ? resultMap.get(c0mid) : null;
                const c1 = c1mid != null ? resultMap.get(c1mid) : null;

                if (posLookup.has(key)) {
                    // Fight already in DB — just wire up any orphaned prev-round matches
                    const existingId = posLookup.get(key);
                    if (c0 && !c0.nextMatchId) { c0.nextMatchId = existingId; c0.nextMatchPos = 'p1'; }
                    if (c1 && !c1.nextMatchId) { c1.nextMatchId = existingId; c1.nextMatchPos = 'p2'; }
                    continue;
                }

                // Create virtual fight for this missing position
                const vm = {
                    matchId: virtualId--, bracketId: base.bracketId, category: base.category,
                    round, posInRound: pos, phase: 'wb', fightNr: null,
                    p1: getAdvancer(c0) ?? tbd(),
                    p2: getAdvancer(c1) ?? tbd(),
                    status: 'upcoming', order: 9999,
                    nextMatchId: null, nextMatchPos: null, winnerId: null, poolIndex: null,
                };
                if (c0 && !c0.nextMatchId) { c0.nextMatchId = vm.matchId; c0.nextMatchPos = 'p1'; }
                if (c1 && !c1.nextMatchId) { c1.nextMatchId = vm.matchId; c1.nextMatchPos = 'p2'; }
                result.push(vm);
                resultMap.set(vm.matchId, vm);
                posLookup.set(key, vm.matchId);
            }
        }

        return result;
    },

    // Builds the full LB structure from WB R0 count so the bracket never shrinks
    // as fights are completed.  Real DB fights are used where they exist; virtual
    // placeholder fights are created for rounds that haven't been played yet.
    // LB alternates: even DB rounds = reduction (same count), odd = injection (halve next).
    // DB round is 0-indexed; frontend round is 1-indexed (DB + 1).
    _expandLb(lbMatches, wbR0Count, fallbackBase) {
        const n = wbR0Count ?? 0;
        // Total LB rounds = 2*log2(n) - 1  (matches backend generate_lb_fights formula)
        const lbTotalRounds = n >= 2 ? (2 * Math.round(Math.log2(n)) - 1) : 0;
        if (lbTotalRounds < 1) return lbMatches;

        const base = lbMatches[0] ?? fallbackBase;
        if (!base) return lbMatches;

        // Lookup real LB fights by (frontendRound, posInRound)
        const realKey = (r, p) => `${r}:${p}`;
        const realFightMap = new Map();
        for (const m of lbMatches) realFightMap.set(realKey(m.round, m.posInRound ?? 0), m);

        const result = lbMatches.map(m => ({ ...m }));
        const resultMap = new Map(result.map(m => [m.matchId, m]));
        let virtualId = -2000;
        const tbd = () => ({ id: 'WAIT', firstName: '', lastName: 'TBD', club: '', score: { points: 0 } });

        // Build each LB round deterministically
        const roundFights = [];
        let fightCount = n / 2; // LB R0 (DB round 0) starts with N/2 fights

        for (let dbRound = 0; dbRound < lbTotalRounds; dbRound++) {
            const frontendRound = dbRound + 1;
            const roundNodes = [];

            for (let pos = 0; pos < fightCount; pos++) {
                const key = realKey(frontendRound, pos);
                let node;
                if (realFightMap.has(key)) {
                    node = resultMap.get(realFightMap.get(key).matchId);
                } else {
                    node = {
                        matchId: virtualId--, bracketId: base.bracketId, category: base.category,
                        round: frontendRound, posInRound: pos, phase: 'lb', fightNr: null,
                        p1: tbd(), p2: tbd(),
                        status: 'upcoming', order: 9999,
                        nextMatchId: null, nextMatchPos: null, winnerId: null, poolIndex: null,
                    };
                    result.push(node);
                    resultMap.set(node.matchId, node);
                }
                // Recompute next-match links below for structural consistency
                node.nextMatchId = null;
                node.nextMatchPos = null;
                roundNodes.push(node);
            }

            roundFights.push(roundNodes);

            // Advance fight count: injection round (odd) → next is reduction (halve)
            if (dbRound % 2 === 1) fightCount = Math.max(1, Math.floor(fightCount / 2));
            // Even round (reduction) → next is injection: count unchanged
        }

        // Wire up nextMatchId between adjacent rounds
        for (let i = 0; i < roundFights.length - 1; i++) {
            const current = roundFights[i];
            const next = roundFights[i + 1];
            if (i % 2 === 0) {
                // Reduction → injection: 1:1 same position, slot p1
                for (let j = 0; j < current.length; j++) {
                    if (j < next.length) { current[j].nextMatchId = next[j].matchId; current[j].nextMatchPos = 'p1'; }
                }
            } else {
                // Injection → reduction: pairs collapse (2:1)
                for (let j = 0; j < current.length; j++) {
                    const t = Math.floor(j / 2);
                    if (t < next.length) { current[j].nextMatchId = next[t].matchId; current[j].nextMatchPos = j % 2 === 0 ? 'p1' : 'p2'; }
                }
            }
        }

        return result;
    },

    renderBracketVisualization() {
        const viz = document.getElementById('bracket-visualization');
        const titleEl = document.getElementById('current-bracket-title');

        if (!viz || !State.currentBracketCategory) return;
        viz.innerHTML = '';
        if (titleEl) titleEl.textContent = State.currentBracketCategory;

        const allMatchesCategory = State.activeMatches.filter(m => m.category === State.currentBracketCategory);
        if (allMatchesCategory.length === 0) return;

        const isPool = allMatchesCategory.some(m => m.poolIndex !== null && m.poolIndex !== undefined);
        if (isPool) {
            this.renderPoolGrid(allMatchesCategory, viz);
            return;
        }

        const wbMatches = allMatchesCategory.filter(m => m.phase !== 'lb');
        let lbMatches = allMatchesCategory.filter(m => m.phase === 'lb');

        // Detect double-elimination even before any LB fights exist in DB
        const isDouble = allMatchesCategory.some(m =>
            m.bracketType === 'ko' || m.bracketType === 'double' || m.bracketType === 'DOUBLE_ELIMINATION'
        );

        // Expand WB first so we can pass R0 count to LB expansion
        const expandedWb = this._expandBracket(wbMatches);

        // Show/hide phase toggle
        const hasLb = isDouble || lbMatches.length > 0;
        const phaseToggle = document.getElementById('phase-toggle');
        if (phaseToggle) phaseToggle.style.display = hasLb ? 'flex' : 'none';
        const showingLb = hasLb && State.bracketPhaseView === 'lb';
        document.getElementById('phase-btn-wb')?.classList.toggle('active', !showingLb);
        document.getElementById('phase-btn-lb')?.classList.toggle('active', showingLb);

        // Expand LB: derive full structure from WB R0 count so rounds never shrink
        const wbR0Count = wbMatches.filter(m => m.round === 1).length;
        const expandedLb = this._expandLb(lbMatches, wbR0Count, wbMatches[0]);
        // Each toggle shows only its own phase — no combined view
        const expandedAll = showingLb ? expandedLb : expandedWb;

        // Build a map of matches and their children (matches that feed INTO them)
        const matchMap = new Map();
        expandedAll.forEach(m => matchMap.set(m.matchId, { ...m, children: [] }));

        expandedAll.forEach(m => {
            if (m.nextMatchId && matchMap.has(m.nextMatchId)) {
                const parent = matchMap.get(m.nextMatchId);
                parent.children.push(m.matchId);
            }
        });

        // Identify roots (matches that don't feed into any other match in THIS set)
        const getRoots = (matchList) => {
            const roots = [];
            matchList.forEach(m => {
                if (!m.nextMatchId || !matchMap.has(m.nextMatchId)) {
                    roots.push(m);
                }
            });
            roots.sort((a, b) => a.matchId - b.matchId);
            return roots;
        };

        const wbRoots = showingLb ? getRoots(expandedLb) : getRoots(expandedWb);
        const lbRoots = []; // no combined view — LB only visible in toggle

        const MATCH_WIDTH = 185;
        const MATCH_HEIGHT = 48;
        const X_SPACING = 260;
        const Y_SPACING = 80;
        const OFFSET_X = 40;
        const OFFSET_Y = 80;

        const maxRound = Math.max(...expandedAll.map(m => m.round));

        function getRoundName(round) {
            if (showingLb) {
                const dbRound = round - 1; // convert to 0-indexed
                if (round === maxRound) return '3. PLATZ';
                // Even DB rounds = Reduktion (LB survivors fight each other)
                // Odd DB rounds = Einspielung (WB loser injects into LB survivor)
                if (dbRound % 2 === 0) {
                    const lbRoundNum = Math.floor(dbRound / 2) + 1;
                    return `VB RUNDE ${lbRoundNum}`;
                }
                const injNum = Math.ceil(dbRound / 2);
                return `EINSPIELUNG ${injNum}`;
            }
            if (round === maxRound) return 'FINALE';
            if (round === maxRound - 1) return 'HALBFINALE';
            if (round === maxRound - 2) return 'VIERTELFINALE';
            return `${round}. RUNDE`;
        }

        let currentY = 0;
        const positions = new Map();
        const visited = new Set();

        function calculatePosDAG(matchId, xOffsetRounds = 0) {
            if (visited.has(matchId)) return positions.get(matchId);
            visited.add(matchId);

            const node = matchMap.get(matchId);
            const children = node.children.map(cid => matchMap.get(cid)).filter(c => c.phase === node.phase);

            // Sort children so p1 is above p2
            children.sort((a, b) => {
                if (a.nextMatchPos === 'p1' && b.nextMatchPos === 'p2') return -1;
                if (a.nextMatchPos === 'p2' && b.nextMatchPos === 'p1') return 1;
                return a.matchId - b.matchId;
            });

            const childPositions = [];
            for (const child of children) {
                childPositions.push(calculatePosDAG(child.matchId, xOffsetRounds));
            }

            let myY;
            if (childPositions.length === 0) {
                myY = currentY;
                currentY += Y_SPACING;
            } else {
                const sumY = childPositions.reduce((acc, pos) => acc + pos.y, 0);
                myY = sumY / childPositions.length;
            }

            const myX = ((node.round - 1) + xOffsetRounds) * X_SPACING;
            const pos = { x: myX, y: myY };
            positions.set(matchId, pos);
            return pos;
        }

        for (const root of wbRoots) {
            calculatePosDAG(root.matchId, 0);
            currentY += Y_SPACING;
        }

        // Record where LB section starts so we can draw a divider label
        let lbSectionY = -1;
        if (lbRoots.length > 0 && wbRoots.length > 0) {
            lbSectionY = currentY + Y_SPACING * 0.25;
            currentY += Y_SPACING * 1.5;  // extra breathing room for the label
        }

        for (const root of lbRoots) {
            calculatePosDAG(root.matchId, 0);
            currentY += Y_SPACING;
        }

        let maxX = 0;
        let maxY = 0;
        positions.forEach(pos => {
            if (pos.x > maxX) maxX = pos.x;
            if (pos.y > maxY) maxY = pos.y;
        });

        viz.style.position = 'relative';
        viz.style.minWidth = `${maxX + MATCH_WIDTH + OFFSET_X * 2}px`;
        viz.style.minHeight = `${maxY + MATCH_HEIGHT + OFFSET_Y * 2}px`;
        // Cleanup old flex properties
        viz.style.display = 'block';

        // Draw SVG lines first
        const svgNS = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(svgNS, "svg");
        svg.style.position = 'absolute';
        svg.style.top = '0';
        svg.style.left = '0';
        svg.style.width = '100%';
        svg.style.height = '100%';
        svg.style.pointerEvents = 'none';

        expandedAll.forEach(m => {
            if (m.nextMatchId && positions.has(m.nextMatchId)) {
                const pos = positions.get(m.matchId);
                const parentPos = positions.get(m.nextMatchId);

                // Skip cross-phase lines (e.g. WB→LB injection); use matchMap to support virtual fights
                const parentNode = matchMap.get(m.nextMatchId);
                if (!parentNode || parentNode.phase !== m.phase) return;

                const startX = pos.x + MATCH_WIDTH + OFFSET_X;
                const startY = pos.y + MATCH_HEIGHT / 2 + OFFSET_Y;
                const endX = parentPos.x + OFFSET_X;
                const endY = parentPos.y + MATCH_HEIGHT / 2 + OFFSET_Y;

                const path = document.createElementNS(svgNS, "path");
                const midX = startX + (endX - startX) / 2;
                path.setAttribute('d', `M ${startX} ${startY} H ${midX} V ${endY} H ${endX}`);

                const strokeColor = m.phase === 'lb' ? '#c08040' : '#a0b8d0';
                path.setAttribute('stroke', strokeColor);
                if (m.phase === 'lb') path.setAttribute('stroke-dasharray', '5,3');
                path.setAttribute('stroke-width', '1.5');
                path.setAttribute('fill', 'none');
                svg.appendChild(path);
            }
        });

        // Draw Column Headers
        for (let r = 1; r <= maxRound; r++) {
            const header = document.createElement('div');
            header.className = 'bracket-round-name';
            header.style.position = 'absolute';
            header.style.left = `${(r - 1) * X_SPACING + OFFSET_X}px`;
            header.style.top = '20px';
            header.style.width = `${MATCH_WIDTH}px`;
            header.textContent = getRoundName(r);
            viz.appendChild(header);
        }

        viz.appendChild(svg);

        // Draw "VERLIERERSEITE" section divider when showing combined WB + LB
        if (!showingLb && lbSectionY >= 0) {
            const divider = document.createElement('div');
            divider.className = 'lb-section-divider';
            divider.style.cssText = `position:absolute;top:${lbSectionY + OFFSET_Y}px;left:${OFFSET_X}px;`;
            divider.innerHTML = '<span class="lb-section-label">VERLIERERSEITE</span>';
            viz.appendChild(divider);
        }

        // Draw nodes (EDV-style)
        expandedAll.forEach(m => {
            const pos = positions.get(m.matchId);
            if (!pos) return;

            const nodeData = matchMap.get(m.matchId);
            const childP1 = nodeData.children.map(cid => matchMap.get(cid)).find(c => c.nextMatchPos === 'p1');
            const childP2 = nodeData.children.map(cid => matchMap.get(cid)).find(c => c.nextMatchPos === 'p2');

            const p1Won = m.status === 'finished' && m.winnerId && String(m.p1.id) === String(m.winnerId);
            const p2Won = m.status === 'finished' && m.winnerId && String(m.p2.id) === String(m.winnerId);

            // Determine label + CSS class for each participant slot.
            // If a child match feeds this slot but the participant is already known
            // (pre-filled from a bye or previous result), show the name — not "Winner X".
            const slotInfo = (p, child, won) => {
                if (child && p.id === 'WAIT') {
                    const label = child.fightNr ? `Winner ${child.fightNr}` : '?';
                    return { label, cls: 'edv-pending', score: null };
                }
                const isBye = !p.lastName || p.lastName === 'TBD' || p.id === 'WAIT';
                if (isBye) return { label: 'Freilos', cls: 'edv-freilos', score: null };
                const name = p.lastName + (p.firstName ? ', ' + p.firstName : '');
                const club = p.club ? ` [${p.club}]` : '';
                const score = m.status !== 'upcoming' ? (p.score?.points ?? 0) : null;
                return { label: name + club, cls: won ? 'edv-winner' : 'edv-participant', score };
            };

            // Bye fight: same participant in both slots → show participant once, Freilos for the other
            const isByeFight = m.status === 'bye' ||
                (m.p1.id !== 'WAIT' && m.p2.id !== 'WAIT' && String(m.p1.id) === String(m.p2.id));

            const s1 = slotInfo(m.p1, childP1, isByeFight || p1Won);
            const s2 = isByeFight
                ? { label: 'Freilos', cls: 'edv-freilos', score: null }
                : slotInfo(m.p2, childP2, p2Won);

            const scoreHtml = (s) => s.score !== null
                ? `<span class="edv-score">${s.score}</span>` : '';

            const node = document.createElement('div');
            node.className = `edv-node absolute-node ${m.phase === 'lb' ? 'edv-lb' : ''}`;
            node.style.cssText = `position:absolute;left:${pos.x + OFFSET_X}px;top:${pos.y + OFFSET_Y}px;width:${MATCH_WIDTH}px;`;
            node.innerHTML = `
                <div class="edv-slot ${s1.cls}">
                    <span class="edv-name">${s1.label}</span>${scoreHtml(s1)}
                </div>
                <div class="edv-divider"></div>
                <div class="edv-slot ${s2.cls}">
                    <span class="edv-name">${s2.label}</span>${scoreHtml(s2)}
                </div>
            `;
            viz.appendChild(node);
        });
    },

    renderPoolGrid(allMatches, container) {
        container.innerHTML = '';
        container.style.cssText = 'display:block;position:static;min-width:auto;min-height:auto;';

        const poolIndices = [...new Set(allMatches.map(m => m.poolIndex ?? 0))].sort((a, b) => a - b);
        const totalFighters = new Set(
            allMatches.flatMap(m => [m.p1?.id, m.p2?.id]).filter(id => id && id !== 'WAIT')
        ).size;

        const wrapper = document.createElement('div');
        wrapper.className = 'pool-system-wrapper';

        const sysTitle = document.createElement('div');
        sysTitle.className = 'pool-system-title';
        sysTitle.textContent = `Pool System – ${totalFighters} Teilnehmer`;
        wrapper.appendChild(sysTitle);

        for (const poolIdx of poolIndices) {
            const pm = allMatches
                .filter(m => (m.poolIndex ?? 0) === poolIdx)
                .sort((a, b) => (a.posInRound ?? 0) - (b.posInRound ?? 0));
            if (!pm.length) continue;

            // Collect fighters in schedule order (first appearance = start nr 1, 2, …)
            const fMap = new Map();
            const fList = [];
            pm.forEach(m => {
                for (const p of [m.p1, m.p2]) {
                    if (p?.id && p.id !== 'WAIT' && !fMap.has(String(p.id))) {
                        const entry = { ...p, startNr: fList.length + 1 };
                        fMap.set(String(p.id), entry);
                        fList.push(entry);
                    }
                }
            });
            const numFights = pm.length;

            if (poolIndices.length > 1) {
                const pt = document.createElement('div');
                pt.className = 'pool-subtitle';
                pt.textContent = `Pool ${poolIdx + 1}`;
                wrapper.appendChild(pt);
            }

            // --- Compute per-fighter stats ---
            const stats = fList.map(f => {
                let wins = 0, ubw = 0;
                pm.forEach(m => {
                    const isP1 = String(m.p1?.id) === String(f.id);
                    const isP2 = String(m.p2?.id) === String(f.id);
                    if (!isP1 && !isP2) return;
                    if (m.status !== 'finished' && m.status !== 'completed') return;
                    const own = Number(isP1 ? (m.p1.score?.points ?? 0) : (m.p2.score?.points ?? 0));
                    const opp = Number(isP1 ? (m.p2.score?.points ?? 0) : (m.p1.score?.points ?? 0));
                    if (String(m.winnerId) === String(f.id)) wins++;
                    ubw += own - opp;  // Ubw. = score difference (own minus conceded)
                });
                return { id: f.id, wins, ubw };
            });
            // Sort by wins DESC, then ubw DESC for tiebreaking
            const ranked = [...stats].sort((a, b) => b.wins - a.wins || b.ubw - a.ubw);
            // Dense ranking: fighters with the same wins AND ubw share the same Platz
            const platzOf = id => {
                const me = stats.find(s => s.id === id);
                return ranked.filter(s => s.wins > me.wins || (s.wins === me.wins && s.ubw > me.ubw)).length + 1;
            };
            const anyDone = pm.some(m => m.status === 'finished' || m.status === 'completed');
            const allDone = pm.every(m => m.status === 'finished' || m.status === 'completed' || m.status === 'bye');

            // --- Build table ---
            const tbl = document.createElement('table');
            tbl.className = 'pool-edv-table';

            // Header row 1
            const thead = tbl.createTHead();
            const th1 = thead.insertRow();
            const mkTH = (html, cls, rs, cs) => {
                const t = document.createElement('th');
                t.innerHTML = html; t.className = cls ?? '';
                if (rs) t.rowSpan = rs; if (cs) t.colSpan = cs;
                return t;
            };
            th1.appendChild(mkTH('Start<br>nr', 'pth pth-startnr', 2));
            th1.appendChild(mkTH('Kämpfername<br>Verein', 'pth pth-name', 2));
            th1.appendChild(mkTH('Kampfnummer', 'pth pth-kampfnr', null, 2));
            for (let i = 1; i <= numFights; i++) th1.appendChild(mkTH(String(i), 'pth pth-fight', 2));
            th1.appendChild(mkTH('Punkte', 'pth pth-sum', 2));
            th1.appendChild(mkTH('Ubw.', 'pth pth-sum', 2));
            th1.appendChild(mkTH('Platz', 'pth pth-sum', 2));

            // Header row 2: sub-headers under Kampfnummer
            const th2 = thead.insertRow();
            th2.appendChild(mkTH('Pkt', 'pth pth-sub'));
            th2.appendChild(mkTH('Ubw.', 'pth pth-sub'));

            // Body
            const tbody = tbl.createTBody();
            fList.forEach(f => {
                const s = stats.find(x => x.id === f.id);
                const platz = platzOf(f.id);
                const isLeader = allDone && platz === 1;
                const tr = tbody.insertRow();
                if (isLeader) tr.classList.add('pool-leader');

                const name = `${f.lastName}${f.firstName ? ', ' + f.firstName : ''} [${f.club || '?'}]`;
                tr.appendChild(Object.assign(document.createElement('td'), { className: 'ptd ptd-startnr', textContent: String(f.startNr) }));
                tr.appendChild(Object.assign(document.createElement('td'), { className: 'ptd ptd-name', textContent: name }));
                tr.appendChild(Object.assign(document.createElement('td'), { className: 'ptd ptd-lbl', textContent: 'Punkte' }));
                tr.appendChild(Object.assign(document.createElement('td'), { className: 'ptd ptd-lbl', textContent: 'Ubw.' }));

                pm.forEach(m => {
                    const isP1 = String(m.p1?.id) === String(f.id);
                    const isP2 = String(m.p2?.id) === String(f.id);
                    const td = document.createElement('td');
                    if (!isP1 && !isP2) {
                        td.className = 'ptd ptd-nopart';
                    } else {
                        const done = m.status === 'finished' || m.status === 'completed';
                        if (!done) {
                            td.className = 'ptd ptd-pending';
                        } else {
                            const myPts  = Number(isP1 ? (m.p1.score?.points ?? 0) : (m.p2.score?.points ?? 0));
                            const oppPts = Number(isP1 ? (m.p2.score?.points ?? 0) : (m.p1.score?.points ?? 0));
                            const won = String(m.winnerId) === String(f.id);
                            td.className = `ptd ${won ? 'ptd-win' : 'ptd-loss'}`;
                            // Show "myScore | oppScore" — mirrored for each row
                            td.innerHTML = `<span class="ps-my">${myPts}</span><span class="ps-sep">|</span><span class="ps-opp">${oppPts}</span>`;
                        }
                    }
                    tr.appendChild(td);
                });

                const mkSum = (val, show) => Object.assign(document.createElement('td'), { className: 'ptd ptd-sum', textContent: show ? String(val) : '' });
                tr.appendChild(mkSum(s.wins, anyDone));
                tr.appendChild(mkSum(s.ubw, anyDone));
                tr.appendChild(mkSum(platz, allDone));
            });

            // Kampfzeit row
            const trTime = tbody.insertRow();
            trTime.classList.add('pool-kampfzeit-row');
            const tdLabel = document.createElement('td');
            tdLabel.colSpan = 4; tdLabel.className = 'ptd ptd-kampfzeit';
            tdLabel.textContent = 'Kampfzeit';
            trTime.appendChild(tdLabel);
            for (let i = 0; i < numFights; i++) {
                trTime.appendChild(Object.assign(document.createElement('td'), { className: 'ptd ptd-time' }));
            }
            const tdSumSpacer = document.createElement('td');
            tdSumSpacer.colSpan = 3; tdSumSpacer.className = 'ptd';
            trTime.appendChild(tdSumSpacer);

            wrapper.appendChild(tbl);

            const footer = document.createElement('div');
            footer.className = 'pool-footer';
            footer.textContent = `${fList.length} Kämpfer • Round-Robin Format`;
            wrapper.appendChild(footer);
        }

        // KO phase: WB fights generated after both pools complete
        const koMatches = allMatches.filter(m => m.phase === 'wb')
            .sort((a, b) => (a.round - b.round) || (a.posInRound - b.posInRound));

        if (koMatches.length > 0) {
            const koDivider = document.createElement('div');
            koDivider.className = 'pool-ko-divider';
            koDivider.textContent = 'KO-Phase';
            wrapper.appendChild(koDivider);

            const koTable = document.createElement('table');
            koTable.className = 'pool-edv-table pool-ko-table';

            const koHead = koTable.createTHead().insertRow();
            ['Kampf', 'Kämpfer 1', 'Ergebnis', 'Kämpfer 2'].forEach(h => {
                koHead.appendChild(Object.assign(document.createElement('th'), {
                    className: 'pth', textContent: h
                }));
            });

            const koBody = koTable.createTBody();
            const sfFights = koMatches.filter(m => m.round === 1);  // round=1 (DB R0) = SFs
            const finalFight = koMatches.find(m => m.round === 2);  // round=2 (DB R1) = Final

            const koLabels = sfFights.map((_, i) => `Halbfinale ${i + 1}`);
            koLabels.push('Finale');

            [...sfFights, ...(finalFight ? [finalFight] : [])].forEach((m, i) => {
                const tr = koBody.insertRow();
                const done = m.status === 'finished' || m.status === 'completed';
                const p1Name = m.p1?.id === 'WAIT' ? '—' : `${m.p1?.lastName || ''}${m.p1?.firstName ? ', ' + m.p1.firstName : ''}`;
                const p2Name = m.p2?.id === 'WAIT' ? '—' : `${m.p2?.lastName || ''}${m.p2?.firstName ? ', ' + m.p2.firstName : ''}`;
                const score = done
                    ? `${Number(m.p1?.score?.points ?? 0)} : ${Number(m.p2?.score?.points ?? 0)}`
                    : (m.p1?.id !== 'WAIT' && m.p2?.id !== 'WAIT' ? 'vs' : '—');
                const isP1Won = done && String(m.winnerId) === String(m.p1?.id);
                const isP2Won = done && String(m.winnerId) === String(m.p2?.id);

                tr.innerHTML = `
                    <td class="ptd ptd-lbl">${koLabels[i]}</td>
                    <td class="ptd ptd-name ${isP1Won ? 'ptd-win' : ''}">${p1Name}</td>
                    <td class="ptd ptd-ko-score">${score}</td>
                    <td class="ptd ptd-name ${isP2Won ? 'ptd-win' : ''}">${p2Name}</td>
                `;
            });

            wrapper.appendChild(koTable);
        }

        container.appendChild(wrapper);
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

        if (m.p1.id === 'WAIT' || m.p2.id === 'WAIT') {
            alert("Dieser Kampf ist noch pas bereit (Teilnehmer fehlen).");
            return;
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
        Network.send({ type: 'IPPON_START', matchId: m.matchId });
    },

    closeModal() {
        this.stopTimer();
        const m = State.currentScoringMatch;
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

        const currentVal = Number(player.score[type] || 0);
        State.scoreHistory.push({ playerNum, type, prevValue: currentVal });
        player.score[type] = currentVal + Number(pointsValue);

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

    // Set timer to a specific elapsed or remaining seconds from Ippon Board
    setTimerSeconds(seconds) {
        State.timer.remainingSeconds = Math.max(0, seconds);
        this.updateTimerUI();
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

            if (table === 'admin') {
                setTimeout(() => UI.switchView('admin-dashboard'), 100);
            } else {
                setTimeout(() => UI.switchView('fights'), 100);
            }
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
        };
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
    UI.switchView('fights');
    UI.renderFightList();
};

window.winByDecision = function (playerNum) {
    if (!State.currentScoringMatch) return;
    const player = playerNum === 1 ? State.currentScoringMatch.p1 : State.currentScoringMatch.p2;
    const currentPoints = player.score.points || 0;

    // Ensure the winner has at least 10 points to satisfy auto-progression condition
    if (currentPoints < 10) {
        const type = 'points';
        const currentVal = Number(currentPoints || 0);
        State.scoreHistory.push({ playerNum, type, prevValue: currentVal });
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

    // Show victory overlay instead of auto-closing silently
    const winnerName = `${player.firstName} ${player.lastName}`.trim() || 'Player';
    UI.showVictoryPopup(winnerName);
};
