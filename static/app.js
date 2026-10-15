"use strict";

const VEHICLE_TYPES = { 1: "Автобус", 2: "Троллейбус", 3: "Трамвай" };
const FORECAST_REFRESH_MS = 20000;

let favorites = { routes: [], stops: [] };
let refreshTimer = null;
let routeGen = 0;

/* ---------- utilities ---------- */

function routeTypeName(id) {
  return VEHICLE_TYPES[id] || "Другое";
}

function routeTypeClass(id) {
  return VEHICLE_TYPES[id] ? "type-" + id : "type-other";
}

function formatEta(sec) {
  if (sec === null || sec === undefined || isNaN(sec) || sec < 0) return "—";
  const min = Math.floor(sec / 60);
  if (min < 1) return "меньше минуты";
  return `через ${min} мин`;
}

function etaClass(sec) {
  if (typeof sec !== "number" || isNaN(sec)) return "";
  if (sec < 60) return "now";
  if (sec < 180) return "soon";
  return "";
}

function arrivalClock(sec) {
  if (typeof sec !== "number" || isNaN(sec) || sec < 0) return "";
  return new Date(Date.now() + sec * 1000).toLocaleTimeString("ru-RU", {
    hour: "2-digit", minute: "2-digit",
  });
}

function timeStr(date) {
  return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function el(tag, props, children) {
  const node = document.createElement(tag);
  if (props) {
    for (const key in props) {
      if (key === "class") node.className = props[key];
      else if (key === "text") node.textContent = props[key];
      else node.setAttribute(key, props[key]);
    }
  }
  (children || []).forEach((child) => {
    if (child === null || child === undefined) return;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  });
  return node;
}

function showBanner(text, kind) {
  const banner = document.getElementById("banner");
  if (!text) {
    banner.className = "banner hidden";
    banner.textContent = "";
    return;
  }
  banner.className = "banner " + (kind || "info");
  banner.textContent = text;
}

async function api(path, options) {
  let resp;
  try {
    resp = await fetch(path, options);
  } catch (err) {
    throw new Error("нет связи с сервером");
  }
  let data = null;
  try {
    data = await resp.json();
  } catch (err) {
    data = null;
  }
  if (!resp.ok) {
    const error = (data && data.error) || {};
    throw new Error(error.message || error.reason || ("HTTP " + resp.status));
  }
  return data || {};
}

/* ---------- favorites ---------- */

function favList(kind) {
  return kind === "route" ? favorites.routes : favorites.stops;
}

function isFav(kind, id) {
  return favList(kind).some((item) => String(item.id) === String(id));
}

async function toggleFavorite(kind, id, name) {
  const action = isFav(kind, id) ? "remove" : "add";
  try {
    const data = await api("/api/favorites", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action, kind: kind, id: id, name: name }),
    });
    favorites = { routes: data.routes || [], stops: data.stops || [] };
    showBanner(action === "add" ? "Добавлено в избранное" : "Удалено из избранного", "info");
    return true;
  } catch (err) {
    showBanner("Избранное: " + err.message, "error");
    return false;
  }
}

function starButton(kind, id, name) {
  const button = el("button", { class: "star" + (isFav(kind, id) ? " on" : ""), title: "В избранное" });
  button.textContent = isFav(kind, id) ? "★" : "☆";
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    const changed = await enqueueStar(kind, id, name);
    button.className = "star" + (isFav(kind, id) ? " on" : "");
    button.textContent = isFav(kind, id) ? "★" : "☆";
  });
  return button;
}

// Serialize favorite mutations so rapid clicks cannot race.
let starChain = Promise.resolve();
function enqueueStar(kind, id, name) {
  starChain = starChain.then(() => toggleFavorite(kind, id, name));
  return starChain;
}

/* ---------- forecast rendering ---------- */

