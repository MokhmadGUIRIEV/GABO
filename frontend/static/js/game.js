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

// Everything below happens directly on the table (seats, center piles) —
// there is no popup/overlay anywhere in this game. A few pieces of local UI
// state drive what's rendered:
//
// - `pendingPrivateContext`: the player id whose privately-scoped server
//   state (e.g. their drawn card, a card a power just revealed to them)
//   `publicState` currently holds. While set, `refreshPublic()` must not
//   overwrite it with the all-hidden public view.
// - `awaitingPeekChoiceFor` / `peekChosenIndices`: initial-peek card choice.
// - `revealingPlayerId` / `revealRemaining`: the seat currently showing a
//   timed reveal (initial peek or a power), with its countdown.
// - `powerSwapOwnIndex`: step 1 of the Valet/Dame power (which of the
//   owner's own cards was chosen, before picking the opponent's card).
// - `snapArmed`: whether clicking any player's card right now attempts a snap.
// - `turnArmedFor`: local-only "device has been passed to this player" flag
//   for the plain GABO/Piocher choice (no private data involved yet).
let pendingPrivateContext = null;
let awaitingPeekChoiceFor = null;
let peekChosenIndices = [];
let peekSubmitting = false;
let revealingPlayerId = null;
let revealRemaining = 0;
let powerSwapOwnIndex = null;
let snapArmed = false;
let turnArmedFor = null;

// Online games: this browser is one fixed player (`myId`), always sees the
// game from that player's point of view, and only ever acts as that player
// (the server enforces it anyway). Local games keep the pass & play flow.
let ONLINE = false;
let myId = null;
const SNAP_PHASES_ONLINE = new Set(["turn", "awaiting_decision", "power_pending"]);

function isHost() {
  return ONLINE && roomInfo && roomInfo.host_player_id === myId;
}

function log(message) {
  logEntries.unshift(`${new Date().toLocaleTimeString("fr-FR")} — ${message}`);
  const panel = document.getElementById("log-panel");
  panel.innerHTML = logEntries.slice(0, 40).map((l) => `<div>${esc(l)}</div>`).join("");
}

function call(action, payload = {}) {
  return new Promise((resolve, reject) => {
    const id = String(++reqCounter);
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ action, id, ...payload }));
  });
}

async function refreshPublic() {
  // Online, every fetch is already scoped to this player, so there is no
  // private view to protect from being overwritten by a shared one.
  if (!ONLINE && pendingPrivateContext) return;
  const resp = await call("get_state", { viewer_id: ONLINE ? "__me__" : "__public__" });
  publicState = resp.state;
  render();
}

// Close codes sent by the server when (re)connecting cannot work.
const WS_ROOM_GONE = 4404;
const WS_NOT_ALLOWED = new Set([4401, 4403]);
const MAX_RECONNECT_ATTEMPTS = 10;
let reconnectAttempts = 0;

function connectWs() {
  ws = new WebSocket(wsUrl(`/ws/rooms/${ROOM_CODE}`));
  ws.onopen = () => {
    log(reconnectAttempts > 0 ? "Reconnecté." : "Connecté à la salle.");
    reconnectAttempts = 0;
  };
  ws.onclose = (event) => {
    for (const { reject } of pending.values()) reject(new Error("Connexion perdue."));
    pending.clear();
    if (event.code === WS_ROOM_GONE) {
      showFatal("Cette partie n'existe plus (le serveur a peut-être redémarré).");
      return;
    }
    if (WS_NOT_ALLOWED.has(event.code)) {
      showFatal("Tu ne fais pas partie de cette partie.");
      return;
    }
    // Phones drop the connection when the screen locks: come back on our
    // own, the server gives the same seat back to the same account.
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      log("Connexion perdue. Recharge la page pour réessayer.");
      return;
    }
    reconnectAttempts += 1;
    log("Connexion perdue, reconnexion…");
    setTimeout(connectWs, Math.min(1000 * reconnectAttempts, 5000));
  };
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (ONLINE && msg.you) myId = msg.you;
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

// Raw (unescaped) name: wrap in esc() whenever it goes into HTML.
function playerName(id) {
  const fromState = publicState && publicState.players.find((p) => p.id === id);
  if (fromState) return fromState.name;
  if (roomInfo && roomInfo.player_names[id]) return roomInfo.player_names[id];
  return id || "";
}

