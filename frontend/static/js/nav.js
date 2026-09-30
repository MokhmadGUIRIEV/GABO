// Shared top navigation for every logged-in page (except the game table).
const NAV_LINKS = [
  { href: "/lobby.html", label: "Jouer" },
  { href: "/leaderboard.html", label: "Classement" },
  { href: "/history.html", label: "Historique" },
  { href: "/account.html", label: "Mon compte" },
];

// Checks the session, draws the nav, and returns the user (or null after
// redirecting to the login page / lobby).
async function initPage({ adminOnly = false } = {}) {
  let user;
  try {
    user = await Api.me();
  } catch (e) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = `/index.html?next=${next}`;
    return null;
  }
  if (adminOnly && !user.is_admin) {
    window.location.href = "/lobby.html";
    return null;
  }
  renderNav(user);
  return user;
}

function renderNav(user) {
  const links = NAV_LINKS.slice();
  if (user.is_admin) links.push({ href: "/admin.html", label: "Administration" });
  const here = window.location.pathname;
  document.getElementById("site-nav").innerHTML = `
    <a class="brand-link" href="/lobby.html">GABO</a>
    <nav class="nav-links">
      ${links.map((l) => `<a href="${l.href}" class="${here === l.href ? "active" : ""}">${l.label}</a>`).join("")}
    </nav>
    <div class="nav-user">
      <span class="hint">${esc(user.display_name)}</span>
      <button class="secondary small" id="logout-btn">Déconnexion</button>
    </div>
  `;
  document.getElementById("logout-btn").onclick = async () => {
    await Api.logout();
    window.location.href = "/index.html";
  };
}

function formatDate(iso) {
  return iso ? new Date(iso).toLocaleString("fr-FR") : "-";
}
