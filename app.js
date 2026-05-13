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
        } else if (data.type === 'SIGNAL') {
            Scoring.triggerTimerSignal(data.signalType, false);
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
            if ((m.status === 'upcoming' || m.status === 'pending') && !nextMatchByTable.has(m.tableId)) {
                nextMatchByTable.set(m.tableId, m.matchId);
            }
        });
        const nextMatchIds = new Set(nextMatchByTable.values());

        const isAdminMode = tableNum === 'admin';
        displayMatches.forEach(m => container.appendChild(this.createFightCard(m, assignedTable, nextMatchIds, isAdminMode)));
        document.getElementById('match-count').textContent = `${displayMatches.length} Kämpfe angezeigt`;
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
                <span class="category-name">${match.category}</span>
                <a href="#" class="bracket-link" onclick="UI.handleBracketClick(event, ${match.matchId})">Live-Turnierbaum</a>
            </div>
            <div class="fighters-display">
                <div class="fighter p1">
                    <span class="fighter-name">${match.p1.firstName} ${match.p1.lastName}</span>
                    <span class="fighter-club">${match.p1.club}</span>
                </div>
                <div class="vs-divider">VS</div>
                <div class="fighter p2">
                    <span class="fighter-name">${match.p2.firstName} ${match.p2.lastName}</span>
                    <span class="fighter-club">${match.p2.club}</span>
                </div>
            </div>
            <div class="status-box">
                <div class="status-badge ${match.status}">${statusLabel}</div>
                ${restTag}
            </div>
            <div class="action-area">
                ${(match.status !== 'finished' && match.status !== 'bye') ?
                `<button class="btn-start" ${isReadOnly ? 'disabled' : ''} onclick="event.stopPropagation(); sendToIpponboard(${match.matchId})" title="An Ipponboard senden">Start</button>
                 <button class="btn-result" ${isReadOnly ? 'disabled' : ''} onclick="event.stopPropagation(); openResultDialog(${match.matchId})">Ergebnis setzen</button>` :
                (match.status === 'bye'
                    ? `<span class="text-muted">FREILOS</span>`
                    : (match.winnerName
                        ? `<span class="winner-badge" title="Sieger">🏆 ${match.winnerName}</span>`
                        : `<span class="draw-badge" title="Unentschieden">Unentschieden</span>`))}
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
        const categories = [...new Set(State.activeMatches.map(m => m.category))];
        categories.forEach(cat => {
            const div = document.createElement('div');
            div.className = `category-item ${State.currentBracketCategory === cat ? 'active' : ''}`;
            div.textContent = cat;
            div.onclick = () => { State.currentBracketCategory = cat; this.renderBracketVisualization(); this.updateBracketSidebar(); };
            list.appendChild(div);
        });
        if (!State.currentBracketCategory && categories.length > 0) {
            State.currentBracketCategory = categories[0];
            this.renderBracketVisualization();
        }
    },

    renderBracketVisualization() {
        const viz = document.getElementById('bracket-visualization');
        const titleEl = document.getElementById('current-bracket-title');

        if (!viz || !State.currentBracketCategory) return;
        viz.innerHTML = '';
        if (titleEl) titleEl.textContent = State.currentBracketCategory;

        // Main bracket only for now: filter out Loser Bracket (lb) matches to prevent them breaking the vertical DAG tree
        const matches = State.activeMatches.filter(m => m.category === State.currentBracketCategory && m.phase !== 'lb');
        if (matches.length === 0) return;

        // Build a map of matches and their children (matches that feed INTO them)
        const matchMap = new Map();
        matches.forEach(m => matchMap.set(m.matchId, { ...m, children: [] }));

        matches.forEach(m => {
            if (m.nextMatchId && matchMap.has(m.nextMatchId)) {
                const parent = matchMap.get(m.nextMatchId);
                parent.children.push(m.matchId);
            }
        });

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
        const MATCH_HEIGHT = 100;
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

            // Sort children so p1 is above p2
            children.sort((a, b) => {
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
        viz.appendChild(svg);

        // Draw nodes
        matches.forEach(m => {
            const pos = positions.get(m.matchId);
            if (!pos) return;
            const node = document.createElement('div');
            node.className = 'bracket-match-node absolute-node';
            node.style.position = 'absolute';
            node.style.left = `${pos.x + OFFSET_X}px`;
            node.style.top = `${pos.y + OFFSET_Y}px`;
            node.style.width = `${MATCH_WIDTH}px`;

            const p1Score = m.p1.score.points || 0;
            const p2Score = m.p2.score.points || 0;
            const p1Won = m.status === 'finished' && m.winnerId != null && m.winnerId === m.p1.gpId;
            const p2Won = m.status === 'finished' && m.winnerId != null && m.winnerId === m.p2.gpId;

            const p1Display = m.p1.firstName || m.p1.lastName
                ? `${m.p1.firstName || ''} ${m.p1.lastName || ''}`.trim()
                : 'TBD';
            const p2Display = m.p2.firstName || m.p2.lastName
                ? `${m.p2.firstName || ''} ${m.p2.lastName || ''}`.trim()
                : 'TBD';
            node.innerHTML = `
                <div class="match-node-p ${p1Won ? 'winner' : ''}">
                    <span class="p-name">${p1Display}</span>
                    <span class="p-score-box">${p1Score}</span>
                </div>
                <div class="match-node-p ${p2Won ? 'winner' : ''}">
                    <span class="p-name">${p2Display}</span>
                    <span class="p-score-box">${p2Score}</span>
                </div>
            `;
            viz.appendChild(node);
        });
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
    UI.renderFightList();
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

    // Show victory overlay instead of auto-closing silently
    const winnerName = `${player.firstName} ${player.lastName}`.trim() || 'Player';
    UI.showVictoryPopup(winnerName);
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
    if (kind === 'p1') markWinner(currentResultMatchId, 1);
    else if (kind === 'p2') markWinner(currentResultMatchId, 2);
    else markDraw(currentResultMatchId);
    closeResultDialog();
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