function renderForecastList(container, forecasts) {
  container.innerHTML = "";
  const items = (forecasts || []).slice().sort((a, b) => {
    const av = typeof a.arrivalTimeInSec === "number" ? a.arrivalTimeInSec : Infinity;
    const bv = typeof b.arrivalTimeInSec === "number" ? b.arrivalTimeInSec : Infinity;
    return av - bv;
  });
  if (!items.length) {
    container.appendChild(el("div", { class: "empty" }, ["Нет данных о прибытии."]));
    return;
  }
  items.forEach((f) => {
    const row = el("div", { class: "row" });
    row.appendChild(el("span", { class: "num " + routeTypeClass(f.routeTypeId) },
      [f.routeNumber || f.routeShortName || "?"]));
    const main = el("div", { class: "main" });
    main.appendChild(el("div", { class: "name" },
      [f.whereGo ? "→ " + f.whereGo : (f.routeName || "")]));
    if (f.lastStation) {
      main.appendChild(el("div", { class: "meta" }, ["от: " + f.lastStation]));
    }
    row.appendChild(main);
    const eta = el("div", { class: "eta " + etaClass(f.arrivalTimeInSec) });
    let text = formatEta(f.arrivalTimeInSec);
    const clock = arrivalClock(f.arrivalTimeInSec);
    if (clock) text += " (" + clock + ")";
    eta.textContent = text;
    row.appendChild(eta);
    container.appendChild(row);
  });
}

function forecastPanel(sid) {
  const list = el("div");
  let last = null;
  let lastAt = null;
  let lastError = null;
  async function tick() {
    try {
      const data = await api("/api/forecast?sid=" + encodeURIComponent(sid));
      last = data.forecasts || [];
      lastAt = new Date();
      lastError = null;
      renderForecastList(list, last);
    } catch (err) {
      lastError = err;
      if (last) {
        renderForecastList(list, last);
      } else {
        list.innerHTML = "";
        list.appendChild(el("div", { class: "empty muted" }, ["Прогноз недоступен: " + err.message]));
      }
    }
    return { ok: !lastError, at: lastAt, stale: Boolean(lastError && last), error: lastError };
  }
  return { list: list, tick: tick };
}

/* ---------- views ---------- */

function routeRow(r) {
  const row = el("div", { class: "row" });
  row.appendChild(el("span", { class: "num " + routeTypeClass(r.routeTypeId) },
    [r.number || r.shortName || "?"]));
  const main = el("div", { class: "main" });
  const link = el("a", { class: "link", href: "#/route/" + encodeURIComponent(r.id) });
  link.appendChild(el("div", { class: "name" }, [r.name || r.shortName || ("Маршрут " + r.id)]));
  link.appendChild(el("div", { class: "meta" }, [routeTypeName(r.routeTypeId)]));
  main.appendChild(link);
  row.appendChild(main);
  row.appendChild(starButton("route", r.id, r.name || r.number));
  return row;
}

function stationRow(st) {
  const row = el("div", { class: "row" });
  const main = el("div", { class: "main" });
  const link = el("a", { class: "link", href: "#/stop/" + encodeURIComponent(st.id) });
  link.appendChild(el("div", { class: "name" }, [st.name || ("Остановка " + st.id)]));
  if (st.adress || st.description) {
    link.appendChild(el("div", { class: "meta" }, [st.adress || st.description]));
  }
  main.appendChild(link);
  row.appendChild(main);
  row.appendChild(starButton("stop", st.id, st.name));
  return row;
}

