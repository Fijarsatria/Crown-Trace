"use strict";

const $ = id => document.getElementById(id);
const number = (value, digits = 0) => new Intl.NumberFormat("id-ID", {
  maximumFractionDigits: digits,
}).format(value);
const snapshot = window.CROWNTRACE_SNAPSHOT || null;
const canvas = $("canvas");
const context = canvas.getContext("2d");
const layers = ["base", "trees", "crowns", "review", "blocks", "grid", "density", "coverage", "photos"];
const views = {
  inventory: ["base", "trees", "crowns", "blocks", "coverage"],
  density: ["base", "blocks", "grid", "density", "coverage"],
  review: ["base", "review", "blocks", "coverage"],
};
const inspectionCandidate = tree => tree.visual_label === "Yellow" || tree.visual_label === "Dead";
const appearance = {
  Healthy: {name: "Normal visual", color: "#91e8ab"},
  Yellow: {name: "Menguning", color: "#ffd375"},
  Dead: {name: "Indikasi mati", color: "#ff9994"},
  Small: {name: "Tajuk kecil", color: "#a4ceff"},
  Unassigned: {name: "Tanpa label", color: "#d6dfda"},
};
const files = [
  ["crowntrace.gpkg", "GeoPackage"],
  ["blocks.csv", "Rekap CSV"],
  ["trees.geojson", "Titik"],
  ["crowns.geojson", "Mahkota"],
  ["blocks.geojson", "Blok"],
  ["grid.geojson", "Grid dan kepadatan"],
  ["coverage.geojson", "Cakupan parsial"],
  ["review.geojson", "Kandidat inspeksi"],
];
let health = {};
let busy = false;
let survey = null;
let detailTimer;
let detailRequest;
let frame;
let drag;

function status(message = "", percentage = null, error = false) {
  $("status-panel").hidden = !message;
  $("status-panel").dataset.error = String(error);
  $("status").textContent = message;
  $("progress").hidden = percentage === null;
  if (percentage !== null) $("progress").value = percentage;
}

function controls() {
  const file = $("raster").files[0];
  $("start").disabled = busy || !file || !health.ready;
  $("demo").disabled = busy || !(health.ready && health.demo);
  $("raster").disabled = busy;
  $("blocks").disabled = busy;
  $("segment").disabled = busy || !health.segmenter;
  $("segment").checked = Boolean(health.segmenter && $("segment").checked);
  $("file-name").textContent = file?.name || "Pilih GeoTIFF";
}

async function api(path, options) {
  const response = await fetch(path, options);
  const json = await response.json();
  if (!response.ok) throw new Error(json.detail || "Permintaan gagal.");
  return json;
}

function post(path, data) {
  return api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
}

function upload(file) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/surveys?filename=" + encodeURIComponent(file.name));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.responseType = "json";
    xhr.upload.onprogress = event => {
      if (event.lengthComputable) status("Mengunggah GeoTIFF…", 100 * event.loaded / event.total);
    };
    xhr.onerror = () => reject(new Error("Unggahan terputus. Coba lagi."));
    xhr.onload = () => xhr.status === 201 ? resolve(xhr.response)
      : reject(new Error(xhr.response?.detail || "GeoTIFF gagal diunggah."));
    xhr.send(file);
  });
}

async function follow(initial) {
  const phases = {
    antrean: ["Menunggu giliran", 0, 0], persiapan: ["Membuka orthomosaic", 0, 5],
    deteksi: ["Mendeteksi pohon", 5, 67], mahkota: ["Menggambar mahkota", 72, 23],
    ekspor: ["Menyusun layer dan rekap", 95, 4],
  };
  const endpoint = "/api/surveys/" + initial.id;
  let job = initial;
  while (job.status !== "done") {
    if (job.status === "error") throw new Error(job.detail);
    if (job.status === "staged") throw new Error("Survei belum dimulai.");
    const [title, base, span] = phases[job.phase] || phases.persiapan;
    const message = job.total ? title + " · " + job.completed + "/" + job.total : title + "…";
    status(message, base + span * (job.total ? job.completed / job.total : 0));
    await new Promise(resolve => setTimeout(resolve, 450));
    job = await api(endpoint);
  }
  const [summary, map] = await Promise.all([api(endpoint + "/summary.json"), api(endpoint + "/map.json")]);
  await show(job, summary, map, endpoint + "/preview.png", endpoint);
  history.replaceState(null, "", "/survei?survei=" + job.id);
}

