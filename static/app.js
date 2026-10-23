"use strict";

const FORECAST_REFRESH_MS = 20000;

// Таблица видов транспорта живёт в vehicle-types.js (одна точка правды).
const { routeTypeName, routeTypeClass, typeOptions } = VehicleTypes;
// Группировка остановок в «платформы» — в stops-group.js.
const { groupStops } = StopsGroup;

let favorites = { routes: [], stops: [] };
let refreshTimer = null;
let routeGen = 0;
const openVehicles = new Set(); // открытые карточки бортов (переживают авто-обновление)

/* ---------- utilities ---------- */

function typeIdsFromRoutes(routes) {
  const ids = new Set();
  (routes || []).forEach((r) => { if (r && r.routeTypeId) ids.add(r.routeTypeId); });
  return [...ids].sort((a, b) => a - b);
}

function fillTypeDots(box, ids) {
  box.innerHTML = "";
  if (!ids || !ids.length) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  ids.forEach((id) => {
    box.appendChild(el("span", { class: "dot " + routeTypeClass(id), title: routeTypeName(id) }));
  });
  box.appendChild(el("span", { class: "type-names" }, [ids.map(routeTypeName).join(" · ")]));
}

async function loadStopRoutes(sid) {
  try {
    const data = await api("/api/stop-routes?sid=" + encodeURIComponent(sid));
    return data.routes || [];
  } catch (err) {
    return [];
  }
}

async function loadStopTypes(sid) {
  return typeIdsFromRoutes(await loadStopRoutes(sid));
}

// Состояние интерфейса, переживающее переходы: hash-роутер каждый раз
// перерисовывает вид целиком, поэтому фильтры и прокрутку храним здесь.
const uiState = {
  stops: { q: "" },
  routes: { q: "", type: "" },
  scroll: {},
  current: null,
  pendingScroll: null,
};