async function renderFavorites(view) {
  const gen = routeGen;
  view.appendChild(el("h2", {}, ["Избранное"]));
  if (!favorites.routes.length && !favorites.stops.length) {
    view.appendChild(el("div", { class: "empty" },
      ["Пока пусто. Добавьте маршруты в разделе «Маршруты», остановки — в «Остановки»."]));
    return;
  }
  if (favorites.routes.length) {
    view.appendChild(el("div", { class: "sub" }, ["Маршруты"]));
    const box = el("div");
    favorites.routes.forEach((r) => box.appendChild(routeRow(r)));
    view.appendChild(box);
  }
  if (favorites.stops.length) {
    view.appendChild(el("div", { class: "sub" }, ["Остановки"]));
    // Collapsed rows: a stop shows only its name until clicked, so a long
    // favorites list stays compact. The forecast is fetched lazily on expand.
    const expanded = new Set();
    const panels = new Map();

    favorites.stops.forEach((s) => {
      const sid = String(s.id);
      const card = el("div", { class: "card stop-card" });
      const head = el("div", {
        class: "row stop-head", role: "button", tabindex: "0", "aria-expanded": "false",
      });
      const chevron = el("span", { class: "chevron" }, ["▸"]);
      head.appendChild(chevron);
      head.appendChild(el("div", { class: "main" }, [
        el("div", { class: "name" }, [s.name || ("Остановка " + s.id)]),
      ]));
      head.appendChild(starButton("stop", s.id, s.name));

      const panel = forecastPanel(s.id);
      panels.set(sid, panel);
      const details = el("div", { class: "stop-details hidden" });
      details.appendChild(panel.list);
      details.appendChild(el("div", { class: "stop-open" }, [
        el("a", { class: "link", href: "#/stop/" + encodeURIComponent(s.id) },
          ["Все прибытия и обновление →"]),
      ]));

      function toggle() {
        const willOpen = !expanded.has(sid);
        if (willOpen) expanded.add(sid); else expanded.delete(sid);
        details.classList.toggle("hidden", !willOpen);
        card.classList.toggle("open", willOpen);
        head.setAttribute("aria-expanded", String(willOpen));
        chevron.textContent = willOpen ? "▾" : "▸";
        if (willOpen) panel.tick();
      }
      head.addEventListener("click", toggle);
      head.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });

      card.appendChild(head);
      card.appendChild(details);
      view.appendChild(card);
    });

    async function refresh() {
      const open = [...expanded].map((sid) => panels.get(sid)).filter(Boolean);
      if (!open.length) return;
      const results = await Promise.all(open.map((p) => p.tick()));
      if (gen !== routeGen) return;
      const ok = results.every((r) => r.ok);
      const at = results.map((r) => r.at).filter(Boolean).pop();
      if (ok && at) showBanner("Обновлено в " + timeStr(at), "info");
      else if (results.some((r) => r.stale)) showBanner("Часть данных устарела (обновление не удалось)", "warn");
    }
    refreshTimer = setInterval(refresh, FORECAST_REFRESH_MS);
  }
}

async function renderRoutes(view) {
  view.appendChild(el("h2", {}, ["Каталог маршрутов"]));
  const controls = el("div", { class: "controls" });
  const search = el("input", { type: "search", placeholder: "Поиск по номеру или названию" });
  const typeSel = el("select");
  [["", "Все виды"], ["1", "Автобус"], ["2", "Троллейбус"], ["3", "Трамвай"]].forEach((pair) => {
    const option = el("option", { value: pair[0] }, [pair[1]]);
    typeSel.appendChild(option);
  });
  controls.appendChild(search);
  controls.appendChild(typeSel);
  view.appendChild(controls);
  const listBox = el("div");
  view.appendChild(listBox);

  let routes = [];
  try {
    routes = (await api("/api/routes")).routes || [];
  } catch (err) {
    showBanner("Не удалось загрузить маршруты: " + err.message, "error");
    return;
  }

  function draw() {
    const query = search.value.trim().toLowerCase();
    const type = typeSel.value;
    listBox.innerHTML = "";
    const filtered = routes.filter((r) => {
      if (type && String(r.routeTypeId) !== type) return false;
      if (!query) return true;
      const hay = [r.number, r.shortName, r.name, r.fromStation, r.toStation]
        .filter(Boolean).join(" ").toLowerCase();
      return hay.indexOf(query) !== -1;
    });
    if (!filtered.length) {
      listBox.appendChild(el("div", { class: "empty" }, ["Ничего не найдено."]));
      return;
    }
    filtered.forEach((r) => listBox.appendChild(routeRow(r)));
  }

  search.addEventListener("input", draw);
  typeSel.addEventListener("change", draw);
  draw();
}

