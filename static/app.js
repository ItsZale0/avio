/* Carpool Hunter - frontend vanilla JS */

const state = {
  watches: [],
  selectedId: null,
  watch: null,
  offers: [],
  links: [],
  runs: [],
  alerts: [],
  sources: [],
  settings: {},
  ai: {},
  plan: null,
  planAlternatives: [],
  questions: [],
  tab: "best",
  editingId: null,
  lastAlertId: Number(localStorage.getItem("ch_last_alert") || 0),
  booted: false,
  polling: null,
  carsharingZones: null,
  showZones: true,
  // True se l'app non chiede l'accesso: niente schermata di login.
  authDisabled: false,
  // Ce' una chiave segreta attiva? Serve per le impostazioni.
  hasKey: false,
  locked: false,
};

// Base dell'API: vuota quando frontend e backend sono sullo stesso host.
// Su Cloudflare Pages punta al tunnel (es. https://carpool.miodominio.it).
let API_BASE = "";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

const euro = (value) =>
  value == null ? "—" : "€" + Number(value).toFixed(2).replace(".", ",");

const dtf = new Intl.DateTimeFormat("it-IT", {
  weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
});
const dfShort = new Intl.DateTimeFormat("it-IT", { day: "2-digit", month: "short" });
const tf = new Intl.DateTimeFormat("it-IT", { hour: "2-digit", minute: "2-digit" });

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : dtf.format(d);
}
function fmtDay(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : dfShort.format(d);
}
function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? "—" : tf.format(d);
}
const ALT_PROFILE = {
  economico: "Più economico",
  veloce: "Più veloce",
  comodo: "Meno cambi",
  alternativa: "Alternativa",
  proposta: "Proposta",
};

// Gli abbonamenti che l'agente conosce: se la tratta e' coperta, il
// biglietto non lo paghi.
const PASS_TYPES = [
  { id: "trenitalia_regionale", label: "Trenitalia regionale", hint: "Abbonamento o carnet regionale Trenitalia" },
  { id: "trenitalia_tutti", label: "Trenitalia (tutti i treni)", hint: "Abbonamento annuale Trenitalia, anche Frecce" },
  { id: "carnet_regionale", label: "Carnet 10 biglietti regionali", hint: "Consuma 1 biglietto del carnet" },
  { id: "carnet_bus", label: "Carnet bus regionali", hint: "Consuma 1 biglietto del carnet" },
  { id: "abbonamento_unico", label: "Abbonamento regionale unico", hint: "Treni e bus regionali" },
  { id: "urbano", label: "Abbonamento urbano", hint: "Bus, metro e tram cittadini" },
  { id: "carnet_urbano", label: "Carnet 10 biglietti urbani", hint: "Consuma 1 biglietto del carnet" },
];

function fmtDuration(minutes) {
  if (!minutes) return "";
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return h ? `${h}h ${String(m).padStart(2, "0")}m` : `${m}m`;
}
function relative(iso) {
  if (!iso) return "—";
  const diff = new Date(iso).getTime() - Date.now();
  if (isNaN(diff)) return "—";
  const mins = Math.round(diff / 60000);
  if (Math.abs(mins) < 1) return "adesso";
  if (mins > 0) {
    if (mins < 60) return `tra ${mins} min`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `tra ${hours} h`;
    return `tra ${Math.round(hours / 24)} g`;
  }
  const past = Math.abs(mins);
  if (past < 60) return `${past} min fa`;
  const hours = Math.round(past / 60);
  if (hours < 24) return `${hours} h fa`;
  return `${Math.round(hours / 24)} g fa`;
}
function toLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const BADGES = {
  cheapest: "Più economico",
  best_value: "Miglior scelta",
  fastest: "Più veloce",
  closest: "Più vicino",
  price_drop: "Ribasso",
  under_budget: "Sotto budget",
  coperto: "Coperto",
  spezzato: "2 biglietti",
  diretto: "Diretto",
  estimate: "Stima",
  orario: "Orario",
};

const MODE_LABEL = {
  rideshare: "Passaggio",
  bus: "Autobus",
  train: "Treno",
  tram: "Tram",
  flight: "Volo",
  carsharing: "Carsharing",
  transit: "Mezzi pubblici",
};

const MODE_COLOR = {
  rideshare: "#e8833a",
  bus: "#2f7d4f",
  train: "#3563c9",
  tram: "#8b5cf6",
  flight: "#0e9aa7",
  carsharing: "#d19b12",
  transit: "#7b8794",
};

const MAX_MAP_OFFERS = 40;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* Il puntatore "fresco" del backend, letto direttamente dal repo: il tunnel
   cambia URL a ogni avvio e la config.json di GitHub Pages si aggiorna solo
   dopo il deploy (un paio di minuti). Questo file e' subito disponibile. */
const RAW_BACKEND =
  "https://raw.githubusercontent.com/ItsZale0/carpool-intelligence/main/backend.json";

/* Tutti i backend conosciuti, in ordine di preferenza. */
let API_FALLBACKS = [];

async function api(path, options = {}) {
  let response;
  let lastError = null;

  // Il tunnel puo' essere riavviato in qualunque momento e cambiare URL:
  // se la richiesta fallisce a livello di rete si prova un altro backend
  // conosciuto (config.json di Pages, puntatore fresco su GitHub).
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      response = await fetch(API_BASE + path, {
        headers: { "Content-Type": "application/json" },
        // La sessione vive in un cookie HttpOnly: va sempre mandato.
        credentials: "include",
        ...options,
      });
      break;
    } catch (error) {
      lastError = error;
      // 1) Prova subito un altro backend noto.
      const alternative = API_FALLBACKS.find((base) => base !== API_BASE);
      if (alternative) {
        API_BASE = alternative;
        API_FALLBACKS = [alternative, ...API_FALLBACKS.filter((b) => b !== alternative)];
        continue;
      }
      // 2) Rileggi i puntatori: potrebbero essersi aggiornati.
      if (attempt >= 1) {
        await loadRuntimeConfig();
        const fresh = API_FALLBACKS.find((base) => base !== API_BASE);
        if (fresh) {
          API_BASE = fresh;
          API_FALLBACKS = [fresh, ...API_FALLBACKS.filter((b) => b !== fresh)];
          continue;
        }
      }
      if (attempt >= 3) {
        scheduleApiRetry();
        throw new Error("Server non raggiungibile");
      }
      await sleep(1500 * (attempt + 1));
    }
  }
  if (!response) throw lastError || new Error("Server non raggiungibile");

  if (response.status === 401) {
    // Accesso richiesto: torna alla schermata di accesso. Con l'accesso
    // disattivato non dovrebbe accadere, ma meglio coprire il caso.
    if (!state.authDisabled) {
      showAuthScreen();
      throw new Error("Accesso richiesto");
    }
    throw new Error("Accesso richiesto");
  }
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch (_) { /* ignore */ }
    throw new Error(detail || `Errore ${response.status}`);
  }
  if (response.status === 204) return null;
  const text = await response.text();
  // ngrok free manda la pagina di avviso al posto dell'app: si riconosce
  // e si spiega, invece di dare un errore JSON incomprensibile.
  if (text.includes("You are about to visit")) {
    throw new Error(
      "Il tunnel ngrok chiede una conferma al browser. Apri l'indirizzo del " +
      "tunnel in una nuova scheda, clicca \"Visit Site\" e poi torna qui."
    );
  }
  try {
    return JSON.parse(text);
  } catch (_) {
    throw new Error("Risposta non valida dal server");
  }
}

async function loadRuntimeConfig() {
  // Se la pagina e' servita dall'app stessa (uso locale), l'API e' alla
  // stessa origine: la si usa direttamente, senza passare dal tunnel.
  try {
    const probe = await fetch("api/auth/status?ts=" + Date.now(), {
      cache: "no-store",
      credentials: "include",
    });
    if (probe.ok) {
      const body = await probe.json();
      if (body && typeof body === "object" && "locked" in body) {
        API_BASE = location.origin;
        API_FALLBACKS = [location.origin];
        showApiHint();
        return;
      }
    }
  } catch (_) { /* non e' l'app stessa: si prosegue */ }

  // Prova il file di config locale; su Pages contiene l'URL del backend.
  // Prima il path relativo: su GitHub Pages la root e' /<repo>/, non "/".
  // `cache: "no-store"` e i query param servono a non prendere una copia
  // vecchia: se il tunnel e' stato riavviato, l'URL e' cambiato e quello
  // precedente non porta piu' da nessuna parte.
  //
  // In coda si legge anche il puntatore "fresco" pubblicato dal tunnel sul
  // branch: copre la finestra in cui la config.json di Pages non e' ancora
  // stata rideployata.
  const stamp = Date.now();
  const found = [];
  const candidates = [`config.json?ts=${stamp}`, "/config.json"];
  for (const path of candidates) {
    try {
      const response = await fetch(path, { cache: "no-store" });
      if (!response.ok) continue;
      const config = await response.json();
      if (config && typeof config.apiBase === "string" && config.apiBase) {
        found.push({
          base: config.apiBase.replace(/\/$/, ""),
          updated: config.updated_at || "",
        });
      }
      break;
    } catch (_) { /* prova il successivo */ }
  }
  try {
    const response = await fetch(`${RAW_BACKEND}?ts=${stamp}`, { cache: "no-store" });
    if (response.ok) {
      const config = await response.json();
      if (config && typeof config.apiBase === "string" && config.apiBase) {
        found.push({
          base: config.apiBase.replace(/\/$/, ""),
          updated: config.updated_at || "",
        });
      }
    }
  } catch (_) { /* senza rete resta solo la config.json */ }

  // Via i duplicati, tenendo la data piu' recente per ogni URL.
  const perBase = new Map();
  for (const item of found) {
    const noto = perBase.get(item.base);
    if (!noto || String(item.updated) > String(noto.updated)) {
      perBase.set(item.base, item);
    }
  }
  const unici = [...perBase.values()].sort(
    (a, b) => String(b.updated).localeCompare(String(a.updated))
  );
  if (unici.length) {
    API_FALLBACKS = unici.map((item) => item.base);
    API_BASE = API_FALLBACKS[0];
    showApiHint();
  }
}

/* Se la pagina e' su un dominio diverso dall'API, il browser potrebbe
   bloccare il cookie di sessione: si mostra l'indirizzo diretto del backend,
   dove frontend e API sono sulla stessa origine e tutto funziona. */
function showApiHint() {
  const hint = $("#loginApiHint");
  if (!hint) return;
  if (!API_BASE || API_BASE === location.origin) {
    hint.textContent = "";
    return;
  }
  hint.innerHTML =
    "Se non riesci a entrare, apri l'app direttamente qui: " +
    `<a href="${esc(API_BASE)}" target="_blank" rel="noopener">${esc(API_BASE)}</a>`;
}

/* Se l'API non risponde, il tunnel potrebbe essere stato riavviato con un
   URL nuovo: si ricarica la pagina, con attese crescenti, per prendere il
   config.json fresco. */