async function run(demo) {
  if (busy) return;
  busy = true;
  controls();
  status("Menyiapkan survei…", 0);
  try {
    if (demo) {
      await follow(await post("/api/surveys/demo", {segment: Boolean(health.segmenter)}));
    } else {
      const raster = $("raster").files[0];
      if (!raster || !/\.tiff?$/i.test(raster.name)) throw new Error("Pilih berkas .tif atau .tiff.");
      if (raster.size > health.max_upload_bytes) throw new Error("Ukuran GeoTIFF melebihi batas 12 GiB.");
      const boundary = $("blocks").files[0];
      if (boundary && boundary.size > 4 * 1024 * 1024) throw new Error("Batas blok maksimal 4 MiB.");
      let blocks = null;
      if (boundary) {
        try { blocks = JSON.parse((await boundary.text()).replace(/^\uFEFF/, "")); }
        catch { throw new Error("Batas blok harus berupa berkas GeoJSON yang valid."); }
        if (blocks?.type !== "FeatureCollection") throw new Error("Batas blok harus berupa GeoJSON FeatureCollection.");
      }
      const job = await upload(raster);
      await follow(await post("/api/surveys/" + job.id + "/start", {blocks, segment: $("segment").checked}));
    }
  } catch (error) {
    status(error.message, null, true);
  } finally {
    busy = false;
    controls();
  }
}

const loadImage = src => new Promise((resolve, reject) => {
  const img = new Image();
  img.onload = () => resolve(img);
  img.onerror = () => reject(new Error("Pratinjau peta tidak dapat dibuka."));
  img.src = src;
});

function appearanceControls(counts) {
  const keys = counts ? Object.keys(appearance).filter(key => key !== "Unassigned" || counts[key]) : [];
  const filter = $("appearance-filter");
  filter.replaceChildren(new Option("Semua kategori", ""), ...keys.map(key => new Option(appearance[key].name, key)));
  $("appearance-counts").replaceChildren(...keys.map(key => {
    const entry = document.createElement("div");
    const term = document.createElement("dt");
    const marker = document.createElement("i");
    marker.className = "marker tree";
    marker.style.backgroundColor = appearance[key].color;
    term.append(marker, appearance[key].name);
    const value = document.createElement("dd");
    value.textContent = number(counts[key] || 0);
    entry.append(term, value);
    return entry;
  }));
  $("appearance-panel").hidden = !keys.length;
  return keys;
}

function blockRows(blocks, keys) {
  const head = $("block-head");
  head.querySelectorAll("[data-appearance]").forEach(cell => cell.remove());
  for (const key of keys) {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.dataset.appearance = key;
    cell.textContent = appearance[key].name;
    head.append(cell);
  }
  $("block-rows").replaceChildren(...blocks.map(block => {
    const row = document.createElement("tr");
    const values = [
      block.block, number(block.trees), number(block.covered_ha, 4) + " / " + number(block.area_ha, 4),
      number(block.coverage_pct, 1) + "%", block.trees_per_covered_ha == null ? "—" : number(block.trees_per_covered_ha, 1),
      ...keys.map(key => number(block["visual_" + key.toLowerCase()] || 0)),
    ];
    values.forEach((value, i) => {
      const cell = document.createElement(i === 0 ? "th" : "td");
      if (i === 0) cell.scope = "row";
      cell.textContent = value;
      row.append(cell);
    });
    return row;
  }));
}