function formatEta(sec) {
  if (sec === null || sec === undefined || isNaN(sec) || sec < 0) return "—";
  const min = Math.floor(sec / 60);
  if (min < 1) return "меньше минуты";
  return `${min} мин`;
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

/* ---------- единый бар действий ---------- */

const ACTION_ICONS = {
  pin: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2a7 7 0 0 0-7 7c0 5.2 7 13 7 13s7-7.8 7-13a7 7 0 0 0-7-7zm0 9.5a2.5 2.5 0 1 1 0-5 2.5 2.5 0 0 1 0 5z" fill="currentColor"/></svg>',
  route: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19h10a3.5 3.5 0 0 0 0-7H9a3.5 3.5 0 0 1 0-7h10" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><path d="M16 2l3.5 3L16 8M19 15l-3.5 3 3.5 3" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 7v5.3l3.4 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
};

function iconButton(icon, label, href) {
  const link = el("a", { class: "icon-btn", href: href, "aria-label": label, title: label });
  link.innerHTML = ACTION_ICONS[icon] || "";
  return link;
}

// items: { icon, label, href } и/или { star: true, kind, id, name }.
function actionBar(items) {
  const bar = el("div", { class: "actions" });
  (items || []).forEach((item) => {
    if (!item) return;
    if (item.star) bar.appendChild(starButton(item.kind, item.id, item.name));
    else bar.appendChild(iconButton(item.icon, item.label, item.href));
  });
  return bar;
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
  const groups = new Map();
  items.forEach((f) => {
    const where = f.whereGo || f.routeName || "—";
    if (!groups.has(where)) groups.set(where, []);
    groups.get(where).push(f);
  });
  groups.forEach((arrivals, where) => {
    container.appendChild(renderDirGroup(where, arrivals));
  });
}

function renderDirGroup(where, arrivals) {
  const group = el("div", { class: "dir-group" });
  const head = el("div", { class: "dir-head" });
  head.appendChild(el("span", { class: "dir-label" }, ["до " + where]));
  group.appendChild(head);

  arrivals.forEach((f) => {
    const item = el("div", { class: "arrival" });
    const row = el("div", { class: "row dir-row" });
    row.appendChild(el("span", { class: "num " + routeTypeClass(f.routeTypeId) },
      [f.routeNumber || f.routeShortName || "?"]));
    const main = el("div", { class: "main" });
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
    item.appendChild(row);

    if (f.deviceCode) {
      const code = f.deviceCode;
      const card = el("div", { class: "vehicle-card hidden" });
      item.appendChild(card);
      row.classList.add("clickable");
      let loaded = false;
      const openCard = async () => {
        card.classList.remove("hidden");
        row.classList.add("open");
        openVehicles.add(code);
        if (loaded) return;
        loaded = true;
        card.innerHTML = "";
        card.appendChild(el("div", { class: "muted" }, ["Загрузка…"]));
        try {
          const data = await api("/api/vehicle?deviceCode=" + encodeURIComponent(code));
          renderVehicleCard(card, data);
        } catch (err) {
          card.innerHTML = "";
          card.appendChild(el("div", { class: "empty muted" },
            ["Борт недоступен: " + err.message]));
          loaded = false;
        }
      };
      row.addEventListener("click", () => {
        if (openVehicles.has(code)) {
          openVehicles.delete(code);
          card.classList.add("hidden");
          row.classList.remove("open");
        } else {
          openCard();
        }
      });
      if (openVehicles.has(code)) openCard();
    }

    group.appendChild(item);
  });

  return group;
}

// Карточка борта: ГРЗ/гараж. номер · Модель · Класс ТС + следующие остановки.
function renderVehicleCard(card, data) {
  card.innerHTML = "";
  const v = (data && data.vehicle && data.vehicle.vehicle) || null;
  if (!v) {
    card.appendChild(el("div", { class: "empty muted" }, ["Нет данных о борте"]));
  } else {
    const fields = el("div", { class: "vehicle-card__fields" });
    const numbers = [v.gosNumber, v.garageNumber].filter(Boolean).join(" / ") || "—";
    const model = [v.brand, v.model].filter(Boolean).join(" ") || "—";
    const klass = (v.vehicleClass && (v.vehicleClass.name || v.vehicleClass.shortName)) || "—";
    [["ГРЗ/гараж. номер", numbers], ["Модель", model], ["Класс ТС", klass]].forEach((pair) => {
      const field = el("div", { class: "vehicle-card__field" });
      field.appendChild(el("div", { class: "vehicle-card__label" }, [pair[0]]));
      field.appendChild(el("div", { class: "vehicle-card__value" }, [pair[1]]));
      fields.appendChild(field);
    });
    card.appendChild(fields);
  }
  const stops = (data && data.forecasts) || [];
  if (stops.length) {
    card.appendChild(el("div", { class: "vehicle-card__stops-title" }, ["Следующие остановки"]));
    const list = el("div", { class: "vehicle-card__stops" });
    stops.slice(0, 12).forEach((s) => {
      const r = el("div", { class: "vehicle-card__stop" });
      r.appendChild(el("span", { class: "vehicle-card__stop-name" }, [s.stname || "—"]));
      r.appendChild(el("span", { class: "eta" }, [formatEta(s.arrt)]));
      list.appendChild(r);
    });
    card.appendChild(list);
  }
}

function forecastPanel(sid, stop) {
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
  const card = el("div", { class: "card route-card" });
  const head = el("div", {
    class: "row stop-head",
    role: "button",
    tabindex: "0",
    "aria-expanded": "false",
    "aria-label": "Действия с маршрутом",
  });
  const chevron = el("span", { class: "chevron" }, ["▸"]);
  head.appendChild(chevron);
  head.appendChild(el("span", { class: "num " + routeTypeClass(r.routeTypeId) },
    [r.number || r.shortName || "?"]));
  const main = el("div", { class: "main" });
  const link = el("a", { class: "link", href: "#/route/" + encodeURIComponent(r.id) });
  link.appendChild(el("div", { class: "name" }, [r.name || r.shortName || ("Маршрут " + r.id)]));
  link.appendChild(el("div", { class: "meta" }, [routeTypeName(r.routeTypeId)]));
  main.appendChild(link);
  head.appendChild(main);
  head.appendChild(starButton("route", r.id, r.name || r.number));
  card.appendChild(head);

  const details = el("div", { class: "stop-details hidden" });
  details.appendChild(actionBar([
    { icon: "pin", label: "Показать на карте", href: "#/map/route/" + encodeURIComponent(r.id) },
    { icon: "route", label: "Открыть маршрут", href: "#/route/" + encodeURIComponent(r.id) },
  ]));
  card.appendChild(details);

  let open = false;
  function toggle() {
    open = !open;
    details.classList.toggle("hidden", !open);
    card.classList.toggle("open", open);
    head.setAttribute("aria-expanded", open ? "true" : "false");
    chevron.textContent = open ? "▾" : "▸";
  }
  head.addEventListener("click", (event) => {
    if (event.target.closest("a, .star")) return;
    toggle();
  });
  head.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    toggle();
  });
  return card;
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

      const nameLine = el("div", { class: "name" });
      nameLine.appendChild(document.createTextNode(s.name || ("Остановка " + s.id)));
      const typeDots = el("span", { class: "type-dots hidden" });
      nameLine.appendChild(typeDots);
      const chips = el("span", { class: "route-chips hidden" });
      nameLine.appendChild(chips);
      const mainBox = el("div", { class: "main" });
      mainBox.appendChild(nameLine);
      head.appendChild(mainBox);

      head.appendChild(starButton("stop", s.id, s.name));

      const panel = forecastPanel(s.id, s);
      loadStopRoutes(s.id).then((routes) => {
        if (gen !== routeGen) return;
        fillTypeDots(typeDots, typeIdsFromRoutes(routes));
        if (routes.length) {
          chips.classList.remove("hidden");
          fillRouteChips(chips, routes);
        }
      });
      panels.set(sid, panel);
      const details = el("div", { class: "stop-details hidden" });
      details.appendChild(panel.list);
      const openBox = actionBar([
        { icon: "clock", label: "Все прибытия и обновление", href: "#/stop/" + encodeURIComponent(s.id) },
        { icon: "pin", label: "Показать на карте", href: "#/map/" + encodeURIComponent(s.id) },
      ]);
      details.appendChild(openBox);

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
  [["", "Все виды"]].concat(typeOptions()).forEach((pair) => {
    const option = el("option", { value: pair[0] }, [pair[1]]);
    typeSel.appendChild(option);
  });
  search.value = uiState.routes.q;
  typeSel.value = uiState.routes.type;
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
      applyPendingScroll();
      return;
    }
    filtered.forEach((r) => listBox.appendChild(routeRow(r)));
    applyPendingScroll();
  }

  search.addEventListener("input", () => {
    uiState.routes.q = search.value;
    draw();
  });
  typeSel.addEventListener("change", () => {
    uiState.routes.type = typeSel.value;
    draw();
  });
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
  const headerRow = actionBar([
    { star: true, kind: "route", id: route.id, name: route.name || route.number },
    { icon: "pin", label: "Показать маршрут на карте", href: "#/map/route/" + encodeURIComponent(route.id || id) },
  ]);
  view.appendChild(headerRow);

  const subroutes = (route.subroutes || []).slice()
    .sort((a, b) => (b.isMain ? 1 : 0) - (a.isMain ? 1 : 0));

  function directionLabel(sub) {
    const st = sub.stations || [];
    const from = st.length ? (st[0].name || "") : "";
    const to = st.length ? (st[st.length - 1].name || "") : "";
    if (from && to && from !== to) return from + " ▸ " + to;
    return sub.name || "Направление";
  }

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
        [directionLabel(sub)]);
      button.addEventListener("click", () => { active = index; drawTabs(); });
      tabs.appendChild(button);
    });
    drawStations();
  }

  drawTabs();
}