let _apiRetry = 0;
function scheduleApiRetry() {
  if (_apiRetry) return;
  const delays = [5000, 15000, 30000];
  let index = 0;
  const retry = () => {
    if (index >= delays.length) return;
    const delay = delays[index++];
    const notice = document.querySelector("#loginHint");
    if (notice) {
      notice.textContent =
        "Server non raggiungibile: l'agente sul PC potrebbe essere spento, " +
        "o il tunnel bloccato dalla rete. " +
        `Riprovo tra ${Math.round(delay / 1000)} secondi…`;
      notice.className = "field-hint err";
    }
    _apiRetry = setTimeout(() => {
      _apiRetry = 0;
      window.location.reload();
    }, delay);
  };
  retry();
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 5200);
}

function openModal(id) { $("#" + id).classList.remove("hidden"); }
function closeModal(id) { $("#" + id).classList.add("hidden"); }

/* ------------------------------------------------------------------ */
/* Accesso                                                             */
/* ------------------------------------------------------------------ */
/* ------------------------------------------------------------------ */
/* Accesso: una password, o una chiave segreta nell'URL                 */
/* ------------------------------------------------------------------ */
function showAuthScreen() {
  const screen = $("#authScreen");
  if (screen) screen.classList.remove("hidden");
  document.querySelector(".layout")?.classList.add("hidden");
  document.querySelector(".topbar")?.classList.add("hidden");
  stopPolling();
}

function hideAuthScreen() {
  const screen = $("#authScreen");
  if (screen) screen.classList.add("hidden");
  document.querySelector(".layout")?.classList.remove("hidden");
  document.querySelector(".topbar")?.classList.remove("hidden");
}

async function submitLogin(event) {
  event.preventDefault();
  const hint = $("#loginHint");
  const button = $("#btnLogin");
  const password = $("#aLoginPass").value;
  button.disabled = true;
  hint.textContent = "";
  try {
    await api("/api/auth/unlock", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    $("#aLoginPass").value = "";
    // La chiave nell'URL non serve piu': si pulisce dalla barra.
    if (location.search.includes("key=")) {
      const url = new URL(location.href);
      url.searchParams.delete("key");
      history.replaceState({}, "", url);
    }
    await bootApp();
  } catch (error) {
    hint.textContent = error.message;
    hint.className = "field-hint err";
  } finally {
    button.disabled = false;
  }
}

/* Se l'URL contiene la chiave segreta, si sblocca da soli. */
async function tryKeyFromUrl() {
  const key = new URL(location.href).searchParams.get("key");
  if (!key) return false;
  try {
    await api("/api/auth/unlock", {
      method: "POST",
      body: JSON.stringify({ password: key }),
    });
    const url = new URL(location.href);
    url.searchParams.delete("key");
    history.replaceState({}, "", url);
    return true;
  } catch (_) {
    return false;
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch (_) { /* la sessione e' comunque da buttare */ }
  state.watches = [];
  state.selectedId = null;
  state.watch = null;
  showAuthScreen();
}

/* ------------------------------------------------------------------ */
/* Bootstrap                                                          */
/* ------------------------------------------------------------------ */
document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  registerServiceWorker();
  await loadRuntimeConfig();
  try {
    // Se l'URL ha la chiave segreta, si sblocca senza digitare nulla.
    if (await tryKeyFromUrl()) {
      await bootApp();
      return;
    }
    const authState = await api("/api/auth/status");
    if (!authState.locked) {
      // Nessuna password impostata: l'app si apre direttamente.
      state.authDisabled = true;
      state.user = authState.user || null;
      await bootApp();
      return;
    }
    if (!authState.unlocked) {
      showAuthScreen();
      return;
    }
    state.user = authState.user;
    await bootApp();
  } catch (error) {
    showAuthScreen();
    const hint = $("#loginHint");
    hint.textContent =
      "Server non raggiungibile: il tunnel potrebbe essere stato riavviato. " +
      "Riprovo fra poco…";
    hint.className = "field-hint err";
    scheduleApiRetry();
  }
});

async function bootApp() {
  hideAuthScreen();
  try {
    const status = await loadStatus();
    state.sources = status.sources || [];
    state.user = status.user || state.user;
    // Lo stato dell'accesso serve per mostrare la sezione "Chiave segreta".
    try {
      const access = await api("/api/auth/status");
      state.hasKey = !!access.has_key;
      state.locked = !!access.locked;
    } catch (_) { /* senza stato accesso: si prosegue */ }
    await Promise.all([loadWatches(), loadSettings()]);
    if (state.watches.length) {
      await selectWatch(state.watches[0].id);
    } else {
      showEmpty(true);
    }
    await loadAlerts(true);
    state.booted = true;
    startPolling();
  } catch (error) {
    toast("Impossibile caricare i dati: " + error.message, "error");
    $("#agentStatus").classList.add("offline");
    $("#agentText").textContent = "offline";
  }
}

function bindEvents() {
  // Accesso
  $("#loginForm").addEventListener("submit", submitLogin);

  $("#btnNew").addEventListener("click", () => openWatchModal(null));
  $("#btnEmptyNew").addEventListener("click", () => openWatchModal(null));
  $("#btnSettings").addEventListener("click", openSettings);
  $("#btnPromos").addEventListener("click", () => openPromos(false));
  $("#btnRefreshPromos").addEventListener("click", () => openPromos(true));
  $("#btnAlerts").addEventListener("click", () => { openModal("alertsDrawer"); loadAlerts(false); });
  $("#btnReadAll").addEventListener("click", markAllRead);
  $("#btnLogout").addEventListener("click", logout);

  $("#watchList").addEventListener("click", (event) => {
    const item = event.target.closest(".watch-item");
    if (item) selectWatch(Number(item.dataset.id));
  });

  $("#tabs").addEventListener("click", (event) => {
    const button = event.target.closest(".tab");
    if (!button) return;
    state.tab = button.dataset.tab;
    $$(".tab").forEach((tab) => tab.classList.toggle("active", tab === button));
    renderTab();
  });

  $("#btnRunNow").addEventListener("click", runNow);
  $("#btnToggleWatch").addEventListener("click", toggleWatch);
  $("#btnEditWatch").addEventListener("click", () => openWatchModal(state.watch));
  $("#btnDeleteWatch").addEventListener("click", deleteWatch);

  $("#watchForm").addEventListener("submit", submitWatch);
  $("#settingsForm").addEventListener("submit", submitSettings);
  $("#btnTestTelegram").addEventListener("click", testTelegram);
  $("#btnTestEmail").addEventListener("click", testEmail);
  $("#btnAiFill").addEventListener("click", aiFill);
  $("#btnTestAi").addEventListener("click", testAi);
  $("#btnAiModels").addEventListener("click", listAiModels);
  $("#btnGtfsUpdate").addEventListener("click", updateGtfs);
  $("#btnPushEnable").addEventListener("click", enablePush);
  $("#btnPushDisable").addEventListener("click", disablePush);
  $("#btnPushTest").addEventListener("click", testPush);
  $("#btnSetAccess").addEventListener("click", setAccessPassword);
  $("#btnClearAccess").addEventListener("click", clearAccessPassword);
  $("#btnNewKey").addEventListener("click", generateKey);
  $("#btnDelKey").addEventListener("click", deleteKey);
  $("#btnTelegramLink").addEventListener("click", telegramLink);
  $("#btnTelegramConfirm").addEventListener("click", telegramConfirm);
  $("#questionsCard").addEventListener("click", (event) => {
    const button = event.target.closest("[data-answer]");
    if (!button) return;
    answerQuestion(Number(button.dataset.qid), button.dataset.answer);
  });
  // Risposta aperta (invio col tasto Invio o col bottone).
  $("#questionsCard").addEventListener("submit", (event) => {
    const form = event.target.closest(".q-free");
    if (!form) return;
    event.preventDefault();
    answerFree(Number(form.dataset.qid), form);
  });

  document.addEventListener("click", (event) => {
    const closer = event.target.closest("[data-close]");
    if (closer) closeModal(closer.dataset.close);
    const geoButton = event.target.closest("[data-geo]");
    if (geoButton) checkGeo(geoButton.dataset.geo);
    if (event.target.classList.contains("modal-backdrop")) {
      event.target.classList.add("hidden");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      $$(".modal-backdrop, .drawer-backdrop").forEach((el) => el.classList.add("hidden"));
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshAll();
  });
}

function startPolling() {
  if (state.polling) clearInterval(state.polling);
  state.polling = setInterval(() => {
    if (!document.hidden) refreshAll();
  }, 30000);
}

function stopPolling() {
  if (state.polling) {
    clearInterval(state.polling);
    state.polling = null;
  }
}

async function refreshAll() {
  if (!state.booted) return;
  try {
    await loadStatus();
    await loadWatches();
    if (state.selectedId) await loadDetail(state.selectedId);
    await loadAlerts(false);
  } catch (_) {
    /* un giro fallito non deve rompere la UI */
  }
}

/* ------------------------------------------------------------------ */
/* Stato agente                                                       */
/* ------------------------------------------------------------------ */
async function loadStatus() {
  const status = await api("/api/status");
  const running = status.scheduler?.running;
  $("#agentStatus").classList.toggle("offline", !running);
  const active = status.active_watches ?? 0;
  $("#agentText").textContent = running
    ? `agente attivo · ${active} ${active === 1 ? "ricerca" : "ricerche"}`
    : "agente in pausa";

  const ai = status.ai || {};
  state.ai = ai;
  const aiPill = $("#aiStatus");
  if (aiPill) {
    aiPill.classList.toggle("off", !ai.active);
    $("#aiText").textContent = ai.active
      ? `AI ${shortModel(ai.model_smart)}`
      : ai.configured
        ? "AI disattivata"
        : "AI non configurata";
  }
  if (status.sources && !state.sources.length) state.sources = status.sources;
  return status;
}

function shortModel(model) {
  if (!model) return "";
  const name = String(model).split("/").pop();
  return name.length > 18 ? name.slice(0, 17) + "…" : name;
}

/* ------------------------------------------------------------------ */
/* Ricerche                                                           */
/* ------------------------------------------------------------------ */
async function loadWatches() {
  state.watches = await api("/api/watches");
  $("#watchCount").textContent = state.watches.length;
  renderWatchList();
}

function renderWatchList() {
  const box = $("#watchList");
  if (!state.watches.length) {
    box.innerHTML = `<div class="hint" style="padding:10px 8px">Nessuna ricerca. Creane una con “+ Nuova ricerca”.</div>`;
    return;
  }
  box.innerHTML = state.watches.map((watch) => {
    const active = watch.id === state.selectedId;
    const route = `${esc(watch.from_place)} <span class="arrow">→</span> ${esc(watch.to_place)}`;
    const price = watch.best_price != null ? euro(watch.best_price) : "—";
    return `
      <div class="watch-item ${active ? "active" : ""} ${watch.active ? "" : "paused"}" data-id="${watch.id}">
        <div class="watch-route"><span class="watch-status-dot"></span>${route}</div>
        ${watch.name ? `<div class="watch-name">${esc(watch.name)}</div>` : ""}
        <div class="watch-meta">
          <span>entro ${fmtDay(watch.arrive_by)}</span>
          <span class="price">${price}</span>
        </div>
      </div>`;
  }).join("");
}

async function selectWatch(id) {
  state.selectedId = id;
  renderWatchList();
  await loadDetail(id);
  state.tab = "best";
  $$(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === "best"));
}