function cardFace(card, extraClass = "") {
  if (!card) return `<div class="card empty ${extraClass}"></div>`;
  const isRed = RED_SUITS.has(card.suit);
  // Rank big and centered, suit small underneath: stays readable even on
  // the tiny cards around the table on a phone.
  return `<div class="card ${isRed ? "red" : ""} ${extraClass}">
    <span class="rank">${RANK_LABELS[card.rank] || card.rank}</span>
    <span class="suit">${SUIT_SYMBOLS[card.suit] || ""}</span>
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

  if (ONLINE) {
    // Online, everyone picks their 2 starting cards at the same time on
    // their own screen: arm the choice automatically, no device passing.
    const needsPeek =
      s.phase === "initial_peek" && myId && !s.players_peeked.includes(myId) && !revealingPlayerId && !peekSubmitting;
    if (needsPeek && awaitingPeekChoiceFor !== myId) {
      awaitingPeekChoiceFor = myId;
      peekChosenIndices = [];
    } else if (!needsPeek && awaitingPeekChoiceFor === myId && !peekSubmitting) {
      awaitingPeekChoiceFor = null;
      peekChosenIndices = [];
    }
  }

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

  document.getElementById("draw-pile-count").textContent = s.phase === "lobby" ? "" : `${s.draw_pile_count} cartes`;
  document.getElementById("discard-pile-card").innerHTML = s.top_discard
    ? cardFace(s.top_discard)
    : cardFace(null);

  const drawnPile = document.getElementById("drawn-pile");
  if (s.drawn_card) {
    // Face up only for whoever drew it; everyone else sees its back.
    drawnPile.style.display = "";
    document.getElementById("drawn-card-slot").innerHTML = s.drawn_card.hidden ? cardBack() : cardFace(s.drawn_card);
  } else {
    drawnPile.style.display = "none";
  }

  renderSeats(s);
  renderPhasePanel(s);
  renderGlobalActions(s);
  notifyMyTurn(s);
}

// Online: when it becomes this player's turn, buzz the phone and flag the
// browser tab, so nobody has to stare at the screen while others play.
const BASE_TITLE = document.title;
let wasMyTurn = false;
function notifyMyTurn(s) {
  const myTurn = ONLINE && s.phase === "turn" && s.current_player_id === myId;
  if (myTurn && !wasMyTurn && navigator.vibrate) navigator.vibrate([120, 60, 120]);
  document.title = myTurn ? `(À toi !) ${BASE_TITLE}` : BASE_TITLE;
  wasMyTurn = myTurn;
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
    { top: 70, left: 10, rotate: 0 },
    { top: 18, left: 24, rotate: 0 },
    { top: 18, left: 76, rotate: 0 },
    { top: 70, left: 90, rotate: 0 },
  ],
  6: [
    { top: 90, left: 50, rotate: 0 },
    { top: 72, left: 10, rotate: 0 },
    { top: 28, left: 10, rotate: 0 },
    { top: 8, left: 50, rotate: 180 },
    { top: 28, left: 90, rotate: 0 },
    { top: 72, left: 90, rotate: 0 },
  ],
};

// Phones: the center piles take almost the whole width of the table, so
// seats at mid-height would cover them. Move those up on narrow screens.
const NARROW_SEAT_LAYOUTS = {
  4: [
    { top: 88, left: 50, rotate: 0 },
    { top: 28, left: 12, rotate: 0 },
    { top: 12, left: 50, rotate: 180 },
    { top: 28, left: 88, rotate: 0 },
  ],
};

function seatLayout(n) {
  const narrow = window.matchMedia("(max-width: 560px)").matches;
  return (narrow && NARROW_SEAT_LAYOUTS[n]) || SEAT_LAYOUTS[n] || SEAT_LAYOUTS[6];
}

// What clicking a given seat's cards currently does, if anything. Returns a
// mode string or null. See `dispatchSeatAction` for what each mode triggers.
function computeSeatMode(s, playerId) {
  // While a timed reveal is animating (initial peek or a power), nothing
  // else is clickable — this avoids racing the reveal's own async flow with
  // a new action started underneath it before it has released its context.
  if (revealingPlayerId) return null;
  if (ONLINE) return computeOnlineSeatMode(s, playerId);
  if (playerId === awaitingPeekChoiceFor) return "peek";
  if (s.phase === "awaiting_decision" && playerId === s.current_player_id) return "swap-drawn";
  if (s.phase === "power_pending") {
    const owner = s.pending_power_owner;
    if (s.pending_power === "peek_own" && playerId === owner) return "power-own";
    if (s.pending_power === "peek_opponent" && playerId !== owner) return "power-opp";
    if (s.pending_power === "swap_and_peek") {
      if (powerSwapOwnIndex === null && playerId === owner) return "power-swap-own";
      if (powerSwapOwnIndex !== null && playerId !== owner) return "power-swap-target";
    }
  }
  if (snapArmed && s.phase === "turn") return "snap";
  return null;
}

// Online: only this player's own decisions are clickable. Opponents' cards
// are only clickable as *targets* of this player's own powers.
function computeOnlineSeatMode(s, playerId) {
  const mine = playerId === myId;
  if (snapArmed) return mine && SNAP_PHASES_ONLINE.has(s.phase) ? "snap" : null;
  if (mine && awaitingPeekChoiceFor === myId) return "peek";
  if (s.phase === "awaiting_decision" && mine && s.current_player_id === myId) return "swap-drawn";
  if (s.phase === "power_pending" && s.pending_power_owner === myId) {
    if (s.pending_power === "peek_own" && mine) return "power-own";
    if (s.pending_power === "peek_opponent" && !mine) return "power-opp";
    if (s.pending_power === "swap_and_peek") {
      if (powerSwapOwnIndex === null && mine) return "power-swap-own";
      if (powerSwapOwnIndex !== null && !mine) return "power-swap-target";
    }
  }
  return null;
}

function renderHandSlot(mode, playerId, idx, slot, chosen) {
  const inner = slot.hidden ? cardBack() : cardFace(slot.card);
  const chosenClass = chosen ? "chosen" : "";
  if (mode) {
    return `<button class="card-btn seat-action-slot ${chosenClass}" data-mode="${mode}" data-player="${esc(playerId)}" data-i="${idx}">${inner}</button>`;
  }
  return `<span class="seat-static-slot ${chosenClass}">${inner}</span>`;
}

function renderSeats(s) {
  const n = s.players.length;
  const layout = seatLayout(n);
  // Online, rotate the table so this player always sits at the bottom.
  const myIdx = ONLINE ? Math.max(0, s.players.findIndex((p) => p.id === myId)) : 0;
  const html = s.players
    .map((p, i) => {
      const seat = layout[(i - myIdx + n) % n] || layout[layout.length - 1];
      const isTurn = p.id === s.current_player_id && ["turn", "awaiting_decision", "power_pending"].includes(s.phase);
      const isSelecting = p.id === awaitingPeekChoiceFor;
      const isRevealing = p.id === revealingPlayerId;
      const mode = !p.eliminated ? computeSeatMode(s, p.id) : null;
      const ownChosenIdx =
        s.phase === "power_pending" && s.pending_power === "swap_and_peek" && p.id === s.pending_power_owner
          ? powerSwapOwnIndex
          : null;
      const peekChosenForThis = p.id === awaitingPeekChoiceFor ? peekChosenIndices : [];

      const classes = ["seat"];
      if (seat.rotate === 180) classes.push("rotate-180");
      if (isTurn) classes.push("is-turn");
      if (p.eliminated) classes.push("eliminated");
      if (isSelecting) classes.push("selecting");
      if (isRevealing) classes.push("revealing");
      if (mode) classes.push("interactive");
      if (ONLINE && p.id === myId) classes.push("me");

      const hand = p.hand
        .map((slot, idx) => {
          const chosen = peekChosenForThis.includes(idx) || idx === ownChosenIdx;
          return renderHandSlot(mode, p.id, idx, slot, chosen);
        })
        .join("");

      const name = esc(p.name) + (ONLINE && p.id === myId ? " (toi)" : "");
      const header = isRevealing
        ? `${name} — regarde bien ! (${revealRemaining}s)`
        : isSelecting
        ? `${name} — choisis 2 cartes (${peekChosenIndices.length}/2)`
        : `${name}${p.eliminated ? " ☠" : ""} — ${p.score} pts`;
      const emptyHand = s.phase === "lobby" ? "" : '<span class="hint">Pas de cartes</span>';

      return `<div class="${classes.join(" ")}" style="top:${seat.top}%; left:${seat.left}%;">
        <div class="seat-header">${header}</div>
        <div class="hand-row">${hand || emptyHand}</div>
      </div>`;
    })
    .join("");
  document.getElementById("seats-container").innerHTML = html;
  keepSeatsOnScreen();

  document.querySelectorAll(".seat-action-slot").forEach((btn) => {
    btn.onclick = () => {
      dispatchSeatAction(btn.dataset.mode, btn.dataset.player, Number(btn.dataset.i));
    };
  });
}

// On a narrow phone, the hands of the side seats can stick out of the
// screen: nudge those seats back inside (a few pixels of margin).
function keepSeatsOnScreen() {
  const margin = 4;
  const screenWidth = document.documentElement.clientWidth;
  document.querySelectorAll("#seats-container .seat").forEach((seat) => {
    const rect = seat.querySelector(".hand-row").getBoundingClientRect();
    const header = seat.querySelector(".seat-header").getBoundingClientRect();
    const left = Math.min(rect.left, header.left);
    const right = Math.max(rect.right, header.right);
    let shift = 0;
    if (left < margin) shift = margin - left;
    else if (right > screenWidth - margin) shift = screenWidth - margin - right;
    if (shift) seat.style.marginLeft = `${shift}px`;
  });
}

// Phone turned sideways: seats need re-placing.
window.addEventListener("resize", () => {
  if (publicState) renderSeats(publicState);
});

function dispatchSeatAction(mode, playerId, idx) {
  const s = publicState;
  if (mode === "peek") return handlePeekSlotClick(idx);
  if (mode === "swap-drawn") return handleSwapDrawn(playerId, idx);
  if (mode === "power-own") return handlePowerPeekOwn(playerId, idx);
  if (mode === "power-opp") return handlePowerPeekOpponent(s.pending_power_owner, playerId, idx);
  if (mode === "power-swap-own") {
    powerSwapOwnIndex = idx;
    render();
    return;
  }
  if (mode === "power-swap-target") return handlePowerSwapTarget(s.pending_power_owner, powerSwapOwnIndex, playerId, idx);
  if (mode === "snap") return handleSnapClick(playerId, idx);
}

function renderGlobalActions(s) {
  const el = document.getElementById("global-actions");
  el.innerHTML = "";
  // Local: only between turns (one shared screen). Online: any time the
  // discard pile has a card, since everyone has their own screen.
  const me = ONLINE ? s.players.find((p) => p.id === myId) : null;
  const snapAllowed = ONLINE
    ? SNAP_PHASES_ONLINE.has(s.phase) && me && !me.eliminated && me.hand.length > 0
    : s.phase === "turn";
  if (revealingPlayerId || !snapAllowed || !s.top_discard) {
    snapArmed = false;
    return;
  }
  const btn = document.createElement("button");
  btn.className = "danger";
  btn.textContent = snapArmed
    ? ONLINE
      ? "Annuler — clique une de tes cartes"
      : "Annuler — clique une carte sur la table"
    : "⚡ Carte identique ! (snap)";
  btn.onclick = () => {
    snapArmed = !snapArmed;
    render();
  };
  el.appendChild(btn);
}

function renderPhasePanel(s) {
  const panel = document.getElementById("phase-panel");
  panel.innerHTML = "";

  if (revealingPlayerId) {
    panel.innerHTML = ONLINE
      ? `<p class="hint">Mémorise bien ! (${revealRemaining}s)</p>`
      : `<p class="hint">${esc(playerName(revealingPlayerId))} mémorise sa carte... (${revealRemaining}s)</p>`;
    return;
  }

  if (ONLINE) {
    renderOnlinePhasePanel(s, panel);
    return;
  }

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
      const name = esc(playerName(awaitingPeekChoiceFor || revealingPlayerId));
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
    if (turnArmedFor !== current.id) {
      const btn = document.createElement("button");
      btn.textContent = `Passe l'appareil à ${current.name} et clique ici pour jouer`;
      btn.onclick = () => {
        turnArmedFor = current.id;
        render();
      };
      panel.appendChild(btn);
      return;
    }
    panel.innerHTML = `<p class="hint">Assure-toi que les autres ne regardent pas l'écran de ${esc(current.name)}.</p>`;
    if (!s.gabo_caller_id) {
      const gaboBtn = document.createElement("button");
      gaboBtn.className = "danger";
      gaboBtn.textContent = "Dire GABO !";
      gaboBtn.onclick = () => {
        turnArmedFor = null;
        handleCallGabo(current.id);
      };
      panel.appendChild(gaboBtn);
    }
    const drawBtn = document.createElement("button");
    drawBtn.textContent = "Piocher une carte";
    drawBtn.onclick = () => {
      turnArmedFor = null;
      handleDraw(current.id);
    };
    panel.appendChild(drawBtn);
    return;
  }

  if (s.phase === "awaiting_decision") {
    panel.innerHTML = `<p class="hint">Défausse la carte piochée (au centre de la table), ou clique une de tes cartes ci-dessus pour l'échanger contre elle.</p>`;
    const btn = document.createElement("button");
    btn.textContent = "Défausser la carte piochée";
    btn.onclick = () => handleDiscardDrawn(s.current_player_id);
    panel.appendChild(btn);
    return;
  }

  if (s.phase === "power_pending") {
    const owner = s.pending_power_owner;
    let hint = "";
    if (s.pending_power === "peek_own") hint = "Clique une de tes cartes sur la table pour la regarder 5 secondes.";
    else if (s.pending_power === "peek_opponent") hint = "Clique une carte d'un adversaire sur la table pour la regarder 5 secondes.";
    else if (s.pending_power === "swap_and_peek") {
      hint =
        powerSwapOwnIndex === null
          ? "Clique une de tes cartes à échanger contre celle d'un adversaire."
          : "Clique maintenant la carte d'un adversaire à échanger contre la tienne.";
    }
    panel.innerHTML = `<p class="hint">${hint}</p>`;
    const skipBtn = document.createElement("button");
    skipBtn.className = "secondary";
    skipBtn.textContent = "Ne pas utiliser le pouvoir";
    skipBtn.onclick = () => handleSkipPower(owner);
    panel.appendChild(skipBtn);
    return;
  }

  if (s.phase === "round_over") {
    renderRoundSummaryPanel(s.last_round_summary);
    return;
  }

  if (s.phase === "game_over") {
    renderGameOverPanel(s);
  }
}