/* ---------- поиск остановок (вариант A: обогащение, вариант B: платформы) ---------- */

function platformWord(n) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "платформа";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "платформы";
  return "платформ";
}

function fillRouteChips(box, routes) {
  box.innerHTML = "";
  (routes || []).forEach((r) => {
    const chip = el("a", {
      class: "chip " + routeTypeClass(r.routeTypeId),
      href: "#/route/" + encodeURIComponent(r.id),
      title: routeTypeName(r.routeTypeId) + " " + (r.name || r.number || ""),
    }, [String(r.number || r.shortName || "?")]);
    // Карточка раскрывается по клику; чип должен вести на маршрут.
    chip.addEventListener("click", (event) => event.stopPropagation());
    box.appendChild(chip);
  });
}

function routeChips(routes) {
  const box = el("div", { class: "route-chips" });
  fillRouteChips(box, routes);
  return box;
}

// Карточка остановки в поиске: свёрнутая строка, клик раскрывает прогноз снизу.
function stopResultRow(st) {
  const card = el("div", { class: "card stop-card" });
  const head = el("div", {
    class: "row stop-head", role: "button", tabindex: "0", "aria-expanded": "false",
  });
  const chevron = el("span", { class: "chevron" }, ["▸"]);
  head.appendChild(chevron);
  const main = el("div", { class: "main" });
  const nameLine = el("div", { class: "name" }, [st.name || ("Остановка " + st.id)]);
  const dots = el("span", { class: "type-dots" });
  fillTypeDots(dots, typeIdsFromRoutes(st.routes));
  nameLine.appendChild(document.createTextNode(" "));
  nameLine.appendChild(dots);
  main.appendChild(nameLine);
  if (st.routes && st.routes.length) main.appendChild(routeChips(st.routes));
  head.appendChild(main);
  head.appendChild(starButton("stop", st.id, st.name));

  const panel = forecastPanel(st.id, st);
  const details = el("div", { class: "stop-details hidden" });
  details.appendChild(panel.list);
  details.appendChild(actionBar([
    { icon: "clock", label: "Все прибытия и обновление", href: "#/stop/" + encodeURIComponent(st.id) },
    { icon: "pin", label: "Показать на карте", href: "#/map/" + encodeURIComponent(st.id) },
  ]));

  let open = false;
  function toggle() {
    open = !open;
    details.classList.toggle("hidden", !open);
    card.classList.toggle("open", open);
    head.setAttribute("aria-expanded", open ? "true" : "false");
    chevron.textContent = open ? "▾" : "▸";
    if (open) panel.tick();
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
  return card;
}

// Группа «платформ»: одна строка-заголовок, по клику разворачивается.
function renderStopGroup(group) {
  if (group.platforms.length === 1) return stopResultRow(group.platforms[0]);
  const wrap = el("div", { class: "stop-group" });
  const row = el("div", { class: "row stop-group-head", role: "button", tabindex: "0", "aria-expanded": "false" });
  const main = el("div", { class: "main" });
  const nameLine = el("div", { class: "name" }, [group.name || "Без названия"]);
  nameLine.appendChild(document.createTextNode(" "));
  nameLine.appendChild(el("span", { class: "badge" },
    [group.platforms.length + " " + platformWord(group.platforms.length)]));
  main.appendChild(nameLine);
  row.appendChild(main);
  const chevron = el("span", { class: "chevron" }, ["▸"]);
  row.appendChild(chevron);
  const body = el("div", { class: "stop-group-body hidden" });
  group.platforms.forEach((p) => body.appendChild(stopResultRow(p)));
  let open = false;
  function toggle() {
    open = !open;
    body.classList.toggle("hidden", !open);
    row.classList.toggle("open", open);
    row.setAttribute("aria-expanded", open ? "true" : "false");
    chevron.textContent = open ? "▾" : "▸";
  }
  row.addEventListener("click", toggle);
  row.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    }
  });
  wrap.appendChild(row);
  wrap.appendChild(body);
  return wrap;
}

async function renderStops(view) {
  view.appendChild(el("h2", {}, ["Поиск остановок"]));
  const controls = el("div", { class: "controls" });
  const search = el("input", { type: "search", placeholder: "Название остановки" });
  search.value = uiState.stops.q;
  controls.appendChild(search);
  view.appendChild(controls);
  const listBox = el("div");
  view.appendChild(listBox);
  let debounce = null;

  async function run() {
    const query = search.value.trim();
    listBox.innerHTML = "";
    try {
      const stops = (await api("/api/stops?q=" + encodeURIComponent(query))).stops || [];
      const groups = groupStops(stops);
      if (!groups.length) {
        listBox.appendChild(el("div", { class: "empty" }, ["Ничего не найдено."]));
        applyPendingScroll();
        return;
      }
      groups.slice(0, 100).forEach((group) => listBox.appendChild(renderStopGroup(group)));
    } catch (err) {
      showBanner("Поиск не удался: " + err.message, "error");
    }
    applyPendingScroll();
  }

  search.addEventListener("input", () => {
    uiState.stops.q = search.value;
    clearTimeout(debounce);
    debounce = setTimeout(run, 250);
  });
  run();
}