async function loadDetail(id) {
  const [watch, offers, links, runs] = await Promise.all([
    api(`/api/watches/${id}`),
    api(`/api/watches/${id}/offers`),
    api(`/api/watches/${id}/links`),
    api(`/api/watches/${id}/runs`),
  ]);
  state.watch = watch;
  state.offers = offers;
  state.links = links.links || [];
  state.runs = runs;
  try {
    const plan = await api(`/api/watches/${id}/plan`);
    state.plan = plan.plan || null;
    state.planAlternatives = plan.alternatives || [];
    state.questions = plan.questions || [];
  } catch (_) {
    state.plan = null;
    state.planAlternatives = [];
    state.questions = [];
  }
  showEmpty(false);
  renderDetail();
  renderTab();
}

function showEmpty(empty) {
  $("#emptyState").classList.toggle("hidden", !empty);
  $("#detail").classList.toggle("hidden", empty);
}

function renderDetail() {
  const watch = state.watch;
  if (!watch) return;

  $("#watchTitle").textContent = `${watch.from_place} → ${watch.to_place}`;
  const statePill = $("#watchState");
  statePill.textContent = watch.active ? "attiva" : "in pausa";
  statePill.classList.toggle("off", !watch.active);

  const sources = (watch.sources || []).map((s) => {
    const meta = state.sources.find((item) => item.id === s);
    return meta ? meta.label : s;
  }).join(", ");
  const passNames = ((watch.prefs && watch.prefs.passes) || [])
    .map((p) => {
      const tipo = typeof p === "string" ? p : p.tipo;
      const known = PASS_TYPES.find((item) => item.id === tipo);
      if (typeof p === "string") return known ? known.label : tipo;
      return p.nome || (known ? known.label : tipo);
    })
    .join(", ");
  $("#watchSub").textContent =
    `entro ${fmtDate(watch.arrive_by)} · budget ${watch.max_price != null ? euro(watch.max_price) : "libero"} · ` +
    `ogni ${watch.interval_min} min · raggio ${watch.radius_km} km · ${sources}` +
    (passNames ? ` · 🎟️ ${passNames}` : "");

  $("#btnToggleWatch").textContent = watch.active ? "Pausa" : "Riprendi";

  const priced = state.offers.filter((o) => o.price != null && !o.price_is_estimate);
  const best = priced.length ? Math.min(...priced.map((o) => o.price)) : null;
  const bestOffer = priced.find((o) => o.price === best);
  $("#statBest").textContent = best != null ? euro(best) : "—";
  $("#statBestNote").textContent = bestOffer ? (bestOffer.operator || bestOffer.source) : "in attesa di offerte";

  $("#statCount").textContent = state.offers.length;
  const transitCount = state.offers.filter((o) => o.mode === "transit").length;
  $("#statCountNote").textContent = transitCount ? `${transitCount} combinazioni mezzi` : "offerte attive";

  $("#statLastRun").textContent = watch.last_run_at ? relative(watch.last_run_at) : "mai";
  const lastRun = state.runs[0];
  $("#statLastRunNote").textContent = lastRun
    ? `${lastRun.offers_found} offerte · ${lastRun.status === "ok" ? "ok" : lastRun.status}`
    : "nessuna esecuzione";

  $("#statNextRun").textContent = watch.active ? relative(watch.next_run_at) : "—";
  $("#statNextRunNote").textContent = watch.active ? "controllo automatico" : "ricerca in pausa";

  $("#countAll").textContent = state.offers.length;
  renderPlan();
  renderQuestions();
}

/* ------------------------------------------------------------------ */
/* Piano AI + domande                                                 */
/* ------------------------------------------------------------------ */
function renderConfronti(confronti) {
  // "Tratte singole": quanto cambia davvero a spezzare il viaggio.
  if (!confronti || !confronti.length) return "";
  const righe = confronti
    .map((c) => {
      const deltaPrezzo = Number(c.delta_prezzo || 0);
      const deltaMinuti = Number(c.delta_minuti || 0);
      constConveniente = deltaPrezzo < -0.01;
      const tempo = deltaMinuti > 0 ? `+${deltaMinuti} min` : `${deltaMinuti} min`;
      const soldi = deltaPrezzo > 0
        ? `+${euro(deltaPrezzo)}`
        : deltaPrezzo < 0 ? `-${euro(Math.abs(deltaPrezzo))}` : "uguale";
      const tratte = (c.tratte || [])
        .map((t) => `${esc(t.da)}→${esc(t.a)} ${euro(t.prezzo)}`)
        .join(" + ");
      return `<div class="conf-row ${deltaPrezzo < -0.01 ? "ok" : "no"}">
        <span class="conf-via">via ${esc(c.via || "")}</span>
        <span class="conf-tratte">${tratte}</span>
        <span class="conf-delta">
          <b>${euro(c.totale)}</b>
          <span class="vs">diretto ${euro(c.diretto_prezzo)}</span>
          <span class="pill ${deltaPrezzo < -0.01 ? "good" : deltaPrezzo > 0.01 ? "bad" : ""}">
            ${soldi} · ${tempo}
          </span>
        </span>
      </div>`;
    })
    .join("");
  return `<div class="confronti">
    <h4>🪓 Tratte singole invece del biglietto unico</h4>
    ${righe}
    <small>Confronto reale sui prezzi Trenitalia: quanto costa e quanto tempo cambia spezzando il viaggio.</small>
  </div>`;
}

function renderPlan() {
  const box = $("#planCard");
  const plan = state.plan;
  if (!plan) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");

  const steps = (plan.steps || []).map((step) => {
    const dep = step.depart_at ? fmtTime(step.depart_at) : "—";
    const arr = step.arrive_at ? fmtTime(step.arrive_at) : "—";
    const day = step.depart_at ? fmtDay(step.depart_at) : "";
    const price = step.price != null ? euro(step.price) : "da verificare";
    const link = step.deeplink
      ? ` <a href="${esc(step.deeplink)}" target="_blank" rel="noreferrer">apri ↗</a>`
      : "";
    // Spezzatura: un passo che racchiude due biglietti.
    const biglietti = step.spezzatura && step.biglietti && step.biglietti.length
      ? `<div class="ps-split">${step.biglietti.map((b, i) => `
           <div class="ps-ticket">
             <span class="tk">${i + 1}</span>
             <span>${esc(b.da || "")} → ${esc(b.a || "")}</span>
             <span class="tk-time">${b.partenza ? fmtTime(b.partenza) : ""} → ${b.arrivo ? fmtTime(b.arrivo) : ""}</span>
           </div>`).join("")}
           <div class="ps-save">🎫 risparmi ${euro(step.risparmio)} rispetto al biglietto unico (${euro(step.prezzo_diretto)})</div>
         </div>`
      : "";

    return `
      <div class="plan-step">
        <span class="idx">${step.leg || 1}</span>
        <div class="ps-main">
          <b>${esc(step.operator || step.source || "")}</b>
          <small>${esc(MODE_LABEL[step.mode] || step.mode || "")} · ${dep} → ${arr} ${day} ·
            ${esc(step.pickup_name || "")} → ${esc(step.dropoff_name || "")}${step.price_is_estimate ? " · stima" : ""}</small>
          ${biglietti}
        </div>
        <span class="ps-price">${price}${link}</span>
      </div>`;
  }).join("");

  const duration = fmtDuration(plan.total_duration_min);
  const confidence = plan.confidence != null ? Math.round(Number(plan.confidence) * 100) : null;
  const watchouts = (plan.watchouts || []).map((w) => `<span>⚠️ ${esc(w)}</span>`).join("");
  const changes = plan.changes || 0;
  const changeLabel = changes === 0
    ? "nessun cambio"
    : changes === 1 ? "1 cambio" : `${changes} cambi`;
  // Il prezzo grande e' quello che si spende davvero: biglietto + spostamento
  // per raggiungere il punto di ritrovo. Se un abbonamento copre il viaggio,
  // il costo effettivo e' zero e il prezzo del biglietto resta informazione.
  const accessCost = plan.access_cost || 0;
  const covered = !!plan.covered;
  const netTotal = plan.net_total_price != null ? plan.net_total_price : plan.total_price;
  const effective = covered
    ? (netTotal || 0) + accessCost
    : (plan.effective_cost != null
        ? plan.effective_cost
        : (plan.total_price != null ? plan.total_price + accessCost : null));
  const hasAccess = accessCost > 0.01;
  const passNames = (plan.passes || []).join(", ");
  const alts = state.planAlternatives.length
    ? `<div class="plan-alts">
         <h4>Alternative: altri profili</h4>
         ${state.planAlternatives.map((alt) => {
           const altCovered = !!alt.covered;
           const altNet = altCovered
             ? (alt.net_total_price != null ? alt.net_total_price : 0)
             : alt.total_price;
           return `
           <div class="plan-alt">
             <span class="alt-profile">${esc(ALT_PROFILE[alt.profile] || "Alternativa")}</span>
             <span>${esc(alt.title || "")}${alt.total_duration_min ? " · " + fmtDuration(alt.total_duration_min) : ""}${alt.changes ? " · " + (alt.changes === 1 ? "1 cambio" : alt.changes + " cambi") : ""}${altCovered ? " · coperto" : ""}</span>
             <b>${altNet != null ? euro(altNet) : "—"}${altCovered && alt.total_price != null ? `<span class="was">${euro(alt.total_price)}</span>` : ""}</b>
           </div>`;
         }).join("")}
       </div>`
    : "";

  box.innerHTML = `
    <div class="plan-head">
      <div>
        <div class="eyebrow">${plan.ai ? "🧠 Piano AI" : "🧮 Piano"}${plan.profile ? " · " + esc(ALT_PROFILE[plan.profile] || "") : ""}${plan.partial ? " · prezzo parziale" : ""}</div>
        <div class="plan-title">${esc(plan.title || "")}</div>
      </div>
      <div class="plan-price">
        <strong>${effective != null ? euro(effective) : "—"}</strong>
        ${covered && plan.total_price != null ? `<span class="was">${euro(plan.total_price)}</span>` : ""}
        <small>${covered
          ? `coperto da ${esc(passNames || "abbonamento")}`
          : plan.partial ? "totale noto · tratte da verificare"
          : hasAccess ? "biglietto + spostamento" : "costo totale"}${plan.ai && confidence != null ? ` · fiducia ${confidence}%` : ""}</small>
        ${covered && plan.total_price != null ? `<small>biglietto ${euro(plan.total_price)}, ma è già coperto</small>` : ""}
        ${!covered && hasAccess && plan.total_price != null ? `<small>biglietti ${euro(plan.total_price)} + ${euro(accessCost)} spostamento</small>` : ""}
      </div>
    </div>
    <div class="plan-explanation">${esc(plan.explanation || "")}</div>
    <div class="plan-facts">
      ${plan.depart_at ? `<span>🛫 partenza ${fmtDate(plan.depart_at)}</span>` : ""}
      ${plan.arrive_at ? `<span>🏁 arrivo ${fmtDate(plan.arrive_at)}</span>` : ""}
      ${duration ? `<span>⏱ ${duration}</span>` : ""}
      <span>🔁 ${changeLabel}</span>
      ${covered ? `<span>🎟️ coperto da ${esc(passNames || "abbonamento")}</span>` : ""}
      ${plan.transfer_km != null ? `<span>🚶 ${plan.transfer_km} km a piedi nel cambio</span>` : ""}
    </div>
    ${steps ? `<div class="plan-steps">${steps}</div>` : ""}
    <div class="plan-meta">
      ${plan.partial ? "<span>💶 prezzo parziale</span>" : ""}
      ${(plan.steps || []).length === 1
        ? "<span>🎯 tratta diretta</span>"
        : `<span>🔁 ${(plan.steps || []).length} tratte</span>`}
    </div>
    ${watchouts ? `<div class="plan-watchouts">${watchouts}</div>` : ""}
    ${renderConfronti(plan.confronti)}
    ${alts}`;
}