async function show(job, summary, map, preview, endpoint) {
  clearTimeout(detailTimer);
  detailRequest?.abort();
  const image = await loadImage(preview);
  const densityValues = summary.grid.map(cell => cell.trees_per_covered_ha).filter(Number.isFinite).sort((a, b) => a - b);
  const densityLimit = Math.max(1, Math.ceil(densityValues[Math.max(0, Math.ceil(.9 * densityValues.length) - 1)] || 0));
  survey = {summary, map, image, endpoint, zoom: 1, center: [summary.width / 2, summary.height / 2],
    densityLimit, detail: null, selected: null, selectedPhoto: null, relative: Boolean(job.relative)};
  $("map-title").textContent = job.name;
  $("map-meta").textContent = job.relative ? `${number(map.photos.length)} pandangan · Koordinat piksel` : job.demo
    ? "Citra MOPAD yang dirangkai ulang · Koordinat demonstrasi"
    : number(summary.width) + " × " + number(summary.height) + " piksel · " + summary.crs;
  const version = $("model-versions");
  version.hidden = !summary.detector;
  version.textContent = summary.detector
    ? ["Deteksi " + summary.detector, summary.segmenter ? "Segmentasi " + summary.segmenter : ""].filter(Boolean).join(" · ")
    : "";
  $("metric-trees").textContent = number(summary.points);
  $("metric-crowns").textContent = summary.segmenter ? number(summary.crowns) : "—";
  $("metric-area").textContent = number(summary.blocks.reduce((sum, block) => sum + block.covered_ha, 0), 4) + " ha";
  $("metric-tiles").textContent = number(summary.tile_count);
  $("recap-scope").textContent = summary.outside_blocks
    ? number(summary.outside_blocks) + " deteksi berada di luar batas blok yang diunggah."
    : "Kepadatan dihitung berdasarkan luas yang tercakup citra.";
  blockRows(summary.blocks, appearanceControls(summary.visual_counts));
  const availableFiles = (snapshot?.files || files).filter(([name]) => {
    if (snapshot) return Boolean(snapshot.downloads[name]);
    if (name === "crowns.geojson") return Boolean(summary.segmenter);
    if (name === "coverage.geojson") return Number.isInteger(summary.coverage_cells);
    if (name === "review.geojson") return Number.isInteger(summary.review_points);
    return true;
  });
  $("export-links").replaceChildren(...availableFiles
    .map(([name, label]) => {
      const link = document.createElement("a");
      link.href = endpoint ? endpoint + "/" + name : snapshot.downloads[name];
      link.download = name;
      link.textContent = label;
      return link;
    }));
  for (const id of ["layers", "exports", "tools", "map-footer", "canvas"]) $(id).hidden = false;
  for (const id of ["metrics", "recap"]) $(id).hidden = survey.relative;
  $("empty").hidden = true;
  const available = {base: true, trees: !survey.relative, crowns: !survey.relative,
    review: !survey.relative, blocks: !survey.relative, grid: summary.grid.length > 0,
    density: densityValues.length > 0, coverage: summary.grid.length > 0,
    photos: Boolean(map.photos?.length)};
  for (const name of layers) $("layer-" + name).closest("label").hidden = !available[name];
  $("layer-views").hidden = survey.relative;
  $("layer-views").querySelector('[data-view="density"]').disabled = !available.density;
  $("layer-crowns").disabled = !summary.segmenter;
  $("review-count").textContent = number(summary.review_points ?? map.trees.filter(inspectionCandidate).length);
  $("coverage-count").textContent = number(summary.coverage_cells ?? summary.grid.filter(cell => cell.coverage < 99.5).length);
  $("photo-count").textContent = number(map.photos?.length || 0);
  $("density-max").textContent = "≥ " + number(densityLimit);
  $("selection").textContent = survey.relative ? "Klik jejak foto untuk melihat nama filenya." : "Klik pohon atau petak untuk melihat detail.";
  status("");
  if (survey.relative) changeLayers();
  else applyView("inventory");
}