async function renderStop(view, sid) {
  const gen = routeGen;
  const fav = favorites.stops.find((s) => String(s.id) === String(sid));
  const heading = el("h2", {}, [fav && fav.name ? fav.name : ("Остановка " + sid)]);
  view.appendChild(heading);
  // В адресе только id — настоящее название добираем с сервера.
  api("/api/stop?sid=" + encodeURIComponent(sid)).then((data) => {
    if (gen !== routeGen) return;
    const stop = data && data.stop;
    if (stop && stop.name) heading.textContent = stop.name;
  }).catch(() => {});
  const subLine = el("div", { class: "sub" }, ["Прибытие по всем маршрутам"]);
  const typeDots = el("span", { class: "type-dots hidden" });
  subLine.appendChild(document.createTextNode(" "));
  subLine.appendChild(typeDots);
  view.appendChild(subLine);
  view.appendChild(actionBar([
    { star: true, kind: "stop", id: Number(sid), name: fav && fav.name },
    { icon: "pin", label: "Показать на карте", href: "#/map/" + encodeURIComponent(sid) },
  ]));

  const panel = forecastPanel(sid, fav || null);
  view.appendChild(panel.list);
  loadStopTypes(sid).then((ids) => {
    if (gen !== routeGen) return;
    fillTypeDots(typeDots, ids);
  });

  async function refresh() {
    const result = await panel.tick();
    if (result.ok && result.at) showBanner("Обновлено в " + timeStr(result.at), "info");
    else if (result.stale) showBanner("Данные устарели: " + result.error.message, "warn");
  }
  await refresh();
  if (gen !== routeGen) return;
  refreshTimer = setInterval(refresh, FORECAST_REFRESH_MS);
}

/* ---------- карта остановок (MapLibre + растровые тайлы OSM) ---------- */

// MapLibre ждёт координаты в порядке [долгота, широта].
// Прямоугольник ограничивает карту Екатеринбургом и ближними окраинами.
const CITY_BOUNDS = [[60.35, 56.68], [60.85, 56.99]];
const MAP_CENTER = [60.6057, 56.8389];
const MAP_STYLE = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      maxzoom: 19,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};
// Иконка кнопки «Моё место» — стрелка-навигатор (как было до компаса).
const GEO_ARROW_SVG = '<svg viewBox="0 0 24 24" aria-hidden="true">' +
  '<path d="M21 3 L3 10.6 L10.3 13.7 L13.4 21 Z" fill="currentColor"/></svg>';
let maplibrePromise = null;
let mapStopsCache = null;
let mapInstance = null;
let vehicleTimer = null;

function stopVehiclePolling() {
  if (vehicleTimer !== null) {
    clearInterval(vehicleTimer);
    vehicleTimer = null;
  }
}

// MapLibre грузим только при первом открытии карты: остальным разделам
// его ~1 МБ (≈270 КБ gzip) не нужен.
function loadMapLibre() {
  if (window.maplibregl) return Promise.resolve();
  if (maplibrePromise) return maplibrePromise;
  maplibrePromise = new Promise((resolve, reject) => {
    document.head.appendChild(el("link", {
      rel: "stylesheet", href: "vendor/maplibre/maplibre-gl.css",
    }));
    const script = el("script", { src: "vendor/maplibre/maplibre-gl.js" });
    script.onload = () => resolve();
    script.onerror = () => {
      maplibrePromise = null;
      reject(new Error("maplibre"));
    };
    document.head.appendChild(script);
  });
  return maplibrePromise;
}

// Все остановки города одним запросом (~36 КБ gzip) и держим в памяти:
// при возврате на карту повторного запроса нет.
function loadMapStops() {
  if (mapStopsCache) return Promise.resolve(mapStopsCache);
  return api("/api/stations").then((data) => {
    mapStopsCache = (data.stations || []).filter(
      (s) => Number.isFinite(s.lat) && Number.isFinite(s.lng));
    return mapStopsCache;
  });
}

// Круг точности геолокации: MapLibre не умеет радиус в метрах, поэтому
// строим простой полигон вокруг точки (64 сегмента).
function accuracyCircle(lng, lat, radiusM, steps) {
  const count = steps || 64;
  const ring = [];
  const dLat = radiusM / 111320;
  const dLng = radiusM / (111320 * Math.max(Math.cos(lat * Math.PI / 180), 0.01));
  for (let i = 0; i <= count; i += 1) {
    const angle = (i / count) * Math.PI * 2;
    ring.push([lng + dLng * Math.cos(angle), lat + dLat * Math.sin(angle)]);
  }
  return {
    type: "Feature",
    properties: {},
    geometry: { type: "Polygon", coordinates: [ring] },
  };
}

function mapStopCard(st, onVehicles) {
  const card = el("div", { class: "card map-stop" });
  const head = el("div", { class: "row stop-head" });
  const main = el("div", { class: "main" });
  main.appendChild(el("div", { class: "name" }, [st.name || ("Остановка " + st.id)]));
  head.appendChild(main);
  head.appendChild(starButton("stop", st.id, st.name));
  card.appendChild(head);
  const panel = forecastPanel(st.id, st);
  card.appendChild(panel.list);
  // Ближайшие борта: сервер отдаёт только те, что есть в прогнозе остановки.
  const incoming = el("div", { class: "stop-vehicles hidden" });
  card.appendChild(incoming);
  api("/api/stop-vehicles?sid=" + encodeURIComponent(st.id)).then((data) => {
    const list = data.vehicles || [];
    if (!list.length) return;
    incoming.classList.remove("hidden");
    incoming.appendChild(el("div", { class: "sub" }, ["Транспорт на подходе"]));
    list.forEach((v) => {
      const bits = ["№ " + (v.routeNumber || "?")];
      if (v.gosNumber) bits.push(v.gosNumber);
      if (Number.isFinite(v.speed)) bits.push(Math.round(v.speed) + " км/ч");
      incoming.appendChild(el("div", { class: "vehicle-line" }, [bits.join(" · ")]));
    });
    if (onVehicles) onVehicles(list);
  }).catch(() => {});
  card.appendChild(actionBar([
    { icon: "clock", label: "Все прибытия и обновление", href: "#/stop/" + encodeURIComponent(st.id) },
  ]));
  panel.tick();
  return card;
}

