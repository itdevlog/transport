"use strict";

// Группировка остановок в «платформы» (вариант B): одинаковые названия
// объединяются только когда остановки физически рядом. Работает и в браузере
// (глобальный StopsGroup), и в Node (module.exports) — одна точка правды.
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.StopsGroup = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  const EARTH_RADIUS_M = 6371000;
  const DEFAULT_THRESHOLD_M = 250;

  function toRad(deg) {
    return (deg * Math.PI) / 180;
  }

  function haversineMeters(a, b) {
    const dLat = toRad(b.lat - a.lat);
    const dLng = toRad(b.lng - a.lng);
    const lat1 = toRad(a.lat);
    const lat2 = toRad(b.lat);
    const h =
      Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) * Math.sin(dLng / 2);
    return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  function hasCoords(s) {
    return s && typeof s.lat === "number" && typeof s.lng === "number";
  }

  // Возвращает массив { name, platforms: [...] } в порядке первого появления.
  // Остановка присоединяется к группе, если имя совпадает и она ближе порога
  // к первой платформе группы; без координат — всегда отдельная группа.
  function groupStops(stops, thresholdMeters) {
    const limit = typeof thresholdMeters === "number" ? thresholdMeters : DEFAULT_THRESHOLD_M;
    const groups = [];
    (stops || []).forEach((stop) => {
      const name = stop ? stop.name : undefined;
      let target = null;
      if (hasCoords(stop)) {
        for (const group of groups) {
          if (group.name !== name) continue;
          const rep = group.platforms[0];
          if (hasCoords(rep) && haversineMeters(rep, stop) <= limit) {
            target = group;
            break;
          }
        }
      }
      if (target) target.platforms.push(stop);
      else groups.push({ name: name, platforms: [stop] });
    });
    return groups;
  }

  return { groupStops, haversineMeters, DEFAULT_THRESHOLD_M };
});
