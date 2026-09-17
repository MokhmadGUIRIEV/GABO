const RANK_LABELS = {
  as: "A", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7",
  "8": "8", "9": "9", "10": "10", valet: "V", dame: "D", roi: "R",
};
const SUIT_SYMBOLS = { coeur: "♥", carreau: "♦", pique: "♠", trefle: "♣" };
const RED_SUITS = new Set(["coeur", "carreau"]);

const params = new URLSearchParams(window.location.search);
const ROOM_CODE = params.get("code");
if (!ROOM_CODE) {
  window.location.href = "/lobby.html";
}

let ws = null;
let reqCounter = 0;
const pending = new Map();
let publicState = null;
let roomInfo = null; // {code, player_ids, player_names}
let currentUser = null;
const logEntries = [];

// Initial-peek state lives entirely on the table (no popup): while a player
// is choosing which 2 of their cards to look at, or while their chosen cards
// are flipped face-up for the 5s memorization window, we render a special
// scoped view instead of the normal all-hidden public one, and we stop
// `refreshPublic` from clobbering it in the meantime.
let awaitingPeekChoiceFor = null;
let peekChosenIndices = [];
let revealingPlayerId = null;
let revealRemaining = 0;
let peekOverrideActive = false;

function log(message) {
  logEntries.unshift(`${new Date().toLocaleTimeString("fr-FR")} — ${message}`);
  const panel = document.getElementById("log-panel");
  panel.innerHTML = logEntries.slice(0, 40).map((l) => `<div>${l}</div>`).join("");
}

function call(action, payload = {}) {
  return new Promise((resolve, reject) => {
    const id = String(++reqCounter);
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ action, id, ...payload }));
  });
}

async function refreshPublic() {
  if (peekOverrideActive || awaitingPeekChoiceFor) return;
  const resp = await call("get_state", { viewer_id: "__public__" });
  publicState = resp.state;
  render();
}

function connectWs() {
  ws = new WebSocket(wsUrl(`/ws/rooms/${ROOM_CODE}`));
  ws.onopen = () => log("Connecté à la salle.");
  ws.onclose = () => log("Connexion perdue. Rechargez la page pour réessayer.");
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.type === "error") reject(new Error(msg.message));
      else resolve(msg);
    }
    if (msg.type === "table_updated") {
      refreshPublic().catch((e) => log("Erreur: " + e.message));
    }
  };
}

function playerName(id) {
  if (!roomInfo) return id;
  return roomInfo.player_names[id] || id;
}

function cardFace(card, extraClass = "") {
  if (!card) return `<div class="card empty ${extraClass}"></div>`;
  const isRed = RED_SUITS.has(card.suit);
  return `<div class="card ${isRed ? "red" : ""} ${extraClass}">
    <div>${RANK_LABELS[card.rank] || card.rank}</div>
    <div class="suit">${SUIT_SYMBOLS[card.suit] || ""}</div>
  </div>`;
}

function cardBack(extraClass = "") {
  return `<div class="card back ${extraClass}">?</div>`;
}

// ---------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------

function render() {
  if (!publicState) return;
  const s = publicState;

  document.getElementById("room-code").textContent = ROOM_CODE;
  document.getElementById("round-label").textContent = s.round_number ? `Manche ${s.round_number}` : "";

  const phaseLabels = {
    lobby: "En attente du lancement",
    initial_peek: "Observation initiale des cartes",
    turn: `Tour de ${playerName(s.current_player_id)}`,
    awaiting_decision: `${playerName(s.current_player_id)} décide de sa carte piochée`,
    power_pending: `Pouvoir en cours pour ${playerName(s.pending_power_owner)}`,
    round_over: "Manche terminée",
    game_over: "Partie terminée",
  };
  document.getElementById("phase-label").textContent = phaseLabels[s.phase] || s.phase;

  document.getElementById("draw-pile-count").textContent = `${s.draw_pile_count} cartes`;
  document.getElementById("discard-pile-card").innerHTML = s.top_discard
    ? cardFace(s.top_discard)
    : cardFace(null);

  renderSeats(s);
  renderPhasePanel(s);
  renderGlobalActions(s);
}