// ---------------------------------------------------------------------
// Online phase panel: this browser is always the same player, so it only
// shows that player's own actions, and what the others are doing.
// ---------------------------------------------------------------------

const POWER_LABELS = {
  peek_own: "7/8 : regarder une de ses cartes",
  peek_opponent: "9/10 : regarder une carte adverse",
  swap_and_peek: "Valet/Dame : échanger une carte",
};

function addButton(panel, text, onClick, className = "") {
  const btn = document.createElement("button");
  if (className) btn.className = className;
  btn.textContent = text;
  btn.onclick = onClick;
  panel.appendChild(btn);
  return btn;
}

async function startRound() {
  try {
    await call("start_round");
  } catch (e) {
    log("Erreur: " + e.message);
  }
}

function renderWaitingRoom(s, panel) {
  const link = `${window.location.origin}/game.html?code=${encodeURIComponent(ROOM_CODE)}`;
  const players = s.players
    .map((p) => {
      const tags = [];
      if (p.id === roomInfo.host_player_id) tags.push("hôte");
      if (p.id === myId) tags.push("toi");
      return `<li>${esc(p.name)}${tags.length ? ` <span class="hint">(${tags.join(", ")})</span>` : ""}</li>`;
    })
    .join("");
  panel.innerHTML = `
    <h3 style="margin-top:0;">Salle d'attente</h3>
    <p>Code de la partie : <span class="room-code">${esc(ROOM_CODE)}</span></p>
    <p class="hint">Envoie ce code à tes amis (ils le saisissent dans « Rejoindre une partie »), ou directement ce lien :</p>
    <div class="row">
      <input type="text" id="share-link" readonly value="${esc(link)}" style="max-width:420px;" />
      <button class="secondary" id="copy-link-btn">Copier le lien</button>
    </div>
    <p>Joueurs connectés (${s.players.length}/6) :</p>
    <ul class="waiting-list">${players}</ul>
  `;
  document.getElementById("copy-link-btn").onclick = async () => {
    const input = document.getElementById("share-link");
    try {
      await navigator.clipboard.writeText(input.value);
      log("Lien copié.");
    } catch (e) {
      input.select();
    }
  };
  if (isHost()) {
    const enough = s.players.length >= 2;
    const btn = addButton(panel, enough ? "Lancer la partie" : "En attente d'au moins un autre joueur…", startRound);
    btn.disabled = !enough;
  } else {
    panel.insertAdjacentHTML("beforeend", `<p class="hint">En attente que l'hôte lance la partie…</p>`);
  }
}