async function renderRoute(view, id) {
  let route;
  try {
    route = (await api("/api/route?id=" + encodeURIComponent(id))).route || {};
  } catch (err) {
    showBanner("Не удалось загрузить маршрут: " + err.message, "error");
    return;
  }
  const title = route.number ? route.number + (route.name ? " · " + route.name : "") : (route.name || "Маршрут");
  view.appendChild(el("h2", {}, [title]));
  const bits = [routeTypeName(route.type || route.routeTypeId)];
  if (route.fromStation && route.toStation) bits.push(route.fromStation + " – " + route.toStation);
  view.appendChild(el("div", { class: "sub" }, [bits.join(" · ")]));
  const headerRow = el("div", { class: "row" });
  headerRow.appendChild(starButton("route", route.id, route.name || route.number));
  headerRow.appendChild(el("div", { class: "main" }, ["Добавить маршрут в избранное"]));
  view.appendChild(headerRow);

  const subroutes = route.subroutes || [];
  if (!subroutes.length) {
    view.appendChild(el("div", { class: "empty" }, ["Нет данных о направлениях."]));
    return;
  }
  const tabs = el("div", { class: "tabs" });
  const listBox = el("div");
  view.appendChild(tabs);
  view.appendChild(listBox);
  let active = 0;

  function drawStations() {
    listBox.innerHTML = "";
    const stations = (subroutes[active] && subroutes[active].stations) || [];
    if (!stations.length) {
      listBox.appendChild(el("div", { class: "empty" }, ["Нет остановок."]));
      return;
    }
    stations.forEach((st, index) => {
      const row = el("div", { class: "row" });
      row.appendChild(el("span", { class: "num type-other" }, [String(index + 1)]));
      const main = el("div", { class: "main" });
      const link = el("a", { class: "link", href: "#/stop/" + encodeURIComponent(st.id) });
      link.appendChild(el("div", { class: "name" }, [st.name || ("Остановка " + st.id)]));
      if (st.description) link.appendChild(el("div", { class: "meta" }, [st.description]));
      main.appendChild(link);
      row.appendChild(main);
      row.appendChild(starButton("stop", st.id, st.name));
      listBox.appendChild(row);
    });
  }

  function drawTabs() {
    tabs.innerHTML = "";
    subroutes.forEach((sub, index) => {
      const button = el("button", { class: index === active ? "active" : "" },
        [sub.name || ("Направление " + (index + 1))]);
      button.addEventListener("click", () => { active = index; drawTabs(); });
      tabs.appendChild(button);
    });
    drawStations();
  }

  drawTabs();
}

async function renderStops(view) {
  view.appendChild(el("h2", {}, ["Поиск остановок"]));
  const controls = el("div", { class: "controls" });
  const search = el("input", { type: "search", placeholder: "Название остановки" });
  controls.appendChild(search);
  view.appendChild(controls);
  const listBox = el("div");
  view.appendChild(listBox);
  let debounce = null;

  async function run() {
    const query = search.value.trim();
    listBox.innerHTML = "";
    try {
      const stations = (await api("/api/stations?q=" + encodeURIComponent(query))).stations || [];
      if (!stations.length) {
        listBox.appendChild(el("div", { class: "empty" }, ["Ничего не найдено."]));
        return;
      }
      stations.slice(0, 100).forEach((st) => listBox.appendChild(stationRow(st)));
    } catch (err) {
      showBanner("Поиск не удался: " + err.message, "error");
    }
  }

  search.addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(run, 250);
  });
  run();
}

async function renderStop(view, sid) {
  const gen = routeGen;
  const fav = favorites.stops.find((s) => String(s.id) === String(sid));
  view.appendChild(el("h2", {}, [fav && fav.name ? fav.name : ("Остановка " + sid)]));
  view.appendChild(el("div", { class: "sub" }, ["Прибытие по всем маршрутам"]));
  const starRow = el("div", { class: "row" });
  starRow.appendChild(starButton("stop", Number(sid), fav && fav.name));
  starRow.appendChild(el("div", { class: "main" }, ["Добавить остановку в избранное"]));
  view.appendChild(starRow);

  const panel = forecastPanel(sid);
  view.appendChild(panel.list);

  async function refresh() {
    const result = await panel.tick();
    if (result.ok && result.at) showBanner("Обновлено в " + timeStr(result.at), "info");
    else if (result.stale) showBanner("Данные устарели: " + result.error.message, "warn");
  }
  await refresh();
  if (gen !== routeGen) return;
  refreshTimer = setInterval(refresh, FORECAST_REFRESH_MS);
}

/* ---------- router ---------- */

function stopRefresh() {
  if (refreshTimer !== null) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

async function route() {
  stopRefresh();
  routeGen += 1;
  showBanner("");
  const view = document.getElementById("view");
  view.innerHTML = "";
  const hash = location.hash || "#/";
  const parts = hash.replace(/^#\/?/, "").split("/");
  if (parts[0] === "") return renderFavorites(view);
  if (parts[0] === "routes") return renderRoutes(view);
  if (parts[0] === "route") return renderRoute(view, parts[1]);
  if (parts[0] === "stops") return renderStops(view);
  if (parts[0] === "stop") return renderStop(view, parts[1]);
  view.appendChild(el("div", { class: "empty" }, ["Страница не найдена."]));
}

async function boot() {
  window.addEventListener("hashchange", route);
  try {
    favorites = await api("/api/favorites");
  } catch (err) {
    favorites = { routes: [], stops: [] };
  }
  route();
}

boot();