function getView() {
  const {width, height} = survey.summary;
  const viewWidth = canvas.clientWidth;
  const viewHeight = canvas.clientHeight;
  const scale = Math.min(viewWidth / width, viewHeight / height) * survey.zoom;
  const [cx, cy] = survey.center;
  return {w: viewWidth, h: viewHeight, scale, x: viewWidth / 2 - cx * scale, y: viewHeight / 2 - cy * scale};
}

function stroke(rings, view, line, fill) {
  context.beginPath();
  for (const ring of rings) {
    ring.forEach(([x, y], index) => {
      const args = [view.x + x * view.scale, view.y + y * view.scale];
      if (index === 0) context.moveTo(...args);
      else context.lineTo(...args);
    });
    context.closePath();
  }
  if (fill) {
    context.fillStyle = fill;
    context.fill("evenodd");
  }
  context.strokeStyle = line;
  context.stroke();
}

function visible(name) {
  const checkbox = $("layer-" + name);
  return checkbox.checked && !checkbox.disabled && !checkbox.closest("label").hidden;
}

function changeLayers() {
  $("density-legend").hidden = !visible("density");
  $("review-note").hidden = !visible("review");
  $("coverage-note").hidden = !visible("coverage");
  for (const button of $("layer-views").querySelectorAll("button")) {
    const active = layers.every(name => {
      const input = $("layer-" + name);
      return input.disabled || input.closest("label").hidden || input.checked === views[button.dataset.view].includes(name);
    });
    button.setAttribute("aria-pressed", String(active));
  }
  detailSoon();
  redraw();
}

function applyView(name) {
  for (const layer of layers) $("layer-" + layer).checked = views[name].includes(layer);
  $("appearance-filter").value = "";
  if (survey) survey.selected = null;
  changeLayers();
}

function densityColor(value, limit) {
  const style = [[104, 134, 231], [40, 185, 181], [243, 213, 104]];
  const position = 2 * Math.min(1, value / limit);
  const low = Math.min(1, Math.floor(position)), weight = position - low;
  const blend = style[low].map((color, i) => Math.round(color * (1 - weight) + style[low + 1][i] * weight));
  return `rgba(${blend.join(",")}, .44)`;
}