// Стрелка борта для слоя карты: canvas → ImageData (addImage принимает его).
function vehicleArrowImage() {
  const size = 36;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.translate(size / 2, size / 2);
  ctx.beginPath();
  ctx.moveTo(0, -13);
  ctx.lineTo(9, 12);
  ctx.lineTo(0, 7);
  ctx.lineTo(-9, 12);
  ctx.closePath();
  ctx.fillStyle = "#1565c0";
  ctx.fill();
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#ffffff";
  ctx.stroke();
  return ctx.getImageData(0, 0, size, size);
}

function vehicleCard(v) {
  const card = el("div", { class: "card map-vehicle" });
  card.appendChild(el("div", { class: "name" }, ["Маршрут " + (v.routeNumber || "?")]));
  const bits = [];
  if (v.gosNumber) bits.push(v.gosNumber);
  if (Number.isFinite(v.speed)) bits.push(Math.round(v.speed) + " км/ч");
  card.appendChild(el("div", { class: "sub" }, [bits.join(" · ") || "В пути"]));
  if (v.routeId !== undefined && v.routeId !== null && v.routeId !== "") {
    card.appendChild(actionBar([
      { icon: "route", label: "Открыть маршрут", href: "#/route/" + encodeURIComponent(v.routeId) },
    ]));
  }
  return card;
}

// Высота шапки и нижнего меню зависит от шрифта и safe-area — меряем фактически,
// чтобы карта занимала ровно остаток экрана.
function syncMapChrome() {
  const topbar = document.querySelector(".topbar");
  const nav = document.querySelector(".topbar nav");
  const top = topbar ? topbar.offsetHeight : 0;
  const navH = nav && getComputedStyle(nav).position === "fixed" ? nav.offsetHeight : 0;
  document.documentElement.style.setProperty("--topbar-h", top + "px");
  document.documentElement.style.setProperty("--nav-h", navH + "px");
}