function renderOnlinePhasePanel(s, panel) {
  if (s.phase === "lobby") {
    renderWaitingRoom(s, panel);
    return;
  }
  if (s.phase === "round_over") {
    renderRoundSummaryPanel(s.last_round_summary);
    return;
  }
  if (s.phase === "game_over") {
    renderGameOverPanel(s);
    return;
  }

  const me = s.players.find((p) => p.id === myId);
  if (me && me.eliminated) {
    panel.innerHTML = `<p class="hint">Tu ne joues plus (100 points atteints) — tu peux suivre la partie jusqu'au bout.</p>`;
    return;
  }

  if (s.phase === "initial_peek") {
    if (awaitingPeekChoiceFor === myId) {
      panel.innerHTML = `<p class="hint">Clique 2 de tes cartes (en bas de la table) pour les regarder pendant 5 secondes.</p>`;
    } else {
      const waiting = s.players
        .filter((p) => !p.eliminated && !s.players_peeked.includes(p.id))
        .map((p) => esc(p.name));
      panel.innerHTML = `<p class="hint">En attente des autres joueurs : ${waiting.join(", ") || "…"}</p>`;
    }
    return;
  }

  const currentName = esc(playerName(s.current_player_id));

  if (s.phase === "turn") {
    if (s.current_player_id !== myId) {
      panel.innerHTML = `<p class="hint">C'est au tour de ${currentName}…</p>`;
      return;
    }
    panel.innerHTML = `<p class="hint"><strong>C'est ton tour !</strong></p>`;
    if (!s.gabo_caller_id) addButton(panel, "Dire GABO !", () => handleCallGabo(myId), "danger");
    addButton(panel, "Piocher une carte", () => handleDraw(myId));
    return;
  }

  if (s.phase === "awaiting_decision") {
    if (s.current_player_id !== myId) {
      panel.innerHTML = `<p class="hint">${currentName} a pioché une carte et réfléchit…</p>`;
      return;
    }
    panel.innerHTML = `<p class="hint">Défausse la carte piochée (au centre de la table), ou clique une de tes cartes pour l'échanger contre elle.</p>`;
    addButton(panel, "Défausser la carte piochée", () => handleDiscardDrawn(myId));
    return;
  }

  if (s.phase === "power_pending") {
    const owner = s.pending_power_owner;
    if (owner !== myId) {
      panel.innerHTML = `<p class="hint">${esc(playerName(owner))} utilise un pouvoir (${POWER_LABELS[s.pending_power] || ""})…</p>`;
      return;
    }
    let hint = "";
    if (s.pending_power === "peek_own") hint = "Clique une de tes cartes pour la regarder 5 secondes.";
    else if (s.pending_power === "peek_opponent") hint = "Clique une carte d'un adversaire pour la regarder 5 secondes.";
    else if (s.pending_power === "swap_and_peek") {
      hint =
        powerSwapOwnIndex === null
          ? "Clique une de tes cartes à échanger contre celle d'un adversaire."
          : "Clique maintenant la carte d'un adversaire à échanger contre la tienne.";
    }
    panel.innerHTML = `<p class="hint">${hint}</p>`;
    addButton(panel, "Ne pas utiliser le pouvoir", () => handleSkipPower(myId), "secondary");
  }
}