function paint() {
  if (!survey) return;
  const {summary, map, image, detail, selected, selectedPhoto, densityLimit} = survey;
  const view = getView();
  if (!view.scale) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const cw = Math.round(view.w * dpr), ch = Math.round(view.h * dpr);
  if (canvas.width !== cw || canvas.height !== ch) {
    canvas.width = cw;
    canvas.height = ch;
  }
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, view.w, view.h);
  const {x, y, scale} = view;
  const shown = Object.fromEntries(layers.map(name => [name, visible(name)]));
  if (shown.base) context.drawImage(image, x, y, summary.width * scale, summary.height * scale);
  if (shown.base && detail) {
    const [left, top, right, bottom] = detail.rect;
    context.drawImage(detail.image, x + left * scale, y + top * scale,
      (right - left) * scale, (bottom - top) * scale);
  }
  context.lineWidth = 1;
  for (const item of summary.grid) {
    const [left, top, right, bottom] = item.core;
    const rect = [x + left * scale, y + top * scale, (right - left) * scale, (bottom - top) * scale];
    if (shown.density && Number.isFinite(item.trees_per_covered_ha)) {
      context.fillStyle = densityColor(item.trees_per_covered_ha, densityLimit);
      context.fillRect(...rect);
    }
    if (shown.coverage && (item.missing_pixels != null ? item.missing_pixels > 0 : item.coverage < 99.5)) {
      context.strokeStyle = "rgba(255,174,105,.93)";
      context.setLineDash([4, 3]);
      context.strokeRect(...rect);
      context.setLineDash([]);
    }
    if (shown.grid) {
      context.strokeStyle = "rgba(211,249,218,.63)";
      context.strokeRect(...rect);
      if (rect[2] > 32 && rect[3] > 25) {
        context.font = "700 12px system-ui";
        context.fillStyle = "#f1ffe8";
        context.shadowColor = "#000";
        context.shadowBlur = 3;
        context.fillText(item.tile, rect[0] + 7, rect[1] + 15);
        context.shadowBlur = 0;
      }
    }
  }
  if (shown.blocks) {
    context.lineWidth = 1.6;
    for (const block of map.blocks) {
      const polygons = block.type === "Polygon" ? [block.coordinates] : block.coordinates;
      for (const rings of polygons) stroke(rings, view, "rgba(183,225,255,.93)");
    }
  }
  if (shown.photos) {
    for (const photo of map.photos) {
      const active = selectedPhoto === photo.name;
      context.lineWidth = active ? 2.3 : 1.1;
      stroke([photo.ring], view, active ? "#f8e48a" : "rgba(168,222,255,.82)", active ? "rgba(248,228,138,.20)" : null);
    }
  }
  if (!shown.trees && !shown.crowns && !shown.review) return;
  const dense = map.trees.length > 4000;
  const plotted = new Set();
  const filter = $("appearance-filter").value;
  for (const tree of map.trees) {
    if (filter && tree.visual_label !== filter) continue;
    const tx = x + tree.center[0] * scale, ty = y + tree.center[1] * scale;
    if (tx < -14 || ty < -14 || tx > view.w + 14 || ty > view.h + 14) continue;
    const active = selected === tree.id;
    const color = appearance[tree.visual_label]?.color;
    if (shown.review && inspectionCandidate(tree)) {
      context.beginPath();
      context.arc(tx, ty, active ? 7 : 4.8, 0, Math.PI * 2);
      context.strokeStyle = "#ffb5d3";
      context.lineWidth = 1.7;
      context.stroke();
    }
    if (shown.crowns && tree.rings && (scale >= .16 || active)) {
      context.lineWidth = active ? 2.2 : 1.05;
      stroke(tree.rings, view, active ? "#fff" : color || "rgba(255,230,132,.96)", "rgba(246,222,113,.12)");
    }
    if (shown.trees) {
      if (dense && !active) {
        const key = Math.floor(tx / 4) + "," + Math.floor(ty / 4);
        if (plotted.has(key)) continue;
        plotted.add(key);
      }
      context.beginPath();
      context.arc(tx, ty, active ? 5 : 2, 0, Math.PI * 2);
      context.fillStyle = active ? "#fff" : color || "#96f1b4";
      context.fill();
    }
  }
}

function redraw() {
  cancelAnimationFrame(frame);
  frame = requestAnimationFrame(paint);
}

function detailSoon() {
  clearTimeout(detailTimer);
  detailRequest?.abort();
  if (!survey?.endpoint || survey.zoom < 1.55 || !visible("base")) return;
  detailTimer = setTimeout(async () => {
    const rendered = survey;
    const view = getView();
    const {width, height} = survey.summary;
    const rect = [Math.max(0, Math.floor(-view.x / view.scale)), Math.max(0, Math.floor(-view.y / view.scale)),
      Math.min(width, Math.ceil((view.w - view.x) / view.scale)),
      Math.min(height, Math.ceil((view.h - view.y) / view.scale))];
    if (rect[2] - rect[0] > 4096 || rect[3] - rect[1] > 4096 || rect[2] <= rect[0] || rect[3] <= rect[1]) return;
    const query = new URLSearchParams(Object.fromEntries(["x0", "y0", "x1", "y1"].map((k, i) => [k, rect[i]])));
    const request = new AbortController();
    detailRequest = request;
    try {
      const response = await fetch(survey.endpoint + "/window?" + query, {signal: request.signal});
      if (!response.ok) return;
      const data = URL.createObjectURL(await response.blob());
      try {
        const image = await loadImage(data);
        if (survey === rendered && !request.signal.aborted) {
          survey.detail = {rect, image};
          redraw();
        }
      } finally {
        URL.revokeObjectURL(data);
      }
    } catch {
      // The overview remains available during a detail-read failure or a pan.
    }
  }, 290);
}