function renderQuestions() {
  const box = $("#questionsCard");
  const questions = state.questions || [];
  if (!questions.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    return;
  }
  box.classList.remove("hidden");
  box.innerHTML = `
    <h4>❓ L'agente ha bisogno di un'informazione</h4>
    ${questions.map((question) => {
      const options = (question.options || []).map((option) =>
        `<button class="btn" data-qid="${question.id}" data-answer="${esc(option.id)}">${esc(option.label)}</button>`
      ).join("");
      const skip = `<button class="btn ghost" data-qid="${question.id}" data-answer="skip">Salta</button>`;
      // Risposta aperta: si scrive a parole, l'agente capisce e ricalcola.
      const libero = question.allow_free_text === false ? "" : `
        <form class="q-free" data-qid="${question.id}">
          <input type="text" name="answer" maxlength="300" autocomplete="off"
                 placeholder="Oppure scrivi qui la risposta…" />
          <button class="btn" type="submit">Invia</button>
        </form>`;
      return `<div class="q-item">
        <p>${esc(question.text)}</p>
        <div class="q-options">${options}${skip}</div>
        ${libero}
      </div>`;
    }).join("")}`;
}

async function answerQuestion(questionId, answer) {
  try {
    const endpoint = answer === "skip" ? "skip" : "answer";
    const options = answer === "skip"
      ? { method: "POST" }
      : { method: "POST", body: JSON.stringify({ answer, source: "web" }) };
    await api(`/api/questions/${questionId}/${endpoint}`, options);
    toast("Grazie: aggiorno la ricerca.");
    await refreshAll();
    setTimeout(refreshAll, 4000);
  } catch (error) {
    toast(error.message, "error");
  }
}

/* Risposta libera: l'agente interpreta la frase e se la usa come
   preferenza (un abbonamento, un budget, un giorno piu' tardi...). */
async function answerFree(questionId, form) {
  const input = form.querySelector("input[name=answer]");
  const text = (input?.value || "").trim();
  if (!text) {
    toast("Scrivi la risposta.", "error");
    input?.focus();
    return;
  }
  const send = form.querySelector("button[type=submit]");
  if (send) {
    send.disabled = true;
    send.textContent = "Invio…";
  }
  try {
    const result = await api(`/api/questions/${questionId}/answer`, {
      method: "POST",
      body: JSON.stringify({ answer: text, source: "web" }),
    });
    const note = result?.applied?.note;
    toast(note ? `Grazie: ${note}` : "Grazie: aggiorno la ricerca.");
    await refreshAll();
    setTimeout(refreshAll, 4000);
  } catch (error) {
    toast(error.message, "error");
    if (send) {
      send.disabled = false;
      send.textContent = "Invia";
    }
  }
}

function renderTab() {
  const panel = $("#tabPanel");
  if (!state.watch) return;
  if (state.tab === "best") return renderBest(panel);
  if (state.tab === "all") return renderAll(panel);
  if (state.tab === "map") return renderMap(panel);
  if (state.tab === "nearby") return renderNearby(panel);
  if (state.tab === "links") return renderLinks(panel);
  if (state.tab === "runs") return renderRuns(panel);
}

function isInitialRun() {
  if (state.offers.length < 2) return false;
  const times = state.offers
    .map((o) => (o.first_seen ? new Date(o.first_seen).getTime() : 0))
    .filter((t) => t > 0);
  if (!times.length) return false;
  const spread = Math.max(...times) - Math.min(...times);
  return spread < 5 * 60 * 1000;
}

function offerCard(offer, suppressNew = false) {
  const badges = (offer.badges || []).filter((b) => BADGES[b]);
  const isNew = offer.first_seen && (Date.now() - new Date(offer.first_seen).getTime() < 2 * 3600 * 1000);
  if (isNew && !suppressNew && !badges.includes("new")) badges.unshift("new");

  const tagHtml = badges.map((b) =>
    `<span class="tag ${b === "new" ? "new" : b}">${b === "new" ? "Nuovo" : BADGES[b]}</span>`
  ).join("");

  const dep = offer.depart_at ? fmtTime(offer.depart_at) : "—";
  const arr = offer.arrive_at ? fmtTime(offer.arrive_at) : "—";
  const dayLabel = offer.depart_at ? fmtDay(offer.depart_at) : "";
  const duration = fmtDuration(offer.duration_min);
  const pickupKm = offer.details?.distanza_pickup_km;
  const dropoffKm = offer.details?.distanza_dropoff_km;
  const effective = offer.details?.costo_effettivo;
  const tappe = Array.isArray(offer.details?.tappe)
    ? offer.details.tappe.filter((t) => t && (t.da || t.a))
    : [];

  const detailBits = [];
  if (duration) detailBits.push(`⏱ ${duration}`);
  const changes = offer.details?.cambi;
  if (changes != null) {
    detailBits.push(
      changes === 0 ? "🎯 diretto, nessun cambio" : `🔁 ${changes} ${changes === 1 ? "cambio" : "cambi"}`
    );
  }
  if (offer.seats) detailBits.push(`👤 ${offer.seats} posti`);
  if (offer.rating) detailBits.push(`★ ${Number(offer.rating).toFixed(1)}`);
  if (effective != null && offer.price != null && Math.abs(effective - offer.price) > 0.01) {
    detailBits.push(`💶 costo eff. ${euro(effective)}`);
  }
  if (offer.details?.partenza_alternativa) {
    detailBits.push(`🚩 partenza da ${esc(offer.details.partenza_alternativa)} (~${offer.details.distanza_partenza_km || 0} km)`);
  }
  if (offer.details?.arrivo_alternativo) {
    detailBits.push(`🏁 arrivo a ${esc(offer.details.arrivo_alternativo)} (~${offer.details.distanza_arrivo_km || 0} km)`);
  }
  if (offer.details?.tariffa) detailBits.push(esc(offer.details.tariffa));
  if (offer.details?.disponibilita) detailBits.push(esc(offer.details.disponibilita));
  if (offer.details?.spezzatura) {
    detailBits.push(
      `🎫 2 biglietti invece di 1: risparmi ${euro(offer.details.risparmio)} ` +
      `(diretto ${euro(offer.details.prezzo_diretto)})`
    );
  }
  if (offer.details?.pass) {
    const nota = offer.details.pass_note ? ` (${offer.details.pass_note})` : "";
    detailBits.push(
      `🎟️ coperto da ${esc(offer.details.pass)}${nota}: non lo paghi`
    );
  }

  const priceHtml = offer.price == null
    ? (offer.details?.orario_gtfs
        ? `<div class="price" style="font-size:14px;color:var(--muted)">solo orario<small>prezzo da tariffa ufficiale</small></div>`
        : `<div class="price" style="font-size:15px;color:var(--muted)">da verificare</div>`)
    : `<div class="price">${offer.prev_price && offer.prev_price > offer.price
        ? `<span class="was">${euro(offer.prev_price)}</span>` : ""}${euro(offer.price)}
        ${offer.price_is_estimate ? "<small>stima, non prezzo finale</small>" : ""}
      </div>`;

  return `
    <article class="offer ${offer.price_is_estimate ? "estimate" : ""}">
      <div class="offer-main">
        <div class="offer-top">
          <span class="operator">${esc(offer.operator || offer.source)}</span>
          <span class="source-tag">${esc(MODE_LABEL[offer.mode] || offer.mode)}</span>
          ${tagHtml}
        </div>
        <div class="times">
          <strong>${dep}</strong><span class="sep">→</span><strong>${arr}</strong>
          <span class="dur">${dayLabel}${duration ? " · " + duration : ""}</span>
        </div>
        <div class="places">
          <span>↑ ${esc(offer.pickup_name || "—")}${pickupKm != null ? ` <span class="km">· ${pickupKm} km</span>` : ""}</span>
          <span>↓ ${esc(offer.dropoff_name || "—")}${dropoffKm != null ? ` <span class="km">· ${dropoffKm} km</span>` : ""}</span>
        </div>
        ${detailBits.length ? `<div class="details-line">${detailBits.map((b) => `<span>${b}</span>`).join("")}</div>` : ""}
        ${tappe.length > 1 ? `
          <details class="tappe">
            <summary>🛣️ ${tappe.length} tratte · dove ferma e cambia</summary>
            <ol>${tappe.map((t, i) => `
              <li>
                <span class="tappe-n">${i + 1}</span>
                <span class="tappe-place"><b>${esc(t.da || "—")}</b><span class="arrow">→</span><b>${esc(t.a || "—")}</b></span>
                <span class="tappe-when">${t.partenza ? fmtTime(t.partenza) : ""}${t.arrivo ? " – " + fmtTime(t.arrivo) : ""}${t.mezzo ? " · " + esc(t.mezzo) : ""}</span>
              </li>`).join("")}</ol>
          </details>` : ""}
      </div>
      <div class="offer-side">
        ${priceHtml}
        ${offer.score != null ? `<div class="score">punteggio <b>${Math.round(offer.score)}</b>/100</div>` : ""}
        ${offer.deeplink ? `<a class="btn small" href="${esc(offer.deeplink)}" target="_blank" rel="noreferrer">Apri ↗</a>` : ""}
      </div>
    </article>`;
}