// ---------------------------------------------------------------------
// Shared timed-reveal helper (initial peek + powers)
// ---------------------------------------------------------------------

async function runRevealCountdown(seatPlayerId) {
  revealingPlayerId = seatPlayerId;
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
  revealingPlayerId = null;
}

// ---------------------------------------------------------------------
// Initial peek: the player clicks 2 of their own (still hidden) cards,
// directly at their seat on the table. Those 2 flip face-up in place for
// 5 seconds, then hide again automatically. No popup involved.
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
  pendingPrivateContext = playerId;
  awaitingPeekChoiceFor = null;
  peekSubmitting = true;
  try {
    const resp = await call("peek_initial", { player_id: playerId, indices });
    publicState = resp.state;
    peekSubmitting = false;
    await runRevealCountdown(playerId);
  } catch (e) {
    log("Erreur: " + e.message);
  } finally {
    peekSubmitting = false;
    peekChosenIndices = [];
    // Only release the context if nothing else has taken it over in the
    // meantime (defensive: this coroutine's own render-gating should
    // already prevent that, but never clobber a newer flow's state).
    if (pendingPrivateContext === playerId) {
      pendingPrivateContext = null;
      await refreshPublic();
    }
  }
}

// ---------------------------------------------------------------------
// Turn actions: GABO / draw / discard / swap
// ---------------------------------------------------------------------