async function renderMap(view, focusId, routeId) {
  const gen = routeGen;
  syncMapChrome();
  const wrap = el("div", { class: "map-wrap" });
  const mapBox = el("div", { class: "map" });
  wrap.appendChild(mapBox);
  // Подсказка плавает поверх карты и исчезает после первого открытия карточки.
  const hint = el("div", { class: "map-hint" }, ["Нажмите на остановку"]);
  wrap.appendChild(hint);
  // Карточка остановки — нижний лист поверх карты: страницу скроллить не нужно.
  const sheet = el("div", { class: "map-sheet hidden", role: "dialog", "aria-label": "Остановка" });
  const sheetHead = el("div", { class: "map-sheet-head" });
  sheetHead.appendChild(el("span", { class: "map-sheet-grab" }));
  const sheetClose = el("button", {
    class: "map-sheet-close", type: "button", title: "Закрыть", "aria-label": "Закрыть",
  }, ["✕"]);
  sheetHead.appendChild(sheetClose);
  const sheetBody = el("div", { class: "map-sheet-body" });
  sheet.appendChild(sheetHead);
  sheet.appendChild(sheetBody);
  wrap.appendChild(sheet);
  view.appendChild(wrap);

  try {
    await loadMapLibre();
  } catch (err) {
    mapBox.appendChild(el("div", { class: "empty" }, ["Не удалось загрузить библиотеку карты."]));
    return;
  }
  if (gen !== routeGen) return;

  // Маршрут на карте: линия, его остановки и борта (данные уже есть в /api/route).
  let lineFeatures = [];
  let routeStopFeatures = [];
  let bounds = null;
  if (routeId) {
    try {
      const route = (await api("/api/route?id=" + encodeURIComponent(routeId))).route || {};
      (route.subroutes || []).forEach((sub) => {
        const coords = (sub.points || [])
          .filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lng))
          .map((p) => [p.lng, p.lat]);
        if (coords.length > 1) {
          lineFeatures.push({
            type: "Feature",
            properties: { id: sub.id || "" },
            geometry: { type: "LineString", coordinates: coords },
          });
          coords.forEach((c) => {
            if (!bounds) bounds = new maplibregl.LngLatBounds(c, c);
            else bounds.extend(c);
          });
        }
        (sub.stations || []).forEach((s) => {
          if (!Number.isFinite(s.lat) || !Number.isFinite(s.lng)) return;
          routeStopFeatures.push({
            type: "Feature",
            geometry: { type: "Point", coordinates: [s.lng, s.lat] },
            properties: { id: s.id, name: s.name || "" },
          });
        });
      });
    } catch (err) {
      showBanner("Не удалось загрузить маршрут: " + err.message, "error");
    }
    if (gen !== routeGen) return;
  }

  const map = new maplibregl.Map({
    container: mapBox,
    style: MAP_STYLE,
    center: MAP_CENTER,
    zoom: 12,
    minZoom: 11,
    maxZoom: 17,
    maxBounds: CITY_BOUNDS,
    attributionControl: false,
  });
  mapInstance = map;
  // Постоянной плашки нет: атрибуция свёрнута в кнопку «i» и убрана
  // в левый нижний угол, чтобы не путать её с кнопкой «Моё место».
  map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-left");
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.on("error", () => {}); // тайлы могут не догрузиться — карта остаётся рабочей
  if (bounds) map.fitBounds(bounds, { padding: 48, duration: 0 });

  // Событие "load" может наступить, пока мы ждём /api/stations (растровые
  // тайлы OSM грузятся быстрее ответа API). Слушатель ставим сразу, иначе
  // слои остановок и кнопка «Моё место» не появятся вовсе.
  let styleLoaded = false;
  map.on("load", () => {
    styleLoaded = true;
  });

  let selectedSource = null;
  let sheetOpen = false;
  let sheetMode = "half";

  const halfHeight = () => Math.round(window.innerHeight * 0.45);
  const fullHeight = () => Math.round(window.innerHeight * 0.92);

  function applyMode(mode) {
    sheetMode = mode;
    sheet.classList.toggle("full", mode === "full");
    sheet.style.height = (mode === "full" ? fullHeight() : halfHeight()) + "px";
  }

  function closeSheet() {
    if (!sheetOpen) return;
    sheetOpen = false;
    sheet.classList.remove("open");
    if (selectedSource) {
      selectedSource.setData({ type: "FeatureCollection", features: [] });
    }
    const stopVehicles = map.getSource("stop-vehicles");
    if (stopVehicles) {
      stopVehicles.setData({ type: "FeatureCollection", features: [] });
    }
    window.setTimeout(() => {
      if (!sheetOpen) {
        sheet.classList.add("hidden");
        sheetBody.innerHTML = "";
      }
    }, 230);
  }

  // Открывает любой узел в листе: карточку остановки или карточку борта.
  function openSheetNode(node, coords) {
    hint.classList.add("hidden");
    sheetBody.innerHTML = "";
    sheetBody.appendChild(node);
    sheetBody.scrollTop = 0;
    sheet.classList.remove("hidden");
    applyMode("half");
    sheetOpen = true;
    requestAnimationFrame(() => sheet.classList.add("open"));
    if (coords) {
      // Поднимаем точку над листом, чтобы маркер не спрятался за карточкой.
      map.easeTo({
        center: coords,
        padding: { bottom: Math.min(sheet.offsetHeight, Math.max(40, mapBox.offsetHeight - 80)) },
        duration: 350,
      });
    }
  }

  function openSheet(st, feature) {
    if (selectedSource) {
      selectedSource.setData({ type: "FeatureCollection", features: [feature] });
    }
    openSheetNode(mapStopCard(st, showStopVehicles), feature.geometry.coordinates);
  }

  function openVehicle(v, coords) {
    openSheetNode(vehicleCard(v), coords);
  }

  // Борта, которые подходят к выбранной остановке, показываем на карте.
  function showStopVehicles(list) {
    const source = map.getSource("stop-vehicles");
    if (!source) return;
    source.setData({
      type: "FeatureCollection",
      features: (list || []).map((v) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [v.lng, v.lat] },
        properties: {
          deviceCode: v.deviceCode,
          dir: Number.isFinite(v.dir) ? v.dir : 0,
          routeNumber: v.routeNumber || "",
          routeId: v.routeId,
          gosNumber: v.gosNumber || "",
          speed: v.speed || 0,
        },
      })),
    });
  }

  sheetClose.addEventListener("click", closeSheet);

  // Лист тянется за шапку: половина (45vh) ↔ почти весь экран (92vh).
  let pointerActive = false;
  let dragStartY = 0;
  let dragStartH = 0;
  let dragHeight = 0;
  let dragMoved = false;

  sheetHead.addEventListener("pointerdown", (event) => {
    if (event.target === sheetClose) return;
    pointerActive = true;
    dragMoved = false;
    dragStartY = event.clientY;
    dragStartH = sheet.offsetHeight;
    dragHeight = dragStartH;
    sheet.style.transition = "none";
    if (sheetHead.setPointerCapture) sheetHead.setPointerCapture(event.pointerId);
  });

  sheetHead.addEventListener("pointermove", (event) => {
    if (!pointerActive) return;
    const dy = dragStartY - event.clientY; // вверх — увеличиваем высоту
    if (Math.abs(dy) > 6) dragMoved = true;
    const min = Math.round(window.innerHeight * 0.25);
    dragHeight = Math.max(min, Math.min(fullHeight(), dragStartH + dy));
    sheet.style.height = dragHeight + "px";
  });

  const endDrag = () => {
    if (!pointerActive) return;
    pointerActive = false;
    sheet.style.transition = "";
    if (!dragMoved) {
      // Короткий тап по шапке: развернуть / свернуть.
      applyMode(sheetMode === "full" ? "half" : "full");
      return;
    }
    if (dragHeight < window.innerHeight * 0.3) {
      closeSheet();
      return;
    }
    applyMode(dragHeight > (halfHeight() + fullHeight()) / 2 ? "full" : "half");
  };

  sheetHead.addEventListener("pointerup", endDrag);
  sheetHead.addEventListener("pointercancel", endDrag);

  window.addEventListener("resize", () => {
    syncMapChrome();
    if (mapInstance === map && sheetOpen) applyMode(sheetMode);
  });

  // Тап по пустому месту карты закрывает лист; тап по маркеру — открывает.
  map.on("click", (event) => {
    if (!sheetOpen) return;
    const layers = ["stops", "vehicles", "stop-vehicles"].filter((id) => map.getLayer(id));
    const hits = layers.length
      ? map.queryRenderedFeatures(event.point, { layers })
      : [];
    if (!hits.length) closeSheet();
  });

  let stops;
  try {
    stops = await loadMapStops();
  } catch (err) {
    showBanner("Не удалось загрузить остановки: " + err.message, "error");
    return;
  }
  if (gen !== routeGen || mapInstance !== map) return;

  const features = stops.map((st) => ({
    type: "Feature",
    geometry: { type: "Point", coordinates: [st.lng, st.lat] },
    properties: { id: st.id, name: st.name || "" },
  }));

  const onLoad = () => {
    if (mapInstance !== map) return;
    map.addSource("accuracy", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    map.addLayer({
      id: "accuracy-fill",
      type: "fill",
      source: "accuracy",
      paint: { "fill-color": "#1565c0", "fill-opacity": 0.12 },
    });
    map.addLayer({
      id: "accuracy-line",
      type: "line",
      source: "accuracy",
      paint: { "line-color": "#1565c0", "line-opacity": 0.45, "line-width": 1 },
    });
    map.addSource("stops", {
      type: "geojson",
      data: { type: "FeatureCollection", features: features },
    });
    map.addLayer({
      id: "stops",
      type: "circle",
      source: "stops",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 4, 16, 9],
        "circle-color": "#c8102e",
        "circle-opacity": 0.7,
        "circle-stroke-width": 1,
        "circle-stroke-color": "#ffffff",
      },
    });
    map.addSource("selected-stop", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    map.addLayer({
      id: "selected-stop",
      type: "circle",
      source: "selected-stop",
      paint: {
        "circle-radius": 11,
        "circle-color": "#ff8f00",
        "circle-opacity": 0.95,
        "circle-stroke-width": 3,
        "circle-stroke-color": "#ffffff",
      },
    });

    // Линия маршрута и его остановки (когда открыта карта маршрута).
    if (lineFeatures.length) {
      map.addSource("route-line", {
        type: "geojson",
        data: { type: "FeatureCollection", features: lineFeatures },
      });
      map.addLayer({
        id: "route-line-casing",
        type: "line",
        source: "route-line",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": 7, "line-opacity": 0.9 },
      });
      map.addLayer({
        id: "route-line",
        type: "line",
        source: "route-line",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#c8102e", "line-width": 4, "line-opacity": 0.95 },
      });
    }
    if (routeStopFeatures.length) {
      map.addSource("route-stops", {
        type: "geojson",
        data: { type: "FeatureCollection", features: routeStopFeatures },
      });
      map.addLayer({
        id: "route-stops",
        type: "circle",
        source: "route-stops",
        paint: {
          "circle-radius": 4,
          "circle-color": "#ffffff",
          "circle-stroke-width": 2,
          "circle-stroke-color": "#c8102e",
        },
      });
    }

    // Борта: стрелка поворачивается по направлению движения (dir).
    let arrow = null;
    try {
      arrow = vehicleArrowImage();
    } catch (err) {
      arrow = null; // без иконки останутся обычные точки
    }
    if (arrow) map.addImage("vehicle-arrow", arrow);
    map.addSource("vehicles", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    if (arrow) {
      map.addLayer({
        id: "vehicles",
        type: "symbol",
        source: "vehicles",
        layout: {
          "icon-image": "vehicle-arrow",
          "icon-rotate": ["coalesce", ["get", "dir"], 0],
          "icon-rotation-alignment": "map",
          "icon-size": 0.85,
          "icon-allow-overlap": true,
        },
      });
    } else {
      map.addLayer({
        id: "vehicles",
        type: "circle",
        source: "vehicles",
        paint: {
          "circle-radius": 7,
          "circle-color": "#1565c0",
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });
    }
    map.on("click", "vehicles", (event) => {
      const feature = event.features && event.features[0];
      if (!feature) return;
      openVehicle(feature.properties, feature.geometry.coordinates);
    });
    map.on("mousemove", "vehicles", () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", "vehicles", () => {
      map.getCanvas().style.cursor = "";
    });

    // Борта, подходящие к выбранной остановке (заполняется из её карточки).
    map.addSource("stop-vehicles", {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
    });
    if (arrow) {
      map.addLayer({
        id: "stop-vehicles",
        type: "symbol",
        source: "stop-vehicles",
        layout: {
          "icon-image": "vehicle-arrow",
          "icon-rotate": ["coalesce", ["get", "dir"], 0],
          "icon-rotation-alignment": "map",
          "icon-size": 0.8,
          "icon-allow-overlap": true,
        },
        paint: { "icon-opacity": 0.85 },
      });
    }
    map.on("click", "stop-vehicles", (event) => {
      const feature = event.features && event.features[0];
      if (!feature) return;
      openVehicle(feature.properties, feature.geometry.coordinates);
    });
    map.on("mousemove", "stop-vehicles", () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", "stop-vehicles", () => {
      map.getCanvas().style.cursor = "";
    });

    map.on("click", "stops", (event) => {
      const feature = event.features && event.features[0];
      if (!feature) return;
      const id = String(feature.properties.id);
      const st = stops.find((s) => String(s.id) === id);
      if (!st) return;
      selectedSource = map.getSource("selected-stop");
      openSheet(st, feature);
    });
    map.on("mousemove", "stops", () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", "stops", () => {
      map.getCanvas().style.cursor = "";
    });

    // Переход из карточки остановки: сразу показываем её на карте.
    if (focusId) {
      const target = stops.find((s) => String(s.id) === String(focusId));
      if (target) {
        const feature = features.find((f) => String(f.properties.id) === String(focusId));
        map.jumpTo({ center: [target.lng, target.lat], zoom: 15 });
        selectedSource = map.getSource("selected-stop");
        openSheet(target, feature || {
          type: "Feature",
          geometry: { type: "Point", coordinates: [target.lng, target.lat] },
          properties: { id: target.id, name: target.name || "" },
        });
      }
    }

    // «Моё место»: координаты считает браузер, на сервер они не уходят.
    const geoButton = el("button", {
      class: "map-geo", type: "button", title: "Моё место", "aria-label": "Моё место",
    });
    geoButton.innerHTML = GEO_ARROW_SVG;
    mapBox.appendChild(geoButton);
    let userMarker = null;
    geoButton.addEventListener("click", () => {
      if (!navigator.geolocation) {
        showBanner("Браузер не умеет определять местоположение.", "error");
        return;
      }
      geoButton.disabled = true;
      navigator.geolocation.getCurrentPosition((pos) => {
        geoButton.disabled = false;
        const coords = [pos.coords.longitude, pos.coords.latitude];
        if (userMarker) userMarker.remove();
        const dot = el("div", { class: "map-dot", title: "Моё место" });
        userMarker = new maplibregl.Marker({ element: dot, anchor: "center" })
          .setLngLat(coords)
          .addTo(map);
        const accuracy = map.getSource("accuracy");
        if (accuracy) {
          const radius = Number.isFinite(pos.coords.accuracy) ? pos.coords.accuracy : 0;
          accuracy.setData({
            type: "FeatureCollection",
            features: radius > 0 ? [accuracyCircle(coords[0], coords[1], radius)] : [],
          });
        }
        map.flyTo({ center: coords, zoom: Math.max(map.getZoom(), 15) });
        if (Number.isFinite(pos.coords.accuracy)) {
          showBanner("Точность определения: ±" + Math.round(pos.coords.accuracy) + " м", "info");
        }
      }, (err) => {
        geoButton.disabled = false;
        const why = err && err.code === 1
          ? "доступ к геолокации запрещён"
          : (err && err.message ? err.message : "неизвестная ошибка");
        showBanner("Не удалось определить местоположение: " + why, "error");
      }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
    });

    // Подсветка выбранной остановки — поверх линии маршрута и бортов.
    if (map.getLayer("selected-stop")) map.moveLayer("selected-stop");

    // Борта маршрута: сразу после слоёв и далее раз в 10 секунд.
    if (routeId) {
      loadRouteVehicles();
      vehicleTimer = setInterval(loadRouteVehicles, 10000);
    }
  };
  if (styleLoaded) onLoad();
  else map.once("load", onLoad);

  // Позиции бортов маршрута: опрашиваем только видимую вкладку.
  async function loadRouteVehicles() {
    if (!routeId || document.hidden) return;
    try {
      const data = await api("/api/route-vehicles?routeId=" + encodeURIComponent(routeId));
      if (gen !== routeGen || mapInstance !== map) return;
      const source = map.getSource("vehicles");
      if (!source) return;
      source.setData({
        type: "FeatureCollection",
        features: (data.vehicles || []).map((v) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [v.lng, v.lat] },
          properties: {
            deviceCode: v.deviceCode,
            dir: Number.isFinite(v.dir) ? v.dir : 0,
            routeNumber: v.routeNumber || "",
            gosNumber: v.gosNumber || "",
            speed: v.speed || 0,
          },
        })),
      });
    } catch (err) {
      // Портал недоступен — линия маршрута и остановки остаются на месте.
    }
  }

  applyPendingScroll();
}

