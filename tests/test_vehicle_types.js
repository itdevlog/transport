"use strict";

// Регрессионный тест на таблицу видов транспорта.
// Портал отдаёт routeTypeId: 1 — автобус, 2 — троллейбус, 4 — трамвай.
// Типа 3 не существует; раньше он был ошибочно сопоставлен трамваю,
// из-за чего трамваи показывались как «Другое» и пустовал фильтр.
const assert = require("assert");
const path = require("path");

const vt = require(path.join(__dirname, "..", "static", "vehicle-types.js"));

assert.strictEqual(vt.routeTypeName(1), "Автобус", "1 → Автобус");
assert.strictEqual(vt.routeTypeName(2), "Троллейбус", "2 → Троллейбус");
assert.strictEqual(vt.routeTypeName(4), "Трамвай", "4 → Трамвай");
assert.strictEqual(vt.routeTypeName(3), "Другое", "3 → Другое (типа нет)");
assert.strictEqual(vt.routeTypeName(99), "Другое", "неизвестный → Другое");

assert.strictEqual(vt.routeTypeClass(4), "type-4", "класс трамвая");
assert.strictEqual(vt.routeTypeClass(3), "type-other", "класс неизвестного");

assert.deepStrictEqual(
  vt.typeOptions(),
  [["1", "Автобус"], ["2", "Троллейбус"], ["4", "Трамвай"]],
  "опции фильтра включают трамвай со значением 4"
);

console.log("vehicle-types: OK");