// Fixed seating around the table, per player count. Seat 0 is always at the
// bottom (closest to the shared device), others distributed around it like a
// real round table. The seat exactly opposite (2p, or the "far" seat on 4p/6p)
// is rotated 180° so it visually faces the bottom seat, like sitting across
// from someone at a table.
const SEAT_LAYOUTS = {
  2: [
    { top: 88, left: 50, rotate: 0 },
    { top: 12, left: 50, rotate: 180 },
  ],
  3: [
    { top: 88, left: 50, rotate: 0 },
    { top: 22, left: 12, rotate: 0 },
    { top: 22, left: 88, rotate: 0 },
  ],
  4: [
    { top: 88, left: 50, rotate: 0 },
    { top: 50, left: 8, rotate: 0 },
    { top: 12, left: 50, rotate: 180 },
    { top: 50, left: 92, rotate: 0 },
  ],
  5: [
    { top: 90, left: 50, rotate: 0 },
    { top: 62, left: 8, rotate: 0 },
    { top: 15, left: 24, rotate: 0 },
    { top: 15, left: 76, rotate: 0 },
    { top: 62, left: 92, rotate: 0 },
  ],
  6: [
    { top: 90, left: 50, rotate: 0 },
    { top: 68, left: 6, rotate: 0 },
    { top: 24, left: 6, rotate: 0 },
    { top: 8, left: 50, rotate: 180 },
    { top: 24, left: 94, rotate: 0 },
    { top: 68, left: 94, rotate: 0 },
  ],
};

function renderSeats(s) {
  const layout = SEAT_LAYOUTS[s.players.length] || SEAT_LAYOUTS[6];
  const html = s.players
    .map((p, i) => {
      const seat = layout[i] || layout[layout.length - 1];
      const isTurn = p.id === s.current_player_id && ["turn", "awaiting_decision", "power_pending"].includes(s.phase);
      const isSelecting = p.id === awaitingPeekChoiceFor;
      const isRevealing = p.id === revealingPlayerId;
      const classes = ["seat"];
      if (seat.rotate === 180) classes.push("rotate-180");
      if (isTurn) classes.push("is-turn");
      if (p.eliminated) classes.push("eliminated");
      if (isSelecting) classes.push("selecting");
      if (isRevealing) classes.push("revealing");

      let hand;
      if (isSelecting) {
        hand = p.hand
          .map((slot, idx) => {
            const chosen = peekChosenIndices.includes(idx);
            return `<button class="card-btn seat-peek-slot ${chosen ? "chosen" : ""}" data-i="${idx}">${cardBack()}</button>`;
          })
          .join("");
      } else {
        hand = p.hand.map((slot) => (slot.hidden ? cardBack() : cardFace(slot.card))).join("");
      }

      const header = isRevealing
        ? `${p.name} — regarde bien ! (${revealRemaining}s)`
        : isSelecting
        ? `${p.name} — choisis 2 cartes (${peekChosenIndices.length}/2)`
        : `${p.name}${p.eliminated ? " ☠" : ""} — ${p.score} pts`;

      return `<div class="${classes.join(" ")}" style="top:${seat.top}%; left:${seat.left}%;">
        <div class="seat-header">${header}</div>
        <div class="hand-row">${hand || '<span class="hint">Pas de cartes</span>'}</div>
      </div>`;
    })
    .join("");
  document.getElementById("seats-container").innerHTML = html;

  document.querySelectorAll(".seat-peek-slot").forEach((btn) => {
    btn.onclick = () => handlePeekSlotClick(Number(btn.dataset.i));
  });
}

