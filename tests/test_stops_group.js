"use strict";

// Тест логики группировки остановок по «платформам» (вариант B).
// Одинаковые названия объединяются только если остановки рядом (порог, метры);
// далёкие тёзки и остановки без координат остаются отдельными строками.
const assert = require("assert");
const path = require("path");

const sg = require(path.join(__dirname, "..", "static", "stops-group.js"));

// Площадь 1905 года: две платформы в ~30 м друг от друга.
const p513 = { id: 513, name: "Площадь 1905 года", lat: 56.837274, lng: 60.595761, routes: [] };
const p10511 = { id: 10511, name: "Площадь 1905 года", lat: 56.837477, lng: 60.595553, routes: [] };

// Тёзка в ~12 км (в жизни такие есть: «Сады», «10-й км»).
const farSameName = { id: 9001, name: "Площадь 1905 года", lat: 56.95, lng: 60.75, routes: [] };
const farSameName2 = { id: 9002, name: "Площадь 1905 года", lat: 57.05, lng: 60.85, routes: [] };

// --- близкие одноимённые объединяются в одну группу из двух платформ ---
{
  const groups = sg.groupStops([p513, p10511]);
  assert.strictEqual(groups.length, 1, "две близкие платформы → одна группа");
  assert.strictEqual(groups[0].platforms.length, 2, "в группе две платформы");
  assert.strictEqual(groups[0].name, "Площадь 1905 года", "имя группы");
}

// --- одиночная остановка → группа из одной платформы ---
{
  const groups = sg.groupStops([p513]);
  assert.strictEqual(groups.length, 1, "одна остановка → одна группа");
  assert.strictEqual(groups[0].platforms.length, 1, "одна платформа");
}

// --- далёкие одноимённые НЕ объединяются ---
{
  const groups = sg.groupStops([farSameName, farSameName2]);
  assert.strictEqual(groups.length, 2, "далёкие тёзки → две группы");
}

// --- близкие, но с разными именами НЕ объединяются ---
{
  const a = { id: 1, name: "Академика Ландау", lat: 56.837, lng: 60.595 };
  const b = { id: 2, name: "Горбольница № 7", lat: 56.8371, lng: 60.5951 };
  const groups = sg.groupStops([a, b]);
  assert.strictEqual(groups.length, 2, "разные имена не сливаются");
}

// --- без координат не объединяются ---
{
  const a = { id: 3, name: "Тёзка" };
  const b = { id: 4, name: "Тёзка" };
  const groups = sg.groupStops([a, b]);
  assert.strictEqual(groups.length, 2, "без координат не сливаются");
}

// --- близкие сливаются, далёкий едет отдельно; порядок входа сохраняется ---
{
  const groups = sg.groupStops([p513, farSameName, p10511]);
  assert.strictEqual(groups.length, 2, "одна слитая + одна отдельная");
  assert.strictEqual(groups[0].platforms.length, 2, "первая группа — две близкие платформы");
  assert.strictEqual(groups[1].platforms.length, 1, "вторая группа — дальний тёзка");
  assert.strictEqual(groups[1].platforms[0].id, 9001, "дальний сохранил свой id");
}

// --- порог настраивается: при крошечном пороге близкие тоже расходятся ---
{
  const groups = sg.groupStops([p513, p10511], 1);
  assert.strictEqual(groups.length, 2, "порог 1 м разъединяет платформы в ~30 м");
}

console.log("stops-group: OK");
