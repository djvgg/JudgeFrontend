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
    poolStandings: {} // bracketId -> standings array
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
                <span class="category-name">${match.category}</span>
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

        const allMatchesCategory = State.activeMatches.filter(m => m.category === State.currentBracketCategory);
        if (allMatchesCategory.length === 0) return;

        const isPool = allMatchesCategory.some(m => m.poolIndex !== null && m.poolIndex !== undefined);
        if (isPool) {
            this.renderPoolGrid(allMatchesCategory, viz);
            return;
        }

        const wbMatches = allMatchesCategory.filter(m => m.phase !== 'lb');
        const lbMatches = allMatchesCategory.filter(m => m.phase === 'lb');

        // Build a map of matches and their children (matches that feed INTO them)
        const matchMap = new Map();
        allMatchesCategory.forEach(m => matchMap.set(m.matchId, { ...m, children: [] }));

        allMatchesCategory.forEach(m => {
            if (m.nextMatchId && matchMap.has(m.nextMatchId)) {
                const parent = matchMap.get(m.nextMatchId);
                parent.children.push(m.matchId);
            }
        });

        // Identify roots (matches that don't feed into any other match in THIS phase)
        const getRoots = (matchList) => {
            const roots = [];
            matchList.forEach(m => {
                if (!m.nextMatchId || !matchMap.has(m.nextMatchId) || matchMap.get(m.nextMatchId).phase !== m.phase) {
                    roots.push(m);
                }
            });
            roots.sort((a, b) => a.matchId - b.matchId);
            return roots;
        };

        const wbRoots = getRoots(wbMatches);
        const lbRoots = getRoots(lbMatches);

        const MATCH_WIDTH = 220;
        const MATCH_HEIGHT = 100;
        const X_SPACING = 300;
        const Y_SPACING = 140;
        const OFFSET_X = 40;
        const OFFSET_Y = 80;

        const maxRound = Math.max(...allMatchesCategory.map(m => m.round));

        function getRoundName(round, isLb = false) {
            if (isLb) return `TROSTRUNDE R${round}`;
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

        // Add visual separation before Loser bracket
        if (lbRoots.length > 0 && wbRoots.length > 0) {
            currentY += Y_SPACING * 0.5;
        }

        for (const root of lbRoots) {
            // Loser bracket starts from round 1 visually, but we can offset it if desired.
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

        allMatchesCategory.forEach(m => {
            if (m.nextMatchId && positions.has(m.nextMatchId)) {
                const pos = positions.get(m.matchId);
                const parentPos = positions.get(m.nextMatchId);

                // For double elimination, we don't draw lines between WB and LB for now as it's cleaner
                const parentNode = allMatchesCategory.find(pm => String(pm.matchId) === String(m.nextMatchId));
                if (!parentNode || parentNode.phase !== m.phase) return;

                const startX = pos.x + MATCH_WIDTH + OFFSET_X;
                const startY = pos.y + MATCH_HEIGHT / 2 + OFFSET_Y;
                const endX = parentPos.x + OFFSET_X;
                const endY = parentPos.y + MATCH_HEIGHT / 2 + OFFSET_Y;

                const path = document.createElementNS(svgNS, "path");
                const midX = startX + (endX - startX) / 2;
                path.setAttribute('d', `M ${startX} ${startY} H ${midX} V ${endY} H ${endX}`);

                // Use Jasmine/Orange for LB, Sky Blue for WB
                const strokeColor = m.phase === 'lb' ? '#F5CA74' : '#22AAF0';
                path.setAttribute('stroke', strokeColor);
                if (m.phase === 'lb') path.setAttribute('stroke-dasharray', '6,4');
                path.setAttribute('stroke-width', '2.5');
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

        // Draw nodes
        allMatchesCategory.forEach(m => {
            const pos = positions.get(m.matchId);
            if (!pos) return;
            const node = document.createElement('div');
            node.className = `bracket-match-node absolute-node ${m.phase === 'lb' ? 'loser-bracket' : ''}`;
            node.style.position = 'absolute';
            node.style.left = `${pos.x + OFFSET_X}px`;
            node.style.top = `${pos.y + OFFSET_Y}px`;
            node.style.width = `${MATCH_WIDTH}px`;

            const p1Score = m.p1.score.points || 0;
            const p2Score = m.p2.score.points || 0;
            const p1Won = m.status === 'finished' && m.winnerId && String(m.p1.id) === String(m.winnerId);
            const p2Won = m.status === 'finished' && m.winnerId && String(m.p2.id) === String(m.winnerId);

            const p1Label = m.p1.lastName;
            const p2Label = m.p2.lastName;

            // Simple heuristic: If it's a loser bracket match and participant is not decided yet
            // In a better system, this would come from the backend metadata.
            const getAltName = (p) => {
                if (m.phase === 'lb' && p.id === 'WAIT') {
                    return 'Verlierer Kampf #...';
                }
                return 'TBD';
            };

            node.innerHTML = `
                <div class="match-node-header">
                    <span class="m-id">Kampf #${m.fightNr}</span>
                    <span class="m-phase">${m.phase === 'lb' ? 'TROSTRUNDE' : 'HAUPTRUNDE'}</span>
                </div>
                <div class="match-node-p ${p1Won ? 'winner' : ''}">
                    <span class="p-name">${p1Label || getAltName(m.p1)}</span>
                    <span class="p-score-box">${p1Score}</span>
                </div>
                <div class="match-node-p ${p2Won ? 'winner' : ''}">
                    <span class="p-name">${p2Label || getAltName(m.p2)}</span>
                    <span class="p-score-box">${p2Score}</span>
                </div>
            `;
            viz.appendChild(node);
        });
    },

    renderPoolGrid(matches, container) {
        // Determine unique fighters
        const fighters = new Map();
        matches.forEach(m => {
            if (m.p1 && m.p1.id !== "WAIT" && m.status !== 'bye') fighters.set(String(m.p1.id), m.p1);
            if (m.p2 && m.p2.id !== "WAIT" && m.status !== 'bye') fighters.set(String(m.p2.id), m.p2);
        });

        const fighterList = Array.from(fighters.values());

        // Use backend standings if available
        const backendStandings = State.poolStandings[State.currentBracketCategory];
        if (backendStandings) {
            this.renderAuthoritativePoolStandings(backendStandings, container);
            return;
        }

        container.style.display = 'block';
        container.style.position = 'static';
        container.style.minWidth = 'auto';
        container.style.minHeight = 'auto';

        const wrapper = document.createElement('div');
        wrapper.className = 'pool-grid-wrapper';

        const table = document.createElement('table');
        table.className = 'pool-table';

        // Header Row
        const trHead = document.createElement('tr');
        trHead.innerHTML = `<th>Kämpfer</th>`;
        fighterList.forEach(f => {
            trHead.innerHTML += `<th>${f.firstName} ${f.lastName}</th>`;
        });
        trHead.innerHTML += `<th>Punkte</th>`;
        table.appendChild(trHead);

        // Pre-calculate points to find leader
        const fighterPoints = fighterList.map(rowFighter => {
            let pts = 0;
            fighterList.forEach(colFighter => {
                if (rowFighter.id !== colFighter.id) {
                    const match = matches.find(m =>
                        (String(m.p1.id) === String(rowFighter.id) && String(m.p2.id) === String(colFighter.id)) ||
                        (String(m.p2.id) === String(rowFighter.id) && String(m.p1.id) === String(colFighter.id))
                    );
                    if (match && match.status === 'finished') {
                        const rScore = Number(String(match.p1.id) === String(rowFighter.id) ? (match.p1.score.points || 0) : (match.p2.score.points || 0));
                        const cScore = Number(String(match.p1.id) === String(colFighter.id) ? (match.p1.score.points || 0) : (match.p2.score.points || 0));
                        if (rScore >= cScore) pts += rScore;
                    }
                }
            });
            return { id: rowFighter.id, points: pts };
        });

        const maxPoints = Math.max(...fighterPoints.map(fp => fp.points));
        const hasFinishedMatches = matches.some(m => m.status === 'finished');

        // Rows
        fighterList.forEach((rowFighter) => {
            const tr = document.createElement('tr');
            const pts = fighterPoints.find(fp => fp.id === rowFighter.id).points;
            const isLeader = hasFinishedMatches && pts === maxPoints && pts > 0;

            if (isLeader) tr.classList.add('pool-leader');

            tr.innerHTML = `<td class="fighter-col">${rowFighter.firstName} ${rowFighter.lastName} ${isLeader ? '🏆' : ''}</td>`;

            fighterList.forEach((colFighter) => {
                if (rowFighter.id === colFighter.id) {
                    tr.innerHTML += `<td class="col-cross"></td>`;
                } else {
                    const match = matches.find(m =>
                        (String(m.p1.id) === String(rowFighter.id) && String(m.p2.id) === String(colFighter.id)) ||
                        (String(m.p2.id) === String(rowFighter.id) && String(m.p1.id) === String(colFighter.id))
                    );

                    if (!match) {
                        tr.innerHTML += `<td>-</td>`;
                    } else if (match.status !== 'finished') {
                        tr.innerHTML += `<td><span class="status-upcoming">Ausstehend</span></td>`;
                    } else {
                        const rScore = Number(String(match.p1.id) === String(rowFighter.id) ? (match.p1.score.points || 0) : (match.p2.score.points || 0));
                        const cScore = Number(String(match.p1.id) === String(colFighter.id) ? (match.p1.score.points || 0) : (match.p2.score.points || 0));

                        if (rScore > cScore) {
                            tr.innerHTML += `<td class="cell-win">Sieg<br><small>(${rScore})</small></td>`;
                        } else if (cScore > rScore) {
                            tr.innerHTML += `<td class="cell-loss">Ndlg<br><small>(${rScore})</small></td>`;
                        } else {
                            tr.innerHTML += `<td class="cell-win" style="background: rgba(255,255,255,0.05)">TIE<br><small>(${rScore})</small></td>`;
                        }
                    }
                }
            });

            tr.innerHTML += `<td class="total-col">${pts}</td>`;
            table.appendChild(tr);
        });

        wrapper.appendChild(table);
        container.appendChild(wrapper);
    },

    renderAuthoritativePoolStandings(standings, container) {
        container.innerHTML = '';
        const table = document.createElement('table');
        table.className = 'pool-table';

        const trHead = document.createElement('tr');
        trHead.innerHTML = `
            <th>Rang</th>
            <th>Name</th>
            <th>Konto/Club</th>
            <th>Siege</th>
            <th>Punkte</th>
        `;
        table.appendChild(trHead);

        standings.forEach((s, idx) => {
            const tr = document.createElement('tr');
            if (idx === 0) tr.classList.add('pool-leader');
            tr.innerHTML = `
                <td>${idx + 1}</td>
                <td class="fighter-col">${s.name} ${idx === 0 ? '🏆' : ''}</td>
                <td>${s.club || '-'}</td>
                <td>${s.wins}</td>
                <td>${s.points}</td>
            `;
            table.appendChild(tr);
        });

        const wrapper = document.createElement('div');
        wrapper.className = 'pool-grid-wrapper';
        wrapper.appendChild(table);
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