function renderGlobalActions(s) {
  const el = document.getElementById("global-actions");
  el.innerHTML = "";
  if (s.phase === "lobby") return;
  if (s.phase === "round_over" || s.phase === "game_over") return;
  if (!s.top_discard) return;

  const btn = document.createElement("button");
  btn.className = "danger";
  btn.textContent = "⚡ Carte identique ! (snap)";
  btn.onclick = openSnapDialog;
  el.appendChild(btn);
}

function renderPhasePanel(s) {
  const panel = document.getElementById("phase-panel");
  panel.innerHTML = "";

  if (s.phase === "lobby") {
    const btn = document.createElement("button");
    btn.textContent = "Distribuer les cartes et commencer";
    btn.onclick = async () => {
      try {
        await call("start_round");
        log("Nouvelle manche distribuée.");
      } catch (e) {
        log("Erreur: " + e.message);
      }
    };
    panel.appendChild(btn);
    return;
  }

  if (s.phase === "initial_peek") {
    const remaining = s.players.filter((p) => !p.eliminated && !s.players_peeked.includes(p.id));
    if (remaining.length === 0) {
      panel.innerHTML = `<p class="hint">Tout le monde a regardé ses cartes, la manche démarre...</p>`;
      return;
    }
    if (awaitingPeekChoiceFor || revealingPlayerId) {
      const name = playerName(awaitingPeekChoiceFor || revealingPlayerId);
      panel.innerHTML = `<p class="hint">Assure-toi que les autres ne regardent pas l'écran de ${name}. Clique 2 de ses cartes sur la table ci-dessus.</p>`;
      return;
    }
    const next = remaining[0];
    const btn = document.createElement("button");
    btn.textContent = `Passe l'appareil à ${next.name} et clique ici`;
    btn.onclick = () => {
      awaitingPeekChoiceFor = next.id;
      peekChosenIndices = [];
      render();
    };
    panel.innerHTML = `<p class="hint">${remaining.length} joueur(s) doivent encore regarder 2 de leurs cartes.</p>`;
    panel.appendChild(btn);
    return;
  }

  if (s.phase === "turn") {
    const current = s.players.find((p) => p.id === s.current_player_id);
    const btn = document.createElement("button");
    btn.textContent = `Passe l'appareil à ${current.name} et clique ici pour jouer`;
    btn.onclick = () => openTurnGate(current.id);
    panel.appendChild(btn);
    return;
  }

  if (s.phase === "awaiting_decision") {
    const btn = document.createElement("button");
    btn.textContent = `${playerName(s.current_player_id)} : reprends ta décision`;
    btn.onclick = () => openTurnGate(s.current_player_id);
    panel.appendChild(btn);
    return;
  }

  if (s.phase === "power_pending") {
    const btn = document.createElement("button");
    btn.textContent = `Passe l'appareil à ${playerName(s.pending_power_owner)} pour utiliser son pouvoir`;
    btn.onclick = () => openPowerGate(s.pending_power_owner);
    panel.appendChild(btn);
    return;
  }

  if (s.phase === "round_over") {
    showRoundSummary(s.last_round_summary);
    return;
  }

  if (s.phase === "game_over") {
    showGameOverSummary(s);
  }
}

// ---------------------------------------------------------------------
// Gate overlay helpers
// ---------------------------------------------------------------------

function showGate(html) {
  document.getElementById("gate-container").innerHTML = `
    <div class="gate-overlay"><div class="gate-box">${html}</div></div>
  `;
}

function closeGate() {
  document.getElementById("gate-container").innerHTML = "";
}

function showModal(html) {
  document.getElementById("modal-container").innerHTML = `
    <div class="modal-overlay"><div class="modal-box">${html}</div></div>
  `;
}

function closeModal() {
  document.getElementById("modal-container").innerHTML = "";
}

// ---------------------------------------------------------------------
// Initial peek: happens directly on the table, in the player's own seat —
// no popup. The player clicks 2 of their own (still hidden) cards to choose
// which ones to look at; those 2 flip face-up in place for 5 seconds.
// ---------------------------------------------------------------------