function changeZoom(factor, point = null) {
  if (!survey) return;
  const view = getView();
  const [sx, sy] = point || [view.w / 2, view.h / 2];
  const rasterX = (sx - view.x) / view.scale, rasterY = (sy - view.y) / view.scale;
  survey.zoom = Math.min(96, Math.max(1, survey.zoom * factor));
  const scaled = getView().scale;
  const {width, height} = survey.summary;
  survey.center = survey.zoom === 1 ? [width / 2, height / 2] :
    [rasterX - (sx - view.w / 2) / scaled, rasterY - (sy - view.h / 2) / scaled];
  constrain();
  if (survey.zoom === 1) survey.detail = null;
  redraw();
  detailSoon();
}

function constrain() {
  const {width, height} = survey.summary;
  survey.center = [Math.max(0, Math.min(width, survey.center[0])),
    Math.max(0, Math.min(height, survey.center[1]))];
}

function footprintContains(x, y, points) {
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const [ax, ay] = points[i], [bx, by] = points[j];
    if ((ay > y) !== (by > y) && x < ax + (bx - ax) * (y - ay) / (by - ay)) inside = !inside;
  }
  return inside;
}

function inspect(screen) {
  const view = getView();
  const x = (screen[0] - view.x) / view.scale, y = (screen[1] - view.y) / view.scale;
  if (survey.relative) {
    let closest = null, distance = Infinity;
    if (visible("photos")) {
      for (const photo of survey.map.photos) {
        if (!footprintContains(x, y, photo.ring)) continue;
        const here = Math.hypot(x - photo.center[0], y - photo.center[1]);
        if (here < distance) {
          closest = photo;
          distance = here;
        }
      }
    }
    survey.selectedPhoto = closest?.name || null;
    $("selection").textContent = closest ? closest.name + " · Posisi " + number(x) + ", " + number(y) + " px"
      : "Pilih jejak foto yang terlihat di peta.";
    redraw();
    return;
  }
  let closest = null;
  let distance = 10 / view.scale;
  const filter = $("appearance-filter").value;
  const trees = visible("trees"), crowns = visible("crowns"), review = visible("review");
  if (trees || crowns || review) {
    for (const tree of survey.map.trees) {
      if (filter && tree.visual_label !== filter) continue;
      if (!trees && !(crowns && tree.rings && view.scale >= .16) && !(review && inspectionCandidate(tree))) continue;
      const here = Math.hypot(x - tree.center[0], y - tree.center[1]);
      if (here < distance) {
        closest = tree;
        distance = here;
      }
    }
  }
  survey.selected = closest?.id || null;
  if (closest) {
    let detail = closest.id + " · " + (closest.block || "Di luar blok") + " · " + (closest.core_tile ?? closest.tile);
    if (appearance[closest.visual_label]) detail += " · " + appearance[closest.visual_label].name;
    if (closest.crown_m2 != null) detail += " · Mahkota " + number(closest.crown_m2, 2) + " m²";
    if (!snapshot && survey.endpoint && !$("map-meta").textContent.includes("demonstrasi")) {
      detail += " · " + closest.coordinates.slice().reverse().map(n => n.toFixed(6)).join(", ");
    }
    $("selection").textContent = detail;
  } else {
    const tile = survey.summary.grid.find(item => x >= item.core[0] && y >= item.core[1] && x < item.core[2] && y < item.core[3]);
    let detail = tile ? "Petak " + tile.tile + " · " + number(tile.coverage, 1) + "% tercakup"
      : "Pilih pohon atau petak pada area peta.";
    if (tile?.trees != null) detail += " · " + number(tile.trees) + " pohon terdeteksi";
    if (tile?.trees_per_covered_ha != null) detail += " · " + number(tile.trees_per_covered_ha, 1) + " pohon/ha tercakup";
    $("selection").textContent = detail;
  }
  redraw();
}