function renderBest(panel) {
  const offers = state.offers
    .filter((o) => o.mode !== "transit" && o.score != null)
    .sort((a, b) => (b.score || 0) - (a.score || 0));

  if (!offers.length) {
    const notes = (state.runs[0]?.notes || []).slice(0, 2).join(" ");
    const text = "L'agente sta cercando. Puoi forzare un controllo con “Cerca ora”." + (notes ? " " + notes : "");
    panel.innerHTML = emptyTab("Ancora nessuna offerta con prezzo", text, true);
    return;
  }
  const initial = isInitialRun();
  panel.innerHTML = `<div class="offers">${offers.map((o) => offerCard(o, initial)).join("")}</div>`;
}

function renderAll(panel) {
  if (!state.offers.length) {
    panel.innerHTML = emptyTab("Nessuna offerta salvata", "Avvia una ricerca o allarga il raggio/budget.");
    return;
  }
  const groups = {};
  for (const offer of state.offers) {
    (groups[offer.mode] ||= []).push(offer);
  }
  const order = ["rideshare", "bus", "train", "tram", "flight", "carsharing", "transit"];
  const initial = isInitialRun();
  const html = order.filter((mode) => groups[mode]?.length).map((mode) => `
    <div class="group-title">${esc(MODE_LABEL[mode] || mode)} · ${groups[mode].length}</div>
    <div class="offers">${groups[mode]
      .sort((a, b) => (b.score || 0) - (a.score || 0))
      .map((o) => offerCard(o, initial)).join("")}</div>
  `).join("");
  panel.innerHTML = html;
}

function renderNearby(panel) {
  const offers = state.offers.filter((o) => o.mode === "carsharing" || o.mode === "transit");
  if (!offers.length) {
    panel.innerHTML = emptyTab(
      "Nessuna alternativa vicina",
      "Il carsharing viene stimato solo per tratte coperte dal provider; le fermate vicine compaiono se la fonte è attiva."
    );
    return;
  }
  panel.innerHTML = `<div class="offers">${offers.map((o) => offerCard(o, isInitialRun())).join("")}</div>`;
}

let mapView = null;
let mapLayer = null;

function ensureMap(canvas) {
  if (mapView && mapView.getContainer() !== canvas) {
    mapView.remove();
    mapView = null;
  }
  if (!mapView) {
    mapView = L.map(canvas, { zoomControl: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "© OpenStreetMap",
    }).addTo(mapView);
  }
  return mapView;
}

async function renderMap(panel) {
  if (typeof L === "undefined") {
    panel.innerHTML = emptyTab(
      "Mappa non disponibile",
      "La libreria della mappa (Leaflet) non è stata caricata: controlla la connessione e ricarica la pagina."
    );
    return;
  }
  if (!state.carsharingZones) {
    try {
      const data = await api("/api/carsharing/zones");
      state.carsharingZones = data.zones || [];
    } catch (_) {
      state.carsharingZones = [];
    }
  }
  panel.innerHTML = `
    <div class="map-toolbar">
      <label class="map-toggle"><input type="checkbox" id="mapZones" ${state.showZones ? "checked" : ""} /> Zone carsharing</label>
      <span class="map-hint" id="mapLegend"></span>
    </div>
    <div class="map-canvas" id="mapCanvas"></div>`;
  $("#mapZones").addEventListener("change", (event) => {
    state.showZones = event.target.checked;
    drawMap();
  });

  const map = ensureMap($("#mapCanvas"));
  map.invalidateSize();
  drawMap();
}

function drawMap() {
  if (!mapView) return;
  if (mapLayer) mapView.removeLayer(mapLayer);
  mapLayer = L.layerGroup().addTo(mapView);

  const bounds = [];
  const watch = state.watch || {};
  const origin = [watch.from_lat, watch.from_lon];
  const dest = [watch.to_lat, watch.to_lon];

  const dot = (lat, lon, color, radius) =>
    L.circleMarker([lat, lon], {
      radius,
      color,
      weight: 2,
      fillColor: color,
      fillOpacity: 0.85,
    });

  if (origin[0] != null && origin[1] != null) {
    dot(origin[0], origin[1], "#1f9d55", 9)
      .bindPopup(`<strong>Partenza</strong><br>${esc(watch.from_place || "")}`)
      .addTo(mapLayer);
    bounds.push(origin);
  }
  if (dest[0] != null && dest[1] != null) {
    dot(dest[0], dest[1], "#d64545", 9)
      .bindPopup(`<strong>Arrivo</strong><br>${esc(watch.to_place || "")}`)
      .addTo(mapLayer);
    bounds.push(dest);
  }

  let placed = 0;
  for (const offer of state.offers.slice(0, MAX_MAP_OFFERS)) {
    const a = [offer.pickup_lat, offer.pickup_lon];
    const b = [offer.dropoff_lat, offer.dropoff_lon];
    if (a[0] == null || a[1] == null || b[0] == null || b[1] == null) continue;
    const color = MODE_COLOR[offer.mode] || "#7b8794";
    L.polyline([a, b], { color, weight: 2, opacity: 0.55, dashArray: "5,6" }).addTo(mapLayer);
    const label = `<strong>${esc(MODE_LABEL[offer.mode] || offer.mode)}</strong><br>${esc(offer.operator || "")}`;
    const price = offer.price != null ? `${euro(offer.price)}${offer.price_is_estimate ? " (stima)" : ""}` : "solo orario";
    dot(a[0], a[1], color, 6)
      .bindPopup(`${label}<br>Partenza: ${fmtTime(offer.depart_at)}<br>${price}`)
      .addTo(mapLayer);
    dot(b[0], b[1], color, 4).bindPopup(`${label}<br>Arrivo: ${fmtTime(offer.arrive_at)}`).addTo(mapLayer);
    bounds.push(a, b);
    placed += 1;
  }

  if (state.showZones) {
    for (const zone of state.carsharingZones || []) {
      L.circle([zone.lat, zone.lon], {
        radius: 9000,
        color: "#d19b12",
        weight: 1,
        fillColor: "#d19b12",
        fillOpacity: 0.08,
      })
        .bindPopup(`<strong>${esc(zone.provider)}</strong><br>Carsharing a ${esc(zone.city)}`)
        .addTo(mapLayer);
    }
  }

  if (bounds.length >= 2) {
    mapView.fitBounds(bounds, { padding: [30, 30], maxZoom: 12 });
  } else if (bounds.length === 1) {
    mapView.setView(bounds[0], 11);
  } else {
    mapView.setView([42.5, 12.5], 5);
  }

  const zones = state.showZones ? (state.carsharingZones || []).length : 0;
  $("#mapLegend").textContent = `${placed} offerte sulla mappa · ${zones} zone carsharing`;

  setTimeout(() => mapView && mapView.invalidateSize(), 60);
}

function renderLinks(panel) {
  if (!state.links.length) {
    panel.innerHTML = emptyTab("Nessun link", "Aggiungi una ricerca per generare i link.");
    return;
  }
  panel.innerHTML = `<div class="links-grid">${state.links.map((link) => `
    <a class="link-card" href="${esc(link.url)}" target="_blank" rel="noreferrer">
      <div class="lc-head"><strong>${esc(link.label)}</strong><span class="lc-arrow">↗</span></div>
      <small>${esc(link.note || "")}</small>
    </a>`).join("")}</div>`;
}

function renderRuns(panel) {
  if (!state.runs.length) {
    panel.innerHTML = emptyTab("Nessuna esecuzione", "La cronologia comparirà dopo il primo controllo.");
    return;
  }
  panel.innerHTML = `<div>${state.runs.map((run) => `
    <div class="run-row">
      <span>${fmtDate(run.started_at)}</span>
      <span class="run-status ${esc(run.status)}">${esc(run.status)}</span>
      <span>${run.offers_found} offerte · ${run.alerts_sent} avvisi</span>
            <span class="run-errors" title="${esc((run.errors || []).join(" · "))}">
        ${run.errors?.length ? esc(run.errors.join(" · ")) : (run.notes?.length ? esc(run.notes.join(" · ")) : `(${esc(run.trigger)})`)}
      </span>
    </div>`).join("")}</div>`;
}

function emptyTab(title, text, withLinks = false) {
  return `<div class="empty" style="margin-top:40px"><div class="empty-emoji">🕵️</div>
    <h2 style="font-size:18px">${esc(title)}</h2>
    <p style="font-size:13px">${esc(text)}</p>
    <div style="display:flex;gap:8px;justify-content:center;flex-wrap:wrap">
      <button class="btn" onclick="runNow()">▶ Cerca ora</button>
      ${withLinks ? `<button class="btn ghost" onclick="document.querySelector('[data-tab=links]').click()">🔗 Link utili</button>` : ""}
    </div></div>`;
}

/* ------------------------------------------------------------------ */
/* Azioni sulla ricerca                                               */
/* ------------------------------------------------------------------ */
async function runNow() {
  if (!state.watch) return;
  try {
    toast("Ricerca avviata…");
    await api(`/api/watches/${state.watch.id}/run`, { method: "POST" });
    setTimeout(refreshAll, 2500);
    setTimeout(refreshAll, 7000);
  } catch (error) {
    toast(error.message, "error");
  }
}