function handlePeekSlotClick(index) {
  if (!awaitingPeekChoiceFor) return;
  if (peekChosenIndices.includes(index)) return;
  peekChosenIndices.push(index);
  if (peekChosenIndices.length < 2) {
    render();
    return;
  }
  startInitialPeekReveal(awaitingPeekChoiceFor, peekChosenIndices.slice());
}

async function startInitialPeekReveal(playerId, indices) {
  peekOverrideActive = true;
  awaitingPeekChoiceFor = null;
  try {
    const resp = await call("peek_initial", { player_id: playerId, indices });
    publicState = resp.state;
    revealingPlayerId = playerId;
    revealRemaining = 5;
    render();
    await new Promise((resolve) => {
      const interval = setInterval(() => {
        revealRemaining -= 1;
        if (revealRemaining <= 0) {
          clearInterval(interval);
          resolve();
        } else {
          render();
        }
      }, 1000);
    });
  } catch (e) {
    log("Erreur: " + e.message);
  } finally {
    revealingPlayerId = null;
    peekChosenIndices = [];
    peekOverrideActive = false;
    await refreshPublic();
  }
}

// ---------------------------------------------------------------------
// Turn gate: draw / GABO / discard / swap
// ---------------------------------------------------------------------

async function openTurnGate(playerId) {
  let resp;
  try {
    resp = await call("get_state", { viewer_id: playerId });
  } catch (e) {
    log("Erreur: " + e.message);
    return;
  }
  renderTurnGate(playerId, resp.state);
}

function handSlotsHtml(player, { clickable = false, name = "hand-slot" } = {}) {
  return player.hand
    .map((slot, i) => {
      const inner = slot.hidden ? cardBack() : cardFace(slot.card);
      if (clickable) {
        return `<div class="hand-slot"><button class="card-btn ${name}" data-i="${i}">${inner}</button><div class="slot-index">${i + 1}</div></div>`;
      }
      return `<div class="hand-slot">${inner}<div class="slot-index">${i + 1}</div></div>`;
    })
    .join("");
}

function renderTurnGate(playerId, state) {
  const me = state.players.find((p) => p.id === playerId);
  const canCallGabo = !state.gabo_caller_id;
  const drawnCard = state.drawn_card && !state.drawn_card.hidden ? state.drawn_card : null;

  let body;
  if (state.phase === "turn") {
    body = `
      <h2>Tour de ${playerName(playerId)}</h2>
      <p class="hint">Assure-toi que les autres joueurs ne regardent pas.</p>
      <div class="row" style="justify-content:center;">${handSlotsHtml(me)}</div>
      <div class="row" style="justify-content:center; margin-top:20px;">
        ${canCallGabo ? '<button id="gabo-btn" class="danger">Dire GABO !</button>' : ""}
        <button id="draw-btn">Piocher une carte</button>
      </div>
    `;
  } else if (state.phase === "awaiting_decision" && drawnCard) {
    body = `
      <h2>${playerName(playerId)} a pioché :</h2>
      <div class="row" style="justify-content:center;">${cardFace(drawnCard)}</div>
      <p class="hint">Défausse-la, ou clique sur une de tes cartes pour l'échanger contre elle.</p>
      <div class="row" style="justify-content:center;">${handSlotsHtml(me, { clickable: true, name: "swap-slot" })}</div>
      <div class="row" style="justify-content:center; margin-top:16px;">
        <button id="discard-btn">Défausser la carte piochée</button>
      </div>
    `;
  } else {
    body = `<p>État inattendu, recharge la page.</p>`;
  }

  showGate(`
    <div>${body}</div>
    <div class="row" style="justify-content:center; margin-top:14px;">
      <button class="secondary" onclick="closeGate()">Fermer</button>
    </div>
  `);

  const gaboBtn = document.getElementById("gabo-btn");
  if (gaboBtn) {
    gaboBtn.onclick = async () => {
      try {
        await call("call_gabo", { player_id: playerId });
        log(`${playerName(playerId)} a dit GABO !`);
        closeGate();
      } catch (e) {
        log("Erreur: " + e.message);
      }
    };
  }
  const drawBtn = document.getElementById("draw-btn");
  if (drawBtn) {
    drawBtn.onclick = async () => {
      try {
        const resp = await call("draw_card", { player_id: playerId });
        renderTurnGate(playerId, resp.state);
      } catch (e) {
        log("Erreur: " + e.message);
      }
    };
  }
  const discardBtn = document.getElementById("discard-btn");
  if (discardBtn) {
    discardBtn.onclick = async () => {
      try {
        const resp = await call("discard_drawn", { player_id: playerId });
        log(`${playerName(playerId)} a défaussé sa carte piochée.`);
        if (resp.state.phase === "power_pending" && resp.state.pending_power_owner === playerId) {
          renderPowerGateBody(playerId, resp.state);
        } else {
          closeGate();
        }
      } catch (e) {
        log("Erreur: " + e.message);
      }
    };
  }
  document.querySelectorAll(".swap-slot").forEach((btn) => {
    btn.onclick = async () => {
      try {
        const i = Number(btn.dataset.i);
        await call("swap_drawn", { player_id: playerId, hand_index: i });
        log(`${playerName(playerId)} a échangé sa carte piochée.`);
        closeGate();
      } catch (e) {
        log("Erreur: " + e.message);
      }
    };
  });
}

