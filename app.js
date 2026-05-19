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
                const prev = State.activeMatches[idx];
                const wasNotFinished = prev.status !== 'finished';
                State.activeMatches[idx] = { ...data.match, restSeconds: State.activeMatches[idx].restSeconds };
                if (State.currentScoringMatch?.matchId === data.matchId) {
                    State.currentScoringMatch = State.activeMatches[idx];
                    UI.updateScoreDisplay();
                }

                const justFinished = wasNotFinished && data.match.status === 'finished';
                if (justFinished && State.autoSendEnabled && State.nextUpMatchId &&
                    State.nextUpMatchId !== data.matchId) {
                    const queuedId = State.nextUpMatchId;
                    State.nextUpMatchId = null;
                    localStorage.removeItem('nextUpMatchId');
                    sendToIpponboard(queuedId);
                }

                UI.renderFightList();
                UI.renderBracketVisualization();
            }
        } else if (data.type === 'SIGNAL') {
            Scoring.triggerTimerSignal(data.signalType, false);
        } else if (data.type === 'CURRENT_MATCH_SET') {
            State.currentMatchId = data.matchId ?? null;
            UI.renderFightList();
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

        const males = upcoming.filter(m => m.gender === 'm').sort((a, b) => a.matchId - b.matchId);
        const females = upcoming.filter(m => m.gender === 'w').sort((a, b) => a.matchId - b.matchId);
        const others = upcoming.filter(m => m.gender !== 'm' && m.gender !== 'w').sort((a, b) => a.matchId - b.matchId);

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

        card.innerHTML = `
            <div class="fight-nr-badge"><div class="fight-num-circle">${match.fightNr}</div></div>
            <div class="category-box">
                <span class="table-label">Tisch ${match.tableId}</span>
                <span class="category-name">${match.categoryLabel || match.category}</span>
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
                <div class="status-badge ${match.status} ${isCurrent ? 'current' : ''}">${statusLabel}</div>
                ${restTag}
            </div>
            <div class="action-area">
                ${(match.status !== 'finished' && match.status !== 'bye') ?
                `<button class="btn-start" ${isReadOnly ? 'disabled' : ''} onclick="event.stopPropagation(); sendToIpponboard(${match.matchId})" title="An Ipponboard senden">Start</button>
                 <button class="btn-next-up ${State.nextUpMatchId === match.matchId ? 'active' : ''}" ${isReadOnly ? 'disabled' : ''} onclick="event.stopPropagation(); markAsNext(${match.matchId})" title="Als nächsten markieren — wird bei Auto-Send aktiv automatisch ans Ipponboard gesendet, sobald der aktuelle Kampf endet.">${State.nextUpMatchId === match.matchId ? '⏭ NÄCHSTER' : '⏭ Als nächster'}</button>
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
        const byKey = new Map();
        State.activeMatches.forEach(m => {
            const key = m.category;
            if (!byKey.has(key)) {
                const base = m.groupLabel || m.categoryLabel || m.category;
                byKey.set(key, m.bracketTypeLabel ? `${base} · ${m.bracketTypeLabel}` : base);
            }
        });
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
            const p1Won = m.status === 'finished' && m.winnerId != null && m.winnerId === m.p1.gpId;
            const p2Won = m.status === 'finished' && m.winnerId != null && m.winnerId === m.p2.gpId;

            const p1Real = (m.p1.firstName || m.p1.lastName) ? `${m.p1.firstName || ''} ${m.p1.lastName || ''}`.trim() : '';
            const p2Real = (m.p2.firstName || m.p2.lastName) ? `${m.p2.firstName || ''} ${m.p2.lastName || ''}`.trim() : '';
            const p1Proj = projectedName(m, 'p1');
            const p2Proj = projectedName(m, 'p2');
            // Empty TBD slots get filled with the projected winner from the feeder fight,
            // shown italic + dimmed so it's clearly "not yet locked in".
            const p1Display = p1Real || p1Proj || 'TBD';
            const p2Display = p2Real || p2Proj || 'TBD';
            const p1ProjectedClass = !p1Real && p1Proj ? ' projected' : '';
            const p2ProjectedClass = !p2Real && p2Proj ? ' projected' : '';

            const badge = isLB ? '<span class="match-node-badge match-node-badge--lb">LB</span>'
                         : isPool ? `<span class="match-node-badge match-node-badge--pool">Pool ${(m.poolIndex ?? 0) + 1}</span>`
                         : '';

            // Click on a bracket node opens the Result-picker dialog (Teil B).
            // Only fightable matches react — TBD / bye / future-only nodes show a hint.
            const p1HasFighter = m.p1.gpId != null;
            const p2HasFighter = m.p2.gpId != null;
            const isReady = p1HasFighter && p2HasFighter;
            const isFinished = m.status === 'finished';
            if (isReady && !isFinished) {
                node.classList.add('clickable');
                node.title = `Kampf #${m.fightNr} — Ergebnis eintragen`;
                node.onclick = () => openResultDialog(m.matchId);
            } else if (isReady && isFinished) {
                node.classList.add('clickable');
                node.title = `Kampf #${m.fightNr} (beendet) — Ergebnis ändern`;
                node.onclick = () => openResultDialog(m.matchId);
            } else if (!isReady && !isFinished) {
                node.classList.add('not-ready');
                node.title = 'Kampf noch nicht startbar — beide Kämpfer fehlen';
            }

            node.innerHTML = `
                <div class="match-node-header">
                    <span class="match-node-num">#${m.fightNr}</span>
                    ${badge}
                </div>
                <div class="match-node-p ${p1Won ? 'winner' : ''}${p1ProjectedClass}">
                    <span class="p-name">${p1Display}</span>
                    <span class="p-score-box">${p1Score}</span>
                </div>
                <div class="match-node-p ${p2Won ? 'winner' : ''}${p2ProjectedClass}">
                    <span class="p-name">${p2Display}</span>
                    <span class="p-score-box">${p2Score}</span>
                </div>
            `;
            viz.appendChild(node);
        });
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

        // If there are KO matches (winners of pools fight each other), render them
        // below the pool tables using a simplified vertical list. The full DAG
        // layout would be overkill for typically 1–3 KO fights here.
        if (koFights.length > 0) {
            const koSection = document.createElement('div');
            koSection.className = 'pool-ko-section';
            const heading = document.createElement('h3');
            heading.className = 'pool-section-title';
            heading.textContent = 'KO-Phase';
            koSection.appendChild(heading);

            koFights.sort((a, b) => (a.round ?? 0) - (b.round ?? 0) || (a.fightNr ?? 0) - (b.fightNr ?? 0))
                .forEach(m => koSection.appendChild(this.renderPoolKoCard(m)));

            viz.appendChild(koSection);
        }
    },

    /**
     * Build one round-robin table for a single pool.
     * Returns a DOM element.
     */
    renderRoundRobinPool(poolIndex, poolFights) {
        // Collect unique fighters as {gpId, displayName} pairs.
        const fighterByGp = new Map();
        const collect = (slot) => {
            if (!slot || slot.gpId == null) return;
            if (!fighterByGp.has(slot.gpId)) {
                const name = `${slot.firstName || ''} ${slot.lastName || ''}`.trim() || `#${slot.gpId}`;
                fighterByGp.set(slot.gpId, { gpId: slot.gpId, name, club: slot.club || '' });
            }
        };
        poolFights.forEach(m => { collect(m.p1); collect(m.p2); });

        // Stable ordering by last name then first name.
        const fighters = [...fighterByGp.values()].sort((a, b) => a.name.localeCompare(b.name, 'de'));

        // Lookup: pair (a,b) -> fight, with a-side score / b-side score
        const matchByPair = new Map();
        poolFights.forEach(m => {
            if (m.p1?.gpId == null || m.p2?.gpId == null) return;
            const k1 = `${m.p1.gpId}|${m.p2.gpId}`;
            const k2 = `${m.p2.gpId}|${m.p1.gpId}`;
            matchByPair.set(k1, { fight: m, swap: false });
            matchByPair.set(k2, { fight: m, swap: true });
        });

        const wrapper = document.createElement('div');
        wrapper.className = 'pool-table-wrapper';

        const title = document.createElement('h3');
        title.className = 'pool-section-title';
        title.textContent = `Pool ${poolIndex + 1}`;
        wrapper.appendChild(title);

        const table = document.createElement('table');
        table.className = 'pool-table';

        // Header row: empty cell + one column per fighter
        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        headRow.appendChild(document.createElement('th'));
        fighters.forEach(f => {
            const th = document.createElement('th');
            th.className = 'pool-header-cell';
            th.title = f.club ? `${f.name} (${f.club})` : f.name;
            th.textContent = f.name;
            headRow.appendChild(th);
        });
        thead.appendChild(headRow);
        table.appendChild(thead);

        // Body rows: one per fighter, with score cells
        const tbody = document.createElement('tbody');
        fighters.forEach(rowFighter => {
            const tr = document.createElement('tr');
            const rowHeader = document.createElement('th');
            rowHeader.className = 'pool-header-cell pool-row-header';
            rowHeader.title = rowFighter.club ? `${rowFighter.name} (${rowFighter.club})` : rowFighter.name;
            rowHeader.textContent = rowFighter.name;
            tr.appendChild(rowHeader);

            fighters.forEach(colFighter => {
                const td = document.createElement('td');
                td.className = 'pool-cell';
                if (rowFighter.gpId === colFighter.gpId) {
                    td.classList.add('pool-cell--diagonal');
                    td.textContent = '—';
                } else {
                    const entry = matchByPair.get(`${rowFighter.gpId}|${colFighter.gpId}`);
                    if (!entry) {
                        td.textContent = '·';
                        td.classList.add('pool-cell--missing');
                    } else {
                        const { fight, swap } = entry;
                        const rowSide = swap ? fight.p2 : fight.p1;
                        const colSide = swap ? fight.p1 : fight.p2;
                        const rs = rowSide.score?.points ?? 0;
                        const cs = colSide.score?.points ?? 0;
                        const isFinished = fight.status === 'finished';
                        const rowWon = isFinished && fight.winnerId === rowSide.gpId;
                        const colWon = isFinished && fight.winnerId === colSide.gpId;

                        td.title = `Kampf #${fight.fightNr} — ${rowSide.lastName} vs ${colSide.lastName}`;
                        td.textContent = isFinished ? `${rs}:${cs}` : (fight.status === 'live' ? '…' : '?');
                        td.classList.add('pool-cell--match');
                        if (rowWon) td.classList.add('pool-cell--row-won');
                        if (colWon) td.classList.add('pool-cell--col-won');

                        td.onclick = () => openResultDialog(fight.matchId);
                    }
                }
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
    // Toggle: clicking the same card again clears the marker.
    if (State.nextUpMatchId === matchId) {
        State.nextUpMatchId = null;
        localStorage.removeItem('nextUpMatchId');
    } else {
        State.nextUpMatchId = matchId;
        localStorage.setItem('nextUpMatchId', String(matchId));
    }
    UI.renderFightList();
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