async function toggleWatch() {
  if (!state.watch) return;
  try {
    await api(`/api/watches/${state.watch.id}/toggle`, { method: "POST" });
    await loadWatches();
    await loadDetail(state.watch.id);
    toast(state.watch.active ? "Ricerca in pausa" : "Ricerca ripresa");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteWatch() {
  if (!state.watch) return;
  if (!confirm(`Eliminare la ricerca ${state.watch.from_place} → ${state.watch.to_place}?`)) return;
  try {
    await api(`/api/watches/${state.watch.id}`, { method: "DELETE" });
    state.selectedId = null;
    state.watch = null;
    await loadWatches();
    if (state.watches.length) await selectWatch(state.watches[0].id);
    else showEmpty(true);
    toast("Ricerca eliminata");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ */
/* Modale ricerca                                                     */
/* ------------------------------------------------------------------ */
function openWatchModal(watch) {
  state.editingId = watch?.id || null;
  $("#watchModalTitle").textContent = watch ? "Modifica ricerca" : "Nuova ricerca";
  $("#btnSaveWatch").textContent = watch ? "Salva modifiche" : "Avvia ricerca";
  $("#watchError").classList.add("hidden");

  const defaults = {
    name: "", from_place: "", to_place: "",
    depart_after: "", arrive_by: "", max_price: "", passengers: 1, radius_km: 5,
    interval_min: 60,
    sources: ["blablacar", "flixbus", "itabus", "trenitalia", "omio", "carsharing", "transit", "gtfs"],
    notify: ["web", "telegram"],
  };

  const data = watch ? {
    ...watch,
    depart_after: toLocalInput(watch.depart_after),
    arrive_by: toLocalInput(watch.arrive_by),
    max_price: watch.max_price ?? "",
  } : defaults;

  if (!watch) {
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(18, 0, 0, 0);
    const morning = new Date(tomorrow);
    morning.setHours(8, 0, 0, 0);
    data.arrive_by = toLocalInput(tomorrow.toISOString());
    data.depart_after = toLocalInput(morning.toISOString());
  }

  $("#fName").value = data.name || "";
  $("#fFrom").value = data.from_place || "";
  $("#fTo").value = data.to_place || "";
  $("#fDepart").value = data.depart_after || "";
  $("#fArrive").value = data.arrive_by || "";
  $("#fPrice").value = data.max_price ?? "";
  $("#fPassengers").value = data.passengers || 1;
  $("#fRadius").value = data.radius_km || 5;
  $("#fInterval").value = String(data.interval_min || 60);
  $("#geoFrom").textContent = "";
  $("#geoTo").textContent = "";
  $("#fAiText").value = "";
  $("#aiFillHint").textContent = "";
  $("#aiFillHint").className = "field-hint";
  const formQuestions = $("#formQuestions");
  if (formQuestions) formQuestions.remove();

  const selectedSources = data.sources || defaults.sources;
  $("#sourcesBox").innerHTML = state.sources.map((source) => {
    const checked = selectedSources.includes(source.id) ? "checked" : "";
    const soon = source.requires ? `<span class="soon">link</span>` : "";
    return `<label class="chip" title="${esc(source.description)}">
      <input type="checkbox" name="source" value="${esc(source.id)}" ${checked} />
      ${esc(source.label)} ${soon}
    </label>`;
  }).join("");

  const notify = data.notify || defaults.notify;
  $$("#notifyBox input[name=notify]").forEach((input) => {
    input.checked = notify.includes(input.value);
  });

  // Abbonamenti: la lista dei tipi noti, con i selezionati della ricerca.
  const currentPasses = (data.prefs && data.prefs.passes) || [];
  const selectedPasses = currentPasses.map((p) => (typeof p === "string" ? p : p.tipo));
  $("#passesBox").innerHTML = PASS_TYPES.map((pass) => {
    const checked = selectedPasses.includes(pass.id) ? "checked" : "";
    return `<label class="chip" title="${esc(pass.hint)}">
      <input type="checkbox" name="pass" value="${esc(pass.id)}" ${checked} />
      ${esc(pass.label)}
    </label>`;
  }).join("");

  openModal("watchModal");
  setTimeout(() => $("#fFrom").focus(), 60);
}

async function aiFill() {
  const text = $("#fAiText").value.trim();
  const hint = $("#aiFillHint");
  if (!text) {
    hint.textContent = "Scrivi prima una frase sul viaggio.";
    hint.className = "field-hint err";
    return;
  }
  const button = $("#btnAiFill");
  button.disabled = true;
  hint.textContent = "L'AI sta leggendo…";
  hint.className = "field-hint";
  try {
    const result = await api("/api/ai/parse", { method: "POST", body: JSON.stringify({ text }) });
    const trip = result.trip || {};
    if (trip.from_place) $("#fFrom").value = trip.from_place;
    if (trip.to_place) $("#fTo").value = trip.to_place;
    if (trip.arrive_by) $("#fArrive").value = toLocalInput(trip.arrive_by);
    if (trip.depart_after) $("#fDepart").value = toLocalInput(trip.depart_after);
    if (trip.max_price != null) $("#fPrice").value = trip.max_price;
    if (trip.passengers) $("#fPassengers").value = trip.passengers;
    if (trip.radius_km) $("#fRadius").value = trip.radius_km;
    if (trip.notes && !$("#fName").value) $("#fName").value = trip.notes.slice(0, 120);

    const missing = result.missing || [];
    const questions = result.questions || [];
    if (missing.length) {
      hint.textContent = "Manca ancora: " + missing.map(fieldLabel).join(", ") + ".";
      hint.className = "field-hint err";
    } else {
      hint.textContent = "Compilato. Controlla i campi e premi Avvia ricerca.";
      hint.className = "field-hint ok";
    }
    if (questions.length) {
      renderFormQuestions(questions);
    }
  } catch (error) {
    hint.textContent = error.message;
    hint.className = "field-hint err";
  } finally {
    button.disabled = false;
  }
}

function fieldLabel(name) {
  return { from_place: "partenza", to_place: "destinazione", arrive_by: "orario di arrivo" }[name] || name;
}

function renderFormQuestions(questions) {
  let box = $("#formQuestions");
  if (!box) {
    box = document.createElement("div");
    box.id = "formQuestions";
    box.className = "questions-card";
    $("#aiFillHint").closest(".ai-box").appendChild(box);
  }
  box.innerHTML = questions.map((question) => `
    <div class="q-item">
      <p>${esc(question.text)}</p>
      <div class="q-options">
        ${(question.options || []).map((option) =>
          `<button type="button" class="btn" data-fill="${esc(option.label)}">${esc(option.label)}</button>`
        ).join("")}
      </div>
    </div>`).join("");
  box.querySelectorAll("[data-fill]").forEach((button) => {
    button.addEventListener("click", () => {
      $("#fAiText").value = ($("#fAiText").value + " " + button.dataset.fill).trim();
      box.remove();
      aiFill();
    });
  });
}

async function checkGeo(fieldId) {
  const input = $("#" + fieldId);
  const hint = $(fieldId === "fFrom" ? "#geoFrom" : "#geoTo");
  const query = input.value.trim();
  if (!query) return;
  hint.textContent = "cerco…";
  hint.className = "field-hint";
  try {
    const result = await api("/api/geocode?q=" + encodeURIComponent(query));
    if (!result.found) {
      hint.textContent = "località non trovata, prova con “Città” o “Città Stazione”";
      hint.className = "field-hint err";
      return;
    }
    const short = (result.name || query).split(",").slice(0, 2).join(",");
    hint.textContent = `✓ ${short} (${result.lat.toFixed(3)}, ${result.lon.toFixed(3)})`;
    hint.className = "field-hint ok";
  } catch (error) {
    hint.textContent = error.message;
    hint.className = "field-hint err";
  }
}

async function submitWatch(event) {
  event.preventDefault();
  const errorBox = $("#watchError");
  errorBox.classList.add("hidden");

  const fromPlace = $("#fFrom").value.trim();
  const toPlace = $("#fTo").value.trim();
  const arriveLocal = $("#fArrive").value;
  const departLocal = $("#fDepart").value;

  const fail = (message) => {
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
  };

  if (!fromPlace || !toPlace) return fail("Inserisci partenza e arrivo.");
  if (!arriveLocal) return fail("Indica entro quando devi arrivare.");
  if (departLocal && new Date(departLocal) >= new Date(arriveLocal)) {
    return fail("La partenza minima deve essere precedente all'arrivo massimo.");
  }

  const sources = $$("#sourcesBox input[name=source]:checked").map((i) => i.value);
  if (!sources.length) return fail("Seleziona almeno una fonte.");
  const notify = $$("#notifyBox input[name=notify]:checked").map((i) => i.value);
  const passTypes = $$("#passesBox input[name=pass]:checked").map((i) => i.value);

  const payload = {
    name: $("#fName").value.trim() || null,
    from_place: fromPlace,
    to_place: toPlace,
    depart_after: departLocal ? new Date(departLocal).toISOString() : null,
    arrive_by: new Date(arriveLocal).toISOString(),
    max_price: $("#fPrice").value ? Number($("#fPrice").value) : null,
    passengers: Number($("#fPassengers").value || 1),
    radius_km: Number($("#fRadius").value || 5),
    interval_min: Number($("#fInterval").value || 60),
    sources,
    notify,
    active: true,
  };
  // Gli abbonamenti sono parte delle preferenze: il resto si conserva.
  // Si manda sempre (anche vuota): togliere un abbonamento deve valere.
  payload.prefs = { passes: passTypes.map((tipo) => ({ tipo })) };

  const button = $("#btnSaveWatch");
  button.disabled = true;
  button.textContent = "Salvo…";
  try {
    let watch;
    if (state.editingId) {
      watch = await api(`/api/watches/${state.editingId}`, {
        method: "PATCH", body: JSON.stringify(payload),
      });
    } else {
      watch = await api("/api/watches", { method: "POST", body: JSON.stringify(payload) });
    }
    closeModal("watchModal");
    await loadWatches();
    await selectWatch(watch.id);
    toast(state.editingId ? "Ricerca aggiornata" : "Ricerca avviata: primo controllo in corso…");
    setTimeout(refreshAll, 3000);
    setTimeout(refreshAll, 8000);
  } catch (error) {
    fail(error.message);
  } finally {
    button.disabled = false;
    button.textContent = state.editingId ? "Salva modifiche" : "Avvia ricerca";
  }
}

/* ------------------------------------------------------------------ */
/* Impostazioni                                                       */
/* ------------------------------------------------------------------ */
async function loadSettings() {
  state.settings = await api("/api/settings");
}

async function openSettings() {
  try {
    await loadSettings();
    const s = state.settings;
    const isAdmin = !!state.user?.is_admin;

    // Le impostazioni comuni (Groq, GTFS) si vedono solo da amministratore.
    ["#sGroqKey", "#sAiSmart", "#sAiFast", "#sAiEnabled", "#sGtfsEnabled", "#btnGtfsUpdate",
     "#btnTestAi", "#btnAiModels"].forEach((sel) => {
      const el = $(sel);
      if (el) el.closest(".field, .field.inline-field, .gtfs-feeds")?.classList.toggle("hidden", !isAdmin);
    });
    $$(".admin-only").forEach((el) => el.classList.toggle("hidden", !isAdmin));

    renderAccessInfo();
    loadTelegramChats();

    $("#sTelegramToken").value = s.telegram_token || "";
    $("#sTelegramChat").value = s.telegram_chat_id || "";
    if ($("#sNgrokToken")) $("#sNgrokToken").value = s.ngrok_authtoken || "";
    if ($("#sNgrokDomain")) $("#sNgrokDomain").value = s.ngrok_domain || "";
    $("#sSmtpHost").value = s.smtp_host || "";
    $("#sSmtpPort").value = s.smtp_port || "587";
    $("#sSmtpUser").value = s.smtp_user || "";
    $("#sSmtpPassword").value = s.smtp_password || "";
    $("#sSmtpFrom").value = s.smtp_from || "";
    $("#sSmtpTo").value = s.smtp_to || "";
    $("#sSmtpTls").checked = (s.smtp_tls || "1") === "1";
    $("#sNotifyEnabled").checked = (s.notify_enabled || "1") === "1";
    $("#sQuietFrom").value = s.quiet_from || "";
    $("#sQuietTo").value = s.quiet_to || "";
    $("#sGroqKey").value = s.groq_api_key || "";
    $("#sAiEnabled").checked = (s.ai_enabled || "1") === "1";
    $("#sGtfsEnabled").checked = (s.gtfs_enabled || "1") === "1";
    $("#aiTestResult").textContent = "";
    $("#settingsError").classList.add("hidden");
    $("#telegramTestResult").textContent = "";
    $("#emailTestResult").textContent = "";
    loadGtfsStatus();
    renderPushStatus();
    openModal("settingsModal");
    // I modelli si caricano dopo aver aperto il pannello, cosi' la
    // finestra non resta in attesa.
    loadModelOptions(s.ai_model_smart, s.ai_model_fast);
  } catch (error) {
    toast(error.message, "error");
  }
}

/* Popola i menu dei modelli Groq.
   - se il modello salvato non e' piu' disponibile, resta comunque
     selezionabile (con un avviso): il free tier cambia spesso; */
let _modelDetails = [];

function fillModelSelect(select, selected) {
  const options = [`<option value="">— modello predefinito —</option>`];
  for (const model of _modelDetails) {
    const ctx = model.context_length
      ? ` · ${Math.round(model.context_length / 1000)}k ctx`
      : "";
    const mark = model.id === selected ? " ✓" : "";
    options.push(
      `<option value="${esc(model.id)}"${model.id === selected ? " selected" : ""}>` +
        `${esc(model.id)}${esc(ctx)}${mark}</option>`
    );
  }
  select.innerHTML = options.join("");
  if (selected && !_modelDetails.some((m) => m.id === selected)) {
    // Il modello salvato non e' fra quelli disponibili: resta selezionabile
    // (il free tier di Groq cambia spesso), ma con un avviso.
    const extra = document.createElement("option");
    extra.value = selected;
    extra.textContent = `${selected} (non disponibile ora)`;
    select.prepend(extra);
  }
}

async function loadModelOptions(smart, fast) {
  const hint = $("#aiTestResult");
  const smartSelect = $("#sAiSmart");
  const fastSelect = $("#sAiFast");
  smartSelect.disabled = true;
  fastSelect.disabled = true;
  try {
    const result = await api("/api/ai/models");
    _modelDetails = result.details || (result.models || []).map((id) => ({ id, context_length: 0 }));
    fillModelSelect(smartSelect, smart || result.model_smart);
    fillModelSelect(fastSelect, fast || result.model_fast);
    smartSelect.disabled = false;
    fastSelect.disabled = false;
    const count = _modelDetails.length;
    hint.textContent = count
      ? `${count} modelli disponibili.`
      : "Nessun modello leggibile (controlla la chiave Groq).";
    hint.className = "field-hint";
  } catch (error) {
    // Senza elenco i select restano con il solo default: si puo' comunque
    // scrivere un id a mano.
    smartSelect.innerHTML = `<option value="">— modello predefinito —</option>`;
    fastSelect.innerHTML = `<option value="">— modello predefinito —</option>`;
    smartSelect.disabled = false;
    fastSelect.disabled = false;
    hint.textContent = "✗ " + error.message;
    hint.className = "field-hint err";
  }
}

function settingsPayload() {
  // Le impostazioni personali vanno sempre; quelle comuni (Groq, modelli,
  // GTFS) solo se l'utente e' amministratore.
  const personal = {
    telegram_token: $("#sTelegramToken").value.trim(),
    telegram_chat_id: $("#sTelegramChat").value.trim(),
    smtp_host: $("#sSmtpHost").value.trim(),
    smtp_port: $("#sSmtpPort").value.trim(),
    smtp_user: $("#sSmtpUser").value.trim(),
    smtp_password: $("#sSmtpPassword").value,
    smtp_from: $("#sSmtpFrom").value.trim(),
    smtp_to: $("#sSmtpTo").value.trim(),
    smtp_tls: $("#sSmtpTls").checked ? "1" : "0",
    quiet_from: $("#sQuietFrom").value,
    quiet_to: $("#sQuietTo").value,
    notify_enabled: $("#sNotifyEnabled").checked ? "1" : "0",
  };
  if (!state.user?.is_admin) return personal;
  return {
    ...personal,
    groq_api_key: $("#sGroqKey").value.trim(),
    ngrok_authtoken: $("#sNgrokToken").value.trim(),
    ngrok_domain: $("#sNgrokDomain").value.trim(),
    // Stringa vuota = torna al modello predefinito dell'app.
    ai_model_smart: $("#sAiSmart").value,
    ai_model_fast: $("#sAiFast").value,
    ai_enabled: $("#sAiEnabled").checked ? "1" : "0",
    gtfs_enabled: $("#sGtfsEnabled").checked ? "1" : "0",
  };
}

async function submitSettings(event) {
  event.preventDefault();
  const errorBox = $("#settingsError");
  errorBox.classList.add("hidden");
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(settingsPayload()) });
    closeModal("settingsModal");
    toast("Impostazioni salvate");
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.classList.remove("hidden");
  }
}

/* ---------- accesso: password e chiave segreta ---------- */
function renderAccessInfo() {
  const user = state.user || {};
  const telegram = user.telegram_chat_id ? "collegata" : "non collegata";
  const access = $("#accessInfo");
  if (access) {
    access.textContent =
      `Sei dentro come ${user.display_name || user.username || "proprietario"}` +
      ` · Telegram ${telegram}`;
  }
  const key = $("#keyInfo");
  if (key) {
    key.textContent = state.hasKey
      ? "C'e' una chiave segreta: mettila nell'URL per entrare con un tocco."
      : "Nessuna chiave. Generane una per entrare con un tocco.";
  }
}

async function setAccessPassword() {
  const hint = $("#accessResult");
  const password = $("#sAccessPassword").value;
  if (!password) {
    hint.textContent = "Scrivi la nuova password.";
    return;
  }
  try {
    const result = await api("/api/auth/access", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    hint.textContent = result.message || "Password impostata.";
    $("#sAccessPassword").value = "";
    state.hasKey = state.hasKey ?? false;
    renderAccessInfo();
  } catch (error) {
    hint.textContent = error.message;
  }
}

async function clearAccessPassword() {
  const hint = $("#accessResult");
  try {
    const result = await api("/api/auth/access", {
      method: "POST",
      body: JSON.stringify({ password: "" }),
    });
    hint.textContent = result.message || "Password rimossa.";
    renderAccessInfo();
  } catch (error) {
    hint.textContent = error.message;
  }
}

async function generateKey() {
  const hint = $("#accessResult");
  try {
    const result = await api("/api/auth/key", { method: "POST" });
    $("#keyValue").value = result.key;
    $("#keyBox").classList.remove("hidden");
    hint.textContent = result.message || "Chiave generata.";
    state.hasKey = true;
    renderAccessInfo();
  } catch (error) {
    hint.textContent = error.message;
  }
}

async function deleteKey() {
  const hint = $("#accessResult");
  try {
    const result = await api("/api/auth/key", { method: "DELETE" });
    $("#keyBox").classList.add("hidden");
    $("#keyValue").value = "";
    hint.textContent = result.message || "Chiave rimossa.";
    state.hasKey = false;
    renderAccessInfo();
  } catch (error) {
    hint.textContent = error.message;
  }
}

/* ---------- collegamento della chat Telegram ---------- */
async function telegramLink() {
  const hint = $("#telegramLinkResult");
  try {
    const result = await api("/api/auth/telegram/link", { method: "POST" });
    $("#telegramLinkCode").value = result.code;
    hint.textContent =
      result.message + " Poi scrivi al bot: /link " + result.code;
  } catch (error) {
    hint.textContent = error.message;
  }
}

async function telegramConfirm() {
  const hint = $("#telegramLinkResult");
  const code = $("#telegramLinkCode").value.trim();
  if (!code) {
    hint.textContent = "Genera prima il codice.";
    return;
  }
  try {
    const result = await api("/api/auth/telegram/confirm", {
      method: "POST",
      body: JSON.stringify({ code }),
    });
    hint.textContent = "Chat collegata.";
    if (state.user) state.user.telegram_chat_id = result.telegram_chat_id;
    renderAccessInfo();
    loadTelegramChats();
  } catch (error) {
    hint.textContent = error.message;
  }
}

/* ---------- chat e gruppi Telegram ---------- */
async function loadTelegramChats() {
  const list = $("#telegramChatsList");
  const info = $("#telegramChatsInfo");
  if (!list) return;
  try {
    const data = await api("/api/telegram/chats");
    const chats = data.chats || [];
    if (data.add_url) {
      $("#telegramAddBotBox")?.classList.remove("hidden");
      const link = $("#telegramAddBot");
      if (link) link.href = data.add_url;
    }
    if (!chats.length) {
      info.textContent =
        "Nessuna chat registrata. Aggiungimi a un gruppo (o a un canale): mi registro da solo e ti scrivo li'.";
      list.innerHTML = "";
      return;
    }
    info.textContent = `${chats.length} chat registrate. Gli avvisi arrivano in tutte, tranne se ne scegli una predefinita.`;
    list.innerHTML = chats
      .map(
        (chat) => `
        <div class="user-row">
          <span>${esc(chat.title || chat.username || chat.chat_id)}
            <small>${esc(chat.chat_type || "")} · ${esc(chat.chat_id)}${chat.is_default ? " · predefinita" : ""}</small>
          </span>
          <span>
            ${chat.is_default ? "" : `<button type="button" class="btn ghost tiny" data-tg-default="${esc(chat.chat_id)}">Predefinita</button>`}
            <button type="button" class="btn ghost tiny" data-tg-remove="${esc(chat.chat_id)}">Togli</button>
          </span>
        </div>`
      )
      .join("");

    list.querySelectorAll("[data-tg-default]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          await api(`/api/telegram/chats/${encodeURIComponent(button.dataset.tgDefault)}/default`, {
            method: "POST",
          });
          loadTelegramChats();
        } catch (error) {
          toast(error.message, "error");
        }
      })
    );
    list.querySelectorAll("[data-tg-remove]").forEach((button) =>
      button.addEventListener("click", async () => {
        try {
          await api(`/api/telegram/chats/${encodeURIComponent(button.dataset.tgRemove)}`, {
            method: "DELETE",
          });
          loadTelegramChats();
        } catch (error) {
          toast(error.message, "error");
        }
      })
    );
  } catch (error) {
    info.textContent = "✗ " + error.message;
  }
}