// ---------------------------------------------------------------------
// Power gate
// ---------------------------------------------------------------------

async function openPowerGate(playerId) {
  let resp;
  try {
    resp = await call("get_state", { viewer_id: playerId });
  } catch (e) {
    log("Erreur: " + e.message);
    return;
  }
  renderPowerGateBody(playerId, resp.state);
}

function renderPowerGateBody(playerId, state) {
  const me = state.players.find((p) => p.id === playerId);
  const power = state.pending_power;
  const opponents = state.players.filter((p) => p.id !== playerId && !p.eliminated);

  let body = "";
  if (power === "peek_own") {
    body = `
      <h2>${playerName(playerId)} — pouvoir 7/8</h2>
      <p class="hint">Choisis une de tes cartes à regarder 5 secondes (ou passe).</p>
      <div class="row" style="justify-content:center;">${handSlotsHtml(me, { clickable: true, name: "power-own-slot" })}</div>
    `;
  } else if (power === "peek_opponent") {
    body = `
      <h2>${playerName(playerId)} — pouvoir 9/10</h2>
      <p class="hint">Choisis un adversaire puis une de ses cartes à regarder 5 secondes (ou passe).</p>
      <div class="row" style="justify-content:center; gap:26px;">
        ${opponents
          .map(
            (op) => `<div>
              <div class="hint" style="text-align:center;margin-bottom:6px;">${op.name}</div>
              <div class="row">${handSlotsHtml(op, { clickable: true, name: "power-opp-slot" }).replaceAll(
                'class="card-btn power-opp-slot"',
                `class="card-btn power-opp-slot" data-target="${op.id}"`
              )}</div>
            </div>`
          )
          .join("")}
      </div>
    `;
  } else if (power === "swap_and_peek") {
    body = `
      <h2>${playerName(playerId)} — pouvoir Valet/Dame</h2>
      <p class="hint">1. Clique une de tes cartes. 2. Clique la carte d'un adversaire à échanger contre elle. (ou passe)</p>
      <div class="hint" id="swap-step-label">Étape 1/2 : choisis ta carte.</div>
      <div class="row" style="justify-content:center; margin:10px 0;">
        <div>
          <div class="hint">Toi</div>
          <div class="row">${handSlotsHtml(me, { clickable: true, name: "power-own-select" })}</div>
        </div>
      </div>
      <div class="row" style="justify-content:center; gap:26px;">
        ${opponents
          .map(
            (op) => `<div>
              <div class="hint" style="text-align:center;margin-bottom:6px;">${op.name}</div>
              <div class="row">${handSlotsHtml(op, { clickable: true, name: "power-opp-select" }).replaceAll(
                'class="card-btn power-opp-select"',
                `class="card-btn power-opp-select" data-target="${op.id}"`
              )}</div>
            </div>`
          )
          .join("")}
      </div>
    `;
  }

  showGate(`
    <div>${body}</div>
    <div class="row" style="justify-content:center; margin-top:16px;">
      <button id="skip-power-btn" class="secondary">Ne pas utiliser le pouvoir</button>
    </div>
  `);

  document.getElementById("skip-power-btn").onclick = async () => {
    try {
      await call("skip_power", { player_id: playerId });
      log(`${playerName(playerId)} n'utilise pas son pouvoir.`);
      closeGate();
    } catch (e) {
      log("Erreur: " + e.message);
    }
  };

  if (power === "peek_own") {
    document.querySelectorAll(".power-own-slot").forEach((btn) => {
      btn.onclick = async () => {
        try {
          const i = Number(btn.dataset.i);
          const resp = await call("use_power_peek_own", { player_id: playerId, hand_index: i });
          const revealed = resp.state.players.find((p) => p.id === playerId).hand[i];
          showPowerReveal(playerId, [revealed.card]);
        } catch (e) {
          log("Erreur: " + e.message);
        }
      };
    });
  } else if (power === "peek_opponent") {
    document.querySelectorAll(".power-opp-slot").forEach((btn) => {
      btn.onclick = async () => {
        try {
          const i = Number(btn.dataset.i);
          const target = btn.dataset.target;
          const resp = await call("use_power_peek_opponent", {
            player_id: playerId,
            target_player_id: target,
            hand_index: i,
          });
          const revealed = resp.state.players.find((p) => p.id === target).hand[i];
          showPowerReveal(playerId, [revealed.card]);
        } catch (e) {
          log("Erreur: " + e.message);
        }
      };
    });
  } else if (power === "swap_and_peek") {
    let ownIndex = null;
    document.querySelectorAll(".power-own-select").forEach((btn) => {
      btn.onclick = () => {
        ownIndex = Number(btn.dataset.i);
        document.querySelectorAll(".power-own-select").forEach((b) => (b.style.outline = ""));
        btn.style.outline = "3px solid var(--accent)";
        const label = document.getElementById("swap-step-label");
        if (label) label.textContent = "Étape 2/2 : choisis la carte de l'adversaire à échanger.";
      };
    });
    document.querySelectorAll(".power-opp-select").forEach((btn) => {
      btn.onclick = async () => {
        if (ownIndex === null) {
          log("Choisis d'abord ta propre carte.");
          return;
        }
        try {
          const targetIndex = Number(btn.dataset.i);
          const target = btn.dataset.target;
          const resp = await call("use_power_swap_and_peek", {
            player_id: playerId,
            own_index: ownIndex,
            target_player_id: target,
            target_index: targetIndex,
          });
          const revealed = resp.state.players.find((p) => p.id === playerId).hand[ownIndex];
          showPowerReveal(playerId, [revealed.card]);
        } catch (e) {
          log("Erreur: " + e.message);
        }
      };
    });
  }
}

