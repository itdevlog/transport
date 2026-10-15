"use strict";

// Единственный источник правды по видам транспорта.
// routeTypeId портала: 1 — автобус, 2 — троллейбус, 4 — трамвай (типа 3 нет).
// Работает и в браузере (глобальный VehicleTypes), и в Node (module.exports),
// чтобы значение нельзя было «разъехать» между фронтендом и тестом.
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.VehicleTypes = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  var VEHICLE_TYPES = { 1: "Автобус", 2: "Троллейбус", 4: "Трамвай" };

  function routeTypeName(id) {
    return VEHICLE_TYPES[id] || "Другое";
  }

  function routeTypeClass(id) {
    return VEHICLE_TYPES[id] ? "type-" + id : "type-other";
  }

  // Опции для фильтра «вид транспорта»: [["1","Автобус"], ...].
  function typeOptions() {
    return Object.keys(VEHICLE_TYPES).map(function (id) {
      return [String(id), VEHICLE_TYPES[id]];
    });
  }

  return {
    VEHICLE_TYPES: VEHICLE_TYPES,
    routeTypeName: routeTypeName,
    routeTypeClass: routeTypeClass,
    typeOptions: typeOptions,
  };
});