let gtfsPoll = null;

async function loadGtfsStatus() {
  try {
    renderGtfs(await api("/api/gtfs"));
  } catch (error) {
    $("#gtfsStatus").textContent = "✗ " + error.message;
    $("#gtfsStatus").className = "field-hint err";
    $("#gtfsFeeds").innerHTML = "";
  }
}

function renderGtfs(data) {
  const hint = $("#gtfsStatus");
  const box = $("#gtfsFeeds");
  const imported = (data.feeds || []).filter((feed) => feed.imported).length;
  if (data.running) {
    hint.textContent = (data.log || []).slice(-1)[0] || "aggiornamento in corso…";
    hint.className = "field-hint";
  } else {
    hint.textContent = `${imported}/${(data.feeds || []).length} feed importati · ${data.enabled ? "attivi" : "disattivati"}`;
    hint.className = "field-hint" + (imported ? " ok" : "");
  }
  box.innerHTML = (data.feeds || []).map((feed) => `
    <div class="gtfs-feed ${feed.imported ? "on" : ""}">
      <span class="gtfs-dot"></span>
      <span class="gtfs-name">${esc(feed.label)}</span>
      <span class="gtfs-meta">${feed.imported ? `${feed.stops} fermate · ${feed.trips} corse` : (feed.license || "")}</span>
    </div>`).join("");
}