function showPowerReveal(playerId, cards) {
  showGate(`
    <h2>${playerName(playerId)}, mémorise bien !</h2>
    <div class="row" style="justify-content:center;">${cards.map((c) => cardFace(c)).join("")}</div>
    <div class="countdown" id="power-countdown">5</div>
  `);
  let remaining = 5;
  const interval = setInterval(() => {
    remaining -= 1;
    const el = document.getElementById("power-countdown");
    if (el) el.textContent = String(Math.max(remaining, 0));
    if (remaining <= 0) {
      clearInterval(interval);
      closeGate();
      log(`${playerName(playerId)} a utilisé son pouvoir.`);
    }
  }, 1000);
}

// ---------------------------------------------------------------------
// Snap
// ---------------------------------------------------------------------

function openSnapDialog() {
  const active = publicState.players.filter((p) => !p.eliminated);
  showGate(`
    <h2>Carte identique !</h2>
    <p class="hint">Qui pense avoir une carte de même valeur que le dessus de la défausse (${RANK_LABELS[publicState.top_discard.rank]}) ?</p>
    <div class="row" id="snap-player-choices" style="justify-content:center;"></div>
  `);
  document.getElementById("snap-player-choices").innerHTML = active
    .map((p) => `<button class="secondary" data-id="${p.id}">${p.name}</button>`)
    .join("");
  document.querySelectorAll("#snap-player-choices button").forEach((btn) => {
    btn.onclick = () => openSnapSlotChoice(btn.dataset.id);
  });
}