async function handleCallGabo(playerId) {
  try {
    await call("call_gabo", { player_id: playerId });
    log(`${playerName(playerId)} a dit GABO !`);
  } catch (e) {
    log("Erreur: " + e.message);
  }
  await refreshPublic();
}

async function handleDraw(playerId) {
  try {
    const resp = await call("draw_card", { player_id: playerId });
    pendingPrivateContext = playerId;
    publicState = resp.state;
    render();
  } catch (e) {
    log("Erreur: " + e.message);
  }
}

async function handleDiscardDrawn(playerId) {
  try {
    const resp = await call("discard_drawn", { player_id: playerId });
    log(`${playerName(playerId)} a défaussé sa carte piochée.`);
    publicState = resp.state;
    if (resp.state.phase === "power_pending" && resp.state.pending_power_owner === playerId) {
      render();
    } else if (pendingPrivateContext === playerId) {
      pendingPrivateContext = null;
      await refreshPublic();
    }
  } catch (e) {
    log("Erreur: " + e.message);
  }
}

async function handleSwapDrawn(playerId, idx) {
  try {
    const resp = await call("swap_drawn", { player_id: playerId, hand_index: idx });
    log(`${playerName(playerId)} a échangé sa carte piochée.`);
    publicState = resp.state;
  } catch (e) {
    log("Erreur: " + e.message);
  } finally {
    if (pendingPrivateContext === playerId) {
      pendingPrivateContext = null;
      await refreshPublic();
    }
  }
}