async function updateGtfs() {
  const hint = $("#gtfsStatus");
  hint.textContent = "avvio aggiornamento…";
  hint.className = "field-hint";
  try {
    await api("/api/gtfs/update", { method: "POST" });
  } catch (error) {
    hint.textContent = "✗ " + error.message;
    hint.className = "field-hint err";
    return;
  }
  if (gtfsPoll) clearInterval(gtfsPoll);
  gtfsPoll = setInterval(async () => {
    try {
      const data = await api("/api/gtfs");
      renderGtfs(data);
      if (!data.running) {
        clearInterval(gtfsPoll);
        gtfsPoll = null;
        toast("Orari GTFS aggiornati");
      }
    } catch (_) { /* ignora errori di polling */ }
  }, 3000);
}

function urlBase64ToUint8Array(base64) {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

async function renderPushStatus() {
  const hint = $("#pushStatus");
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    hint.textContent = "Questo browser non supporta le notifiche push.";
    hint.className = "field-hint err";
    return;
  }
  try {
    const data = await api("/api/push/status");
    if (!data.available) {
      hint.textContent = "Server: libreria push non installata (pip install pywebpush).";
      hint.className = "field-hint err";
      return;
    }
    const registration = await navigator.serviceWorker.getRegistration();
    const subscription = registration ? await registration.pushManager.getSubscription() : null;
    hint.textContent = subscription
      ? `Attive su questo dispositivo · ${data.subscriptions} dispositivi collegati`
      : `Non attive su questo dispositivo · ${data.subscriptions} dispositivi collegati`;
    hint.className = "field-hint" + (subscription ? " ok" : "");
  } catch (error) {
    hint.textContent = "✗ " + error.message;
    hint.className = "field-hint err";
  }
}

async function enablePush() {
  const hint = $("#pushStatus");
  try {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      throw new Error("push non supportate da questo browser");
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("permesso notifiche negato");
    const data = await api("/api/push/status");
    if (!data.available) throw new Error("server: libreria push non installata");
    const registration = await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(data.public_key),
      });
    }
    const json = subscription.toJSON();
    await api("/api/push/subscribe", {
      method: "POST",
      body: JSON.stringify({
        endpoint: subscription.endpoint,
        keys: { p256dh: json.keys.p256dh, auth: json.keys.auth },
        label: navigator.platform || "dispositivo",
      }),
    });
    toast("Notifiche push attivate");
    await renderPushStatus();
  } catch (error) {
    hint.textContent = "✗ " + error.message;
    hint.className = "field-hint err";
  }
}

async function disablePush() {
  const hint = $("#pushStatus");
  try {
    const registration = await navigator.serviceWorker.getRegistration();
    const subscription = registration ? await registration.pushManager.getSubscription() : null;
    if (subscription) {
      await api("/api/push/unsubscribe", {
        method: "POST",
        body: JSON.stringify({ endpoint: subscription.endpoint }),
      });
      await subscription.unsubscribe();
    }
    toast("Notifiche push disattivate");
    await renderPushStatus();
  } catch (error) {
    hint.textContent = "✗ " + error.message;
    hint.className = "field-hint err";
  }
}

async function testPush() {
  const hint = $("#pushStatus");
  hint.textContent = "invio…";
  hint.className = "field-hint";
  try {
    const data = await api("/api/push/test", { method: "POST" });
    if (!data.ok) throw new Error(data.error || "nessun dispositivo raggiunto");
    toast("Notifica di prova inviata");
  } catch (error) {
    hint.textContent = "✗ " + error.message;
    hint.className = "field-hint err";
    return;
  }
  await renderPushStatus();
}

async function testTelegram() {
  const hint = $("#telegramTestResult");
  hint.textContent = "invio…";
  hint.className = "field-hint";
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(settingsPayload()) });
    const result = await api("/api/settings/test-telegram", { method: "POST" });
    hint.textContent = result.ok ? "✓ messaggio inviato" : "✗ " + result.error;
    hint.className = "field-hint " + (result.ok ? "ok" : "err");
  } catch (error) {
    hint.textContent = "✗ " + error.message;
    hint.className = "field-hint err";
  }
}

async function testEmail() {
  const hint = $("#emailTestResult");
  hint.textContent = "invio…";
  hint.className = "field-hint";
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(settingsPayload()) });
    const result = await api("/api/settings/test-email", { method: "POST" });
    hint.textContent = result.ok ? "✓ email inviata" : "✗ " + result.error;
    hint.className = "field-hint " + (result.ok ? "ok" : "err");
  } catch (error) {
    hint.textContent = "✗ " + error.message;
    hint.className = "field-hint err";
  }
}

/* ------------------------------------------------------------------ */
/* Avvisi                                                             */
/* ------------------------------------------------------------------ */
async function loadAlerts(initial = false) {
  const alerts = await api("/api/alerts?limit=40");
  state.alerts = alerts;
  const unread = alerts.filter((a) => !a.read);
  const badge = $("#alertBadge");
  badge.textContent = unread.length;
  badge.classList.toggle("hidden", unread.length === 0);

  if (alerts.length) {
    const maxId = Math.max(...alerts.map((a) => a.id));
    if (!initial && maxId > state.lastAlertId) {
      const newest = alerts.filter((a) => a.id > state.lastAlertId);
      newest.slice(0, 2).forEach((alert) => toast(alert.title + " · " + routeOfAlert(alert)));
      maybeDesktopNotify(newest);
    }
    state.lastAlertId = Math.max(state.lastAlertId, maxId);
    localStorage.setItem("ch_last_alert", String(state.lastAlertId));
  }

  renderAlerts();
}

function routeOfAlert(alert) {
  const watch = state.watches.find((w) => w.id === alert.watch_id);
  return watch ? `${watch.from_place} → ${watch.to_place}` : "";
}

function renderAlerts() {
  const box = $("#alertsList");
  if (!state.alerts.length) {
    box.innerHTML = `<div class="hint" style="padding:20px 0">Nessun avviso. Quando l'agente trova qualcosa di nuovo lo vedrai qui.</div>`;
    return;
  }
  box.innerHTML = state.alerts.map((alert) => `
    <div class="alert-item ${alert.read ? "" : "unread"}">
      <h4>${esc(alert.title)}</h4>
      <time>${fmtDate(alert.created_at)} · ${esc(routeOfAlert(alert))}</time>
      <p>${esc(alert.message)}</p>
    </div>`).join("");
}

function maybeDesktopNotify(alerts) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  for (const alert of alerts) {
    const watch = state.watches.find((w) => w.id === alert.watch_id);
    if (!watch || !watch.notify?.includes("desktop")) continue;
    try {
      new Notification("Carpool Hunter · " + alert.title, {
        body: routeOfAlert(alert),
        tag: "carpool-" + alert.id,
      });
    } catch (_) { /* ignore */ }
  }
}

async function markAllRead() {
  try {
    await api("/api/alerts/read", { method: "POST", body: JSON.stringify({}) });
    await loadAlerts(false);
    toast("Avvisi segnati come letti");
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ------------------------------------------------------------------ */
/* Offerte e codici sconto                                            */
/* ------------------------------------------------------------------ */
async function openPromos(force = false) {
  openModal("promosModal");
  const body = $("#promosBody");
  body.innerHTML = `<div class="loading">Carico le offerte…</div>`;
  try {
    const data = await api("/api/promos" + (force ? "?refresh=true" : ""));
    renderPromos(data);
  } catch (error) {
    body.innerHTML = `<div class="form-error">${esc(error.message)}</div>`;
  }
}

function promoCards(items, subtitle) {
  return items.map((item) => `
    <a class="link-card" href="${esc(item.url)}" target="_blank" rel="noreferrer">
      <div class="lc-head"><strong>${esc(item.title || item.operator)}</strong><span class="lc-arrow">↗</span></div>
      <small>${esc(subtitle(item))}</small>
    </a>`).join("");
}

function renderPromos(data) {
  const offers = data.offers || [];
  const coupons = data.coupons || [];
  const errors = data.errors || [];
  const updated = data.updated_at ? fmtDate(data.updated_at) : "";

  const offersHtml = offers.length
    ? promoCards(offers, (o) => `${o.operator || ""} · offerta`)
    : `<div class="hint" style="padding:10px 0">Nessuna offerta letta adesso: riprova tra poco o apri le pagine dei vettori qui sotto.</div>`;

  $("#promosBody").innerHTML = `
    <p class="muted" style="margin:0 0 14px">
      Offerte e codici letti direttamente dalle pagine ufficiali dei vettori.
      ${updated ? `Ultimo aggiornamento: ${esc(updated)}.` : ""}
    </p>
    <div class="section-title">🔥 Offerte del momento</div>
    <div class="links-grid">${offersHtml}</div>
    <div class="section-title">🎟️ Codici sconto &amp; voucher</div>
    <div class="links-grid">${promoCards(coupons, (c) => `${c.operator || ""}${c.note ? " · " + c.note : ""}`)}</div>
    ${errors.length ? `<small class="field-hint">Alcune pagine non hanno risposto: ${esc(errors.join(" · "))}</small>` : ""}`;
}

/* ------------------------------------------------------------------ */
/* AI: prova e modelli                                                */
/* ------------------------------------------------------------------ */
async function testAi() {
  const hint = $("#aiTestResult");
  hint.textContent = "provo…";
  hint.className = "field-hint";
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify(settingsPayload()) });
    const result = await api("/api/ai/test", { method: "POST" });
    if (result.ok) {
      hint.textContent = `✓ risposta da ${shortModel(result.model)}: “${result.reply}”`;
      hint.className = "field-hint ok";
      await loadStatus();
    } else {
      hint.textContent = "✗ " + (result.error || "nessuna risposta");
      hint.className = "field-hint err";
    }
  } catch (error) {
    hint.textContent = "✗ " + error.message;
    hint.className = "field-hint err";
  }
}

async function listAiModels() {
  const hint = $("#aiTestResult");
  hint.textContent = "carico l'elenco…";
  hint.className = "field-hint";
  try {
    // Salva prima: la chiave potrebbe essere appena stata inserita.
    await api("/api/settings", { method: "PUT", body: JSON.stringify(settingsPayload()) });
    await loadModelOptions($("#sAiSmart").value, $("#sAiFast").value);
  } catch (error) {
    hint.textContent = "✗ " + error.message;
    hint.className = "field-hint err";
  }
}

/* ------------------------------------------------------------------ */
/* PWA: service worker e installazione                                */
/* ------------------------------------------------------------------ */
function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("sw.js").catch(() => { /* offline non disponibile */ });
}

let deferredPrompt = null;
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredPrompt = event;
  const button = $("#btnInstall");
  if (button) {
    button.classList.remove("hidden");
    button.addEventListener("click", async () => {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      deferredPrompt = null;
      button.classList.add("hidden");
    }, { once: true });
  }
});

/* Rende disponibile runNow() agli onclick inline generati da emptyTab */
window.runNow = runNow;