function openSnapSlotChoice(playerId) {
  const player = publicState.players.find((p) => p.id === playerId);
  showGate(`
    <h2>${playerName(playerId)}</h2>
    <p class="hint">Clique la carte que tu penses jeter (de mémoire, sans regarder les autres cartes).</p>
    <div class="row" id="snap-slots" style="justify-content:center;"></div>
  `);
  document.getElementById("snap-slots").innerHTML = player.hand
    .map((_, i) => `<button class="card-btn" data-i="${i}">${cardBack()}</button>`)
    .join("");
  document.querySelectorAll("#snap-slots button").forEach((btn) => {
    btn.onclick = async () => {
      try {
        const i = Number(btn.dataset.i);
        await call("snap_attempt", { player_id: playerId, hand_index: i });
        log(`${playerName(playerId)} tente un snap.`);
        closeGate();
      } catch (e) {
        log("Erreur: " + e.message);
        closeGate();
      }
    };
  });
}

// ---------------------------------------------------------------------
// Round / game over summaries
// ---------------------------------------------------------------------

function showRoundSummary(summary) {
  if (!summary) return;
  const rows = Object.keys(summary.hand_sums)
    .map((pid) => {
      const delta = summary.score_deltas[pid];
      return `<tr><td>${playerName(pid)}${pid === summary.caller_id ? " (GABO)" : ""}</td><td>${summary.hand_sums[pid]}</td><td>+${delta}</td></tr>`;
    })
    .join("");
  showModal(`
    <h2>${summary.caller_won ? `${playerName(summary.caller_id)} a réussi son GABO !` : `${playerName(summary.caller_id)} a raté son GABO !`}</h2>
    <table class="summary">
      <thead><tr><th>Joueur</th><th>Somme des cartes</th><th>Points ajoutés</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${summary.newly_eliminated.length ? `<p class="hint">Éliminé(s) : ${summary.newly_eliminated.map(playerName).join(", ")}</p>` : ""}
    <div class="row" style="justify-content:center; margin-top:10px;">
      <button id="next-round-btn">Manche suivante</button>
    </div>
  `);
  document.getElementById("next-round-btn").onclick = async () => {
    closeModal();
    try {
      await call("start_round");
    } catch (e) {
      log("Erreur: " + e.message);
    }
  };
}

function showGameOverSummary(s) {
  const sorted = s.players.slice().sort((a, b) => a.score - b.score);
  const rows = sorted
    .map((p) => `<tr><td>${p.name}${p.id === s.winner_id ? " 🏆" : ""}</td><td>${p.score}</td></tr>`)
    .join("");
  showModal(`
    <h2>Partie terminée !</h2>
    <p>${playerName(s.winner_id)} remporte la partie 🎉</p>
    <table class="summary">
      <thead><tr><th>Joueur</th><th>Score final</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="row" style="justify-content:center;">
      <a class="link" href="/lobby.html"><button>Retour au lobby</button></a>
    </div>
  `);
}

// ---------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------

(async () => {
  try {
    currentUser = await Api.me();
  } catch (e) {
    window.location.href = "/index.html";
    return;
  }
  try {
    roomInfo = await Api.getRoom(ROOM_CODE);
  } catch (e) {
    log("Salle introuvable.");
    return;
  }
  connectWs();
})();