// ---------------------------------------------------------------------
// Powers: 7/8 (peek own), 9/10 (peek opponent), Valet/Dame (swap + peek)
// ---------------------------------------------------------------------

async function handlePowerPeekOwn(playerId, idx) {
  try {
    const resp = await call("use_power_peek_own", { player_id: playerId, hand_index: idx });
    publicState = resp.state;
    await runRevealCountdown(playerId);
  } catch (e) {
    log("Erreur: " + e.message);
  } finally {
    if (pendingPrivateContext === playerId) {
      pendingPrivateContext = null;
      await refreshPublic();
    }
  }
}

async function handlePowerPeekOpponent(ownerId, targetId, idx) {
  try {
    const resp = await call("use_power_peek_opponent", {
      player_id: ownerId,
      target_player_id: targetId,
      hand_index: idx,
    });
    publicState = resp.state;
    await runRevealCountdown(targetId);
  } catch (e) {
    log("Erreur: " + e.message);
  } finally {
    if (pendingPrivateContext === ownerId) {
      pendingPrivateContext = null;
      await refreshPublic();
    }
  }
}

async function handlePowerSwapTarget(ownerId, ownIndex, targetId, targetIndex) {
  try {
    const resp = await call("use_power_swap_and_peek", {
      player_id: ownerId,
      own_index: ownIndex,
      target_player_id: targetId,
      target_index: targetIndex,
    });
    publicState = resp.state;
    await runRevealCountdown(ownerId);
  } catch (e) {
    log("Erreur: " + e.message);
  } finally {
    powerSwapOwnIndex = null;
    if (pendingPrivateContext === ownerId) {
      pendingPrivateContext = null;
      await refreshPublic();
    }
  }
}

async function handleSkipPower(playerId) {
  try {
    await call("skip_power", { player_id: playerId });
    log(`${playerName(playerId)} n'utilise pas son pouvoir.`);
  } catch (e) {
    log("Erreur: " + e.message);
  } finally {
    powerSwapOwnIndex = null;
    if (pendingPrivateContext === playerId) {
      pendingPrivateContext = null;
      await refreshPublic();
    }
  }
}

// ---------------------------------------------------------------------
// Snap: click any player's card directly on the table to try to match it
// with the top of the discard pile.
// ---------------------------------------------------------------------

async function handleSnapClick(playerId, idx) {
  snapArmed = false;
  try {
    await call("snap_attempt", { player_id: playerId, hand_index: idx });
    log(`${playerName(playerId)} tente un snap.`);
  } catch (e) {
    log("Erreur: " + e.message);
  }
  await refreshPublic();
}