const screenPoint = event => {
  const rect = canvas.getBoundingClientRect();
  return [event.clientX - rect.left, event.clientY - rect.top];
};
canvas.addEventListener("pointerdown", event => {
  if (!survey || event.button !== 0 || drag) return;
  canvas.setPointerCapture(event.pointerId);
  drag = {id: event.pointerId, start: screenPoint(event), center: survey.center.slice(), moving: false};
});
canvas.addEventListener("pointermove", event => {
  if (!drag || drag.id !== event.pointerId) return;
  const [x, y] = screenPoint(event);
  const dx = x - drag.start[0], dy = y - drag.start[1];
  if (Math.hypot(dx, dy) > 4) drag.moving = true;
  if (drag.moving && survey.zoom > 1) {
    const {scale} = getView();
    survey.center = [drag.center[0] - dx / scale, drag.center[1] - dy / scale];
    constrain();
    redraw();
  }
});
canvas.addEventListener("pointerup", event => {
  if (!drag || drag.id !== event.pointerId) return;
  if (!drag.moving) inspect(screenPoint(event));
  else detailSoon();
  drag = null;
});
canvas.addEventListener("pointercancel", () => { drag = null; });
canvas.addEventListener("wheel", event => {
  if (!survey) return;
  event.preventDefault();
  changeZoom(event.deltaY < 0 ? 1.2 : 1 / 1.2, screenPoint(event));
}, {passive: false});
canvas.addEventListener("keydown", event => {
  if (!survey) return;
  const directions = {ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0]};
  if (event.key === "+" || event.key === "=") changeZoom(1.4);
  else if (event.key === "-") changeZoom(1 / 1.4);
  else if (event.key === "0") changeZoom(0);
  else if (event.key in directions) {
    const [x, y] = directions[event.key];
    const step = 45 / getView().scale;
    survey.center = [survey.center[0] + x * step, survey.center[1] + y * step];
    constrain();
    redraw();
    detailSoon();
  } else return;
  event.preventDefault();
});

$("fit").onclick = () => changeZoom(0);
$("zoom-in").onclick = () => changeZoom(1.5);
$("zoom-out").onclick = () => changeZoom(1 / 1.5);
$("demo").onclick = () => run(true);
$("raster").onchange = controls;
$("upload-form").onsubmit = event => { event.preventDefault(); run(false); };
for (const layer of layers) $("layer-" + layer).onchange = changeLayers;
for (const button of $("layer-views").querySelectorAll("button")) button.onclick = () => applyView(button.dataset.view);
$("appearance-filter").onchange = () => {
  if (!survey) return;
  survey.selected = null;
  $("selection").textContent = "Klik pohon atau petak untuk melihat detail.";
  redraw();
};
new ResizeObserver(redraw).observe($("viewport"));

async function init() {
  if (snapshot) {
    $("upload-form").hidden = true;
    $("demo").hidden = true;
    $("engine").textContent = "Pratinjau hasil";
    document.querySelector("nav").hidden = true;
    document.querySelector(".identity").href = "#main";
    document.querySelector(".page-head p").textContent = snapshot.job.relative
      ? "Jelajahi mosaik Palangka Raya dan jejak foto pembentuknya."
      : "Jelajahi titik, mahkota, kepadatan, dan rekap blok pada contoh MOPAD.";
    await show(snapshot.job, snapshot.summary, snapshot.map, snapshot.image, null);
    return;
  }
  health = await api("/api/surveys/health");
  $("engine").textContent = health.ready ? "Model lokal siap" : health.gdal ? "Bobot model belum siap" : "QGIS belum siap";
  $("engine").dataset.ready = String(health.ready);
  controls();
  const restored = new URLSearchParams(location.search).get("survei");
  if (restored && /^[a-f0-9]{32}$/.test(restored)) {
    busy = true;
    controls();
    try { await follow(await api("/api/surveys/" + restored)); }
    finally { busy = false; controls(); }
  }
}

init().catch(error => {
  $("engine").textContent = health.ready ? "Model lokal siap" : "Server belum tersedia";
  status(error.message, null, true);
});