/* ---------- router ---------- */

function stopRefresh() {
  if (refreshTimer !== null) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

function applyPendingScroll() {
  if (uiState.pendingScroll === null) return;
  const y = uiState.pendingScroll;
  uiState.pendingScroll = null;
  window.scrollTo(0, y);
}

// Какой пункт нижнего меню подсветить для текущего раздела.
const NAV_SECTION = {
  "": "map",
  map: "map",
  favorites: "favorites",
  routes: "routes",
  route: "routes",
  stops: "stops",
  stop: "stops",
};

function markNav(parts) {
  const active = NAV_SECTION[parts[0] || ""] || "";
  document.querySelectorAll(".topbar nav a[data-nav]").forEach((link) => {
    if (link.dataset.nav === active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

async function route() {
  stopRefresh();
  stopVehiclePolling();
  routeGen += 1;
  showBanner("");
  const hash = location.hash || "#/";
  if (uiState.current !== null) uiState.scroll[uiState.current] = window.scrollY;
  uiState.current = hash;
  uiState.pendingScroll = uiState.scroll[hash] || 0;
  const view = document.getElementById("view");
  if (mapInstance) {
    mapInstance.remove();
    mapInstance = null;
  }
  const parts = hash.replace(/^#\/?/, "").split("/");
  // Карта — во весь экран: у раздела нет отступов и прокрутки страницы.
  const isMap = parts[0] === "" || parts[0] === "map";
  view.classList.toggle("view-map", isMap);
  document.body.classList.toggle("map-mode", isMap);
  view.innerHTML = "";
  markNav(parts);
  if (parts[0] === "") return renderMap(view);
  if (parts[0] === "favorites") return renderFavorites(view);
  if (parts[0] === "routes") return renderRoutes(view);
  if (parts[0] === "route") return renderRoute(view, parts[1]);
  if (parts[0] === "stops") return renderStops(view);
  if (parts[0] === "map" && parts[1] === "route") return renderMap(view, null, parts[2]);
  if (parts[0] === "map") return renderMap(view, parts[1]);
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

// Простая установка как приложение (PWA). Работает только по HTTPS.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  });
}

boot();
