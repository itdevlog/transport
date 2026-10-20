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

/* ---------- forecast rendering ---------- */

function renderForecastList(container, forecasts, stop) {
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
    container.appendChild(renderDirGroup(where, arrivals, stop));
  });
}

function renderDirGroup(where, arrivals, stop) {
  const group = el("div", { class: "dir-group" });
  const head = el("div", { class: "dir-head" });
  const labelEl = el("span", { class: "dir-label" });
  const terminalEl = el("span", { class: "dir-terminal" });
  head.appendChild(labelEl);
  head.appendChild(terminalEl);

  function paint() {
    const custom = (stop && stop.dirs && stop.dirs[where]) || "";
    labelEl.textContent = custom || ("до " + where);
    terminalEl.textContent = custom ? "(до " + where + ")" : "";
  }

  if (stop) {
    const input = el("input", {
      class: "dir-input hidden", type: "text", maxlength: "60",
      placeholder: "подпись направления", "aria-label": "Подпись направления",
    });
    const pencil = el("button", { class: "note-edit", title: "Подпись направления",
      "aria-label": "Подпись направления" }, ["✎"]);
    head.appendChild(input);
    head.appendChild(pencil);
    let busy = false;

    function showLabel() {
      input.classList.add("hidden");
      pencil.classList.remove("hidden");
      labelEl.classList.remove("hidden");
      terminalEl.classList.remove("hidden");
      paint();
    }
    function startEdit(event) {
      event.preventDefault();
      event.stopPropagation();
      input.value = (stop.dirs && stop.dirs[where]) || "";
      input.classList.remove("hidden");
      pencil.classList.add("hidden");
      labelEl.classList.add("hidden");
      terminalEl.classList.add("hidden");
      input.focus();
      input.select();
    }
    async function commit() {
      if (busy) return;
      busy = true;
      try {
        const data = await api("/api/favorites", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "dir", kind: "stop", id: stop.id, where: where, label: input.value }),
        });
        favorites = { routes: data.routes || [], stops: data.stops || [] };
        const fresh = favorites.stops.find((x) => String(x.id) === String(stop.id));
        stop.dirs = (fresh && fresh.dirs) || {};
        showLabel();
      } catch (err) {
        showBanner("Подпись: " + err.message, "error");
        showLabel();
      } finally {
        busy = false;
      }
    }
    pencil.addEventListener("click", startEdit);
    input.addEventListener("click", (event) => event.stopPropagation());
    input.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter") { event.preventDefault(); commit(); input.blur(); }
      else if (event.key === "Escape") { event.preventDefault(); showLabel(); }
    });
    input.addEventListener("blur", () => { if (!input.classList.contains("hidden")) commit(); });
  }

  paint();
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
      renderForecastList(list, last, stop);
    } catch (err) {
      lastError = err;
      if (last) {
        renderForecastList(list, last, stop);
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

      const noteChip = el("span", { class: "stop-note" });
      if (s.note) noteChip.textContent = s.note; else noteChip.classList.add("hidden");
      const nameLine = el("div", { class: "name" });
      nameLine.appendChild(document.createTextNode(s.name || ("Остановка " + s.id)));
      nameLine.appendChild(noteChip);
      const typeDots = el("span", { class: "type-dots hidden" });
      nameLine.appendChild(typeDots);
      const chips = el("span", { class: "route-chips hidden" });
      nameLine.appendChild(chips);
      const noteInput = el("input", {
        class: "note-input hidden", type: "text", placeholder: "заметка",
        maxlength: "60", "aria-label": "Заметка к остановке",
      });
      const mainBox = el("div", { class: "main" });
      mainBox.appendChild(nameLine);
      mainBox.appendChild(noteInput);
      head.appendChild(mainBox);

      const pencil = el("button", {
        class: "note-edit", title: "Заметка", "aria-label": "Заметка",
      }, ["✎"]);
      head.appendChild(pencil);
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

      // Note editing starts ONLY from the pencil: the chip is a plain label,
      // so clicking the name/chip just toggles the row as usual.
      let noteBusy = false;
      function startEditNote(event) {
        if (event) { event.preventDefault(); event.stopPropagation(); }
        noteInput.value = s.note || "";
        noteInput.classList.remove("hidden");
        noteChip.classList.add("hidden");
        pencil.classList.add("hidden");
        noteInput.focus();
        noteInput.select();
      }
      function stopEditNote() {
        noteInput.classList.add("hidden");
        pencil.classList.remove("hidden");
        noteChip.classList.toggle("hidden", !s.note);
      }
      async function commitNote() {
        if (noteBusy) return;
        noteBusy = true;
        const value = noteInput.value.trim();
        try {
          const data = await api("/api/favorites", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "note", kind: "stop", id: s.id, note: value }),
          });
          favorites = { routes: data.routes || [], stops: data.stops || [] };
          const fresh = favorites.stops.find((x) => String(x.id) === sid);
          s.note = fresh ? fresh.note : undefined;
          noteChip.textContent = s.note || "";
          stopEditNote();
        } catch (err) {
          showBanner("Заметка: " + err.message, "error");
          stopEditNote();
        } finally {
          noteBusy = false;
        }
      }
      pencil.addEventListener("click", startEditNote);
      noteInput.addEventListener("click", (event) => event.stopPropagation());
      noteInput.addEventListener("keydown", (event) => {
        event.stopPropagation();
        if (event.key === "Enter") { event.preventDefault(); commitNote(); noteInput.blur(); }
        else if (event.key === "Escape") { event.preventDefault(); stopEditNote(); }
      });
      noteInput.addEventListener("blur", () => {
        if (!noteInput.classList.contains("hidden")) commitNote();
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
  const headerRow = el("div", { class: "row" });
  headerRow.appendChild(starButton("route", route.id, route.name || route.number));
  headerRow.appendChild(el("div", { class: "main" }, ["Добавить маршрут в избранное"]));
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
    box.appendChild(el("span", {
      class: "chip " + routeTypeClass(r.routeTypeId),
      title: routeTypeName(r.routeTypeId) + " " + (r.name || r.number || ""),
    }, [String(r.number || r.shortName || "?")]));
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
  const openBox = el("div", { class: "stop-open" });
  openBox.appendChild(el("a", { class: "link", href: "#/stop/" + encodeURIComponent(st.id) },
    ["Все прибытия и обновление →"]));
  details.appendChild(openBox);

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
  view.appendChild(el("h2", {}, [fav && fav.name ? fav.name : ("Остановка " + sid)]));
  const subLine = el("div", { class: "sub" }, ["Прибытие по всем маршрутам"]);
  const typeDots = el("span", { class: "type-dots hidden" });
  subLine.appendChild(document.createTextNode(" "));
  subLine.appendChild(typeDots);
  view.appendChild(subLine);
  const starRow = el("div", { class: "row" });
  starRow.appendChild(starButton("stop", Number(sid), fav && fav.name));
  starRow.appendChild(el("div", { class: "main" }, ["Добавить остановку в избранное"]));
  view.appendChild(starRow);

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

/* ---------- карта остановок ---------- */

// Город ограничиваем прямоугольником, чтобы карту нельзя было утащить
// в бесконечность: только Екатеринбург и ближние окраины.
const CITY_BOUNDS = [[56.68, 60.35], [56.99, 60.85]];
const MAP_CENTER = [56.8389, 60.6057];
const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

let leafletPromise = null;
let mapStopsCache = null;
let mapInstance = null;

// Leaflet грузим только при первом открытии карты: остальным разделам
// его ~150 КБ не нужны.
function loadLeaflet() {
  if (window.L) return Promise.resolve();
  if (leafletPromise) return leafletPromise;
  leafletPromise = new Promise((resolve, reject) => {
    document.head.appendChild(el("link", {
      rel: "stylesheet", href: "vendor/leaflet/leaflet.css",
    }));
    const script = el("script", { src: "vendor/leaflet/leaflet.js" });
    script.onload = () => resolve();
    script.onerror = () => {
      leafletPromise = null;
      reject(new Error("leaflet"));
    };
    document.head.appendChild(script);
  });
  return leafletPromise;
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

function mapStopCard(st) {
  const card = el("div", { class: "card map-stop" });
  const head = el("div", { class: "row stop-head" });
  const main = el("div", { class: "main" });
  main.appendChild(el("div", { class: "name" }, [st.name || ("Остановка " + st.id)]));
  head.appendChild(main);
  head.appendChild(starButton("stop", st.id, st.name));
  card.appendChild(head);
  const panel = forecastPanel(st.id, st);
  card.appendChild(panel.list);
  const open = el("div", { class: "stop-open" });
  open.appendChild(el("a", {
    class: "link",
    href: "#/stop/" + encodeURIComponent(st.id),
  }, ["Все прибытия и обновление →"]));
  card.appendChild(open);
  panel.tick();
  return card;
}

async function renderMap(view) {
  const gen = routeGen;
  view.appendChild(el("h2", {}, ["Карта остановок"]));
  view.appendChild(el("div", { class: "sub" }, ["Нажмите на остановку, чтобы увидеть прогноз."]));
  const mapBox = el("div", { class: "map" });
  view.appendChild(mapBox);
  const details = el("div");
  view.appendChild(details);

  try {
    await loadLeaflet();
  } catch (err) {
    mapBox.appendChild(el("div", { class: "empty" }, ["Не удалось загрузить библиотеку карты."]));
    return;
  }
  if (gen !== routeGen) return;

  const map = L.map(mapBox, {
    center: MAP_CENTER,
    zoom: 12,
    minZoom: 11,
    maxZoom: 17,
    maxBounds: CITY_BOUNDS,
    maxBoundsViscosity: 1.0,
  });
  mapInstance = map;
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 17,
    attribution: OSM_ATTRIBUTION,
  }).addTo(map);

  let stops;
  try {
    stops = await loadMapStops();
  } catch (err) {
    showBanner("Не удалось загрузить остановки: " + err.message, "error");
    return;
  }
  if (gen !== routeGen || mapInstance !== map) return;

  const renderer = L.canvas({ padding: 0.4 });
  const base = {
    radius: 5, weight: 1, color: "#c8102e", fillColor: "#c8102e", fillOpacity: 0.7,
  };
  const active = { color: "#1565c0", fillColor: "#1565c0", fillOpacity: 0.95, weight: 3 };
  let selected = null;
  stops.forEach((st) => {
    const marker = L.circleMarker([st.lat, st.lng], Object.assign({ renderer: renderer }, base));
    marker.bindTooltip(st.name || "", { direction: "top" });
    marker.on("click", () => {
      if (selected) selected.setStyle(base);
      selected = marker;
      marker.setStyle(active);
      details.innerHTML = "";
      details.appendChild(mapStopCard(st));
    });
    marker.addTo(map);
  });

  map.invalidateSize();
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

async function route() {
  stopRefresh();
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
  view.innerHTML = "";
  const parts = hash.replace(/^#\/?/, "").split("/");
  if (parts[0] === "") return renderFavorites(view);
  if (parts[0] === "routes") return renderRoutes(view);
  if (parts[0] === "route") return renderRoute(view, parts[1]);
  if (parts[0] === "stops") return renderStops(view);
  if (parts[0] === "map") return renderMap(view);
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