// ---------------------------------------------------------------------
// Round / game over summaries — rendered inline in the phase panel, never
// as a popup.
// ---------------------------------------------------------------------

function renderRoundSummaryPanel(summary) {
  const panel = document.getElementById("phase-panel");
  if (!summary) {
    panel.innerHTML = "";
    return;
  }
  const rows = Object.keys(summary.hand_sums)
    .map((pid) => {
      const delta = summary.score_deltas[pid];
      return `<tr><td>${esc(playerName(pid))}${pid === summary.caller_id ? " (GABO)" : ""}</td><td>${summary.hand_sums[pid]}</td><td>+${delta}</td></tr>`;
    })
    .join("");
  const caller = esc(playerName(summary.caller_id));
  // Online, only the host moves the table on to the next round.
  const nextRound = !ONLINE || isHost()
    ? `<button id="next-round-btn">Manche suivante</button>`
    : `<p class="hint">En attente que l'hôte lance la manche suivante…</p>`;
  panel.innerHTML = `
    <h3 style="margin-top:0;">${summary.caller_won ? `${caller} a réussi son GABO !` : `${caller} a raté son GABO !`}</h3>
    <table class="summary">
      <thead><tr><th>Joueur</th><th>Somme des cartes</th><th>Points ajoutés</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${summary.newly_eliminated.length ? `<p class="hint">Éliminé(s) : ${summary.newly_eliminated.map((pid) => esc(playerName(pid))).join(", ")}</p>` : ""}
    <div class="row" style="justify-content:center; margin-top:10px;">${nextRound}</div>
  `;
  const btn = document.getElementById("next-round-btn");
  if (btn) btn.onclick = startRound;
}

function renderGameOverPanel(s) {
  const panel = document.getElementById("phase-panel");
  const sorted = s.players.slice().sort((a, b) => a.score - b.score);
  const rows = sorted
    .map((p) => `<tr><td>${esc(p.name)}${p.id === s.winner_id ? " 🏆" : ""}</td><td>${p.score}</td></tr>`)
    .join("");
  panel.innerHTML = `
    <h3 style="margin-top:0;">Partie terminée !</h3>
    <p>${esc(playerName(s.winner_id))} remporte la partie 🎉</p>
    <table class="summary">
      <thead><tr><th>Joueur</th><th>Score final</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <div class="row" style="justify-content:center;">
      <a class="link" href="/lobby.html"><button>Retour au lobby</button></a>
    </div>
  `;
}

// ---------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------

function showFatal(message) {
  document.getElementById("phase-label").textContent = message;
  document.getElementById("phase-panel").innerHTML =
    `<p class="hint">${esc(message)}</p><a class="link" href="/lobby.html">Retour au lobby</a>`;
}

// Keep the phone screen on during a game (it would otherwise lock and drop
// the connection). Silently skipped by browsers that don't support it.
let wakeLock = null;
async function keepScreenAwake() {
  if (!("wakeLock" in navigator)) return;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
  } catch (e) {
    wakeLock = null;
  }
}
document.addEventListener("visibilitychange", () => {
  // The lock is released automatically when the page is hidden.
  if (document.visibilityState === "visible") keepScreenAwake();
});

(async () => {
  try {
    currentUser = await Api.me();
  } catch (e) {
    // Come back to this exact game after logging in (shared invite links).
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = `/index.html?next=${next}`;
    return;
  }
  try {
    roomInfo = await Api.getRoom(ROOM_CODE);
  } catch (e) {
    showFatal("Salle introuvable.");
    return;
  }
  if (roomInfo.mode === "online") {
    ONLINE = true;
    if (!roomInfo.you) {
      // Opened an invite link: take a seat automatically.
      try {
        roomInfo = await Api.joinRoom(ROOM_CODE);
      } catch (e) {
        showFatal(e.message);
        return;
      }
    }
    myId = roomInfo.you;
  }
  connectWs();
  keepScreenAwake();
  // Free hosting puts the server to sleep after a while without HTTP
  // traffic (which would wipe in-memory games): keep it awake while a game
  // page is open, and keep the WebSocket from being closed as idle.
  setInterval(() => {
    fetch("/api/health").catch(() => {});
    if (ws && ws.readyState === WebSocket.OPEN) {
      call("get_state", { viewer_id: ONLINE ? "__me__" : "__public__" }).catch(() => {});
    }
  }, 4 * 60 * 1000);
})();
