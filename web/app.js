"use strict";

const palette = {
  Healthy: ["Sehat", "#67ba85"],
  Dead: ["Mati", "#e48276"],
  Yellow: ["Menguning", "#e4c15d"],
  Small: ["Kecil", "#90b6e2"],
};
const $ = id => document.getElementById(id);
const maxFileBytes = 45 * 1024 * 1024;
const scales = [1, 1.25, 1.5, 2, 2.5, 3];
const state = {result: null, view: "image", selected: null, file: null, model: false, sample: false, busy: false,
  zoom: {image: 0, map: 0}};

function text(tag, value) {
  const element = document.createElement(tag);
  element.textContent = value;
  return element;
}

function svg(tag, attr) {
  const shape = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attr)) shape.setAttribute(key, value);
  return shape;
}

function dot(color) {
  const element = document.createElement("span");
  element.className = "dot";
  element.style.setProperty("--swatch", color);
  element.setAttribute("aria-hidden", "true");
  return element;
}

function setNote(value, error = false) {
  $("notice").textContent = value;
  $("notice").dataset.error = String(error);
}

function syncControls() {
  $("run").disabled = state.busy || !state.model || !state.file;
  $("run").textContent = state.busy ? "Sedang memproses…" : "Hitung pohon";
  $("sample").disabled = state.busy || !state.model || !state.sample;
  $("demo").disabled = state.busy;
  $("image-file").disabled = state.busy;
  $("method").disabled = state.busy;
  $("viewer").setAttribute("aria-busy", String(state.busy));
  $("score").disabled = !state.result;
  $("reset-filters").disabled = !state.result;
  for (const input of $("classes").querySelectorAll("input")) input.disabled = !state.result;
}

async function request(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "Permintaan gagal.");
  return payload;
}

function getVisible() {
  const enabled = new Set([...$("classes").querySelectorAll("input:checked")].map(item => item.value));
  const threshold = Number($("score").value) / 100;
  return (state.result?.detections || []).filter(det => enabled.has(det.class) && det.confidence >= threshold);
}

function drawCounts() {
  const detections = state.result?.detections || [];
  const cards = Object.entries(palette).map(([key, [label, color]]) =>
    [label, detections.filter(det => det.class === key).length, color]);
  $("counts").replaceChildren();
  for (const [label, count, color] of [["Total hasil", detections.length], ...cards]) {
    const card = document.createElement("div");
    card.className = "count";
    const caption = text("span", label);
    if (color) caption.prepend(dot(color));
    card.append(caption, text("b", state.result ? count : "–"));
    $("counts").append(card);
  }
}

function activateMark(shape, det) {
  shape.setAttribute("class", "mark");
  shape.setAttribute("tabindex", "0");
  shape.setAttribute("role", "button");
  shape.setAttribute("aria-label", `${det.id}, ${palette[det.class][0]}`);
  const select = () => {
    state.selected = state.selected === det.id ? null : det.id;
    redraw();
    [...$(state.view === "map" ? "map" : "overlay").querySelectorAll(".mark")]
      .find(node => node.getAttribute("aria-label") === shape.getAttribute("aria-label"))?.focus();
  };
  shape.addEventListener("click", select);
  shape.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      select();
    }
  });
}

function drawPhoto(detections) {
  const overlay = $("overlay");
  overlay.replaceChildren();
  if (!state.result) return;
  const {width, height} = state.result.source_image;
  overlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
  $("photo-stage").style.setProperty("--fit-width", `min(100%, calc(clamp(365px, 56vh, 620px) * ${width / height}))`);
  $("photo-stage").style.setProperty("--photo-ratio", String(width / height));
  for (const det of detections) {
    const [x, y, right, bottom] = det.bbox_px;
    const selected = state.selected === det.id;
    const color = palette[det.class][1];
    const shape = svg("rect", {x, y, width: right - x, height: bottom - y, fill: color,
      "fill-opacity": selected ? ".35" : ".06", stroke: color, "stroke-width": selected ? 3 : 1.35});
    activateMark(shape, det);
    overlay.append(shape);
  }
}

function drawMap(detections) {
  const canvas = $("map");
  const bounds = state.result?.footprint;
  canvas.toggleAttribute("hidden", !bounds);
  $("map-empty").hidden = Boolean(bounds);
  canvas.replaceChildren();
  if (!bounds) return;
  const longitude = bounds.map(p => p[0]);
  const latitude = bounds.map(p => p[1]);
  const [left, right] = [Math.min(...longitude), Math.max(...longitude)];
  const [bottom, top] = [Math.min(...latitude), Math.max(...latitude)];
  const ratio = Math.cos((top + bottom) / 2 * Math.PI / 180);
  const scale = Math.min(625 / ((right - left) * ratio), 335 / (top - bottom));
  const project = (lon, lat) => [400 + (lon - (left + right) / 2) * ratio * scale,
    222 - (lat - (top + bottom) / 2) * scale];
  const corners = bounds.map(point => project(...point));
  canvas.append(svg("rect", {x: 50, y: 25, width: 700, height: 395, rx: 8, fill: "#f1f7ec", stroke: "#aebdac"}));
  for (let tick = 0; tick <= 4; tick++) {
    const lon = left + (right - left) * tick / 4;
    const lat = top - (top - bottom) * tick / 4;
    const [x, y] = project(lon, lat);
    canvas.append(svg("path", {d: `M${x} 26V419 M51 ${y}H749`, stroke: "#ccd8c8", fill: "none"}));
    if (tick === 0 || tick === 4) {
      const longitudeLabel = svg("text", {x, y: 439, "text-anchor": "middle", fill: "#3e584a", "font-size": 12});
      longitudeLabel.textContent = lon.toFixed(6) + "°";
      const latitudeLabel = svg("text", {x: 56, y: y - 5, fill: "#3e584a", "font-size": 12});
      latitudeLabel.textContent = lat.toFixed(6) + "°";
      canvas.append(longitudeLabel, latitudeLabel);
    }
  }
  const footprint = svg("polygon", {points: corners.map(point => point.join(",")).join(" "),
    fill: "#b9d7b8", "fill-opacity": ".45", stroke: "#438269", "stroke-width": "2"});
  canvas.append(footprint);
  const north = svg("text", {x: 726, y: 51, fill: "#284f3b", "font-size": 16, "text-anchor": "middle"});
  north.textContent = "↑ N";
  canvas.append(north);
  for (const det of detections.filter(entry => entry.location)) {
    const {longitude: lon, latitude: lat} = det.location;
    const [cx, cy] = project(lon, lat);
    const shape = svg("circle", {cx, cy, r: state.selected === det.id ? 7 : 4, fill: palette[det.class][1],
      stroke: "#204c3a", "stroke-width": state.selected === det.id ? 2 : 1});
    activateMark(shape, det);
    canvas.append(shape);
  }
}

function redraw() {
  const visible = getVisible();
  const selected = visible.find(det => det.id === state.selected);
  if (!selected) state.selected = null;
  $("score-label").value = $("score").value + "%";
  $("visible-count").textContent = state.result
    ? `${visible.length} dari ${state.result.detections.length} deteksi ditampilkan` : "";
  $("selection").textContent = selected
    ? `${selected.id} · ${palette[selected.class][0]} · ${Math.round(selected.confidence*100)}% · px ${selected.center_px.map(x => x.toFixed(1)).join(", ")}`
      + (selected.location ? ` · ${selected.location.longitude.toFixed(6)}, ${selected.location.latitude.toFixed(6)}` : "")
    : "Pilih kotak atau titik untuk melihat detail.";
  drawPhoto(visible);
  drawMap(visible);
}

function changeView(view) {
  state.view = view;
  for (const label of ["image", "map"]) {
    $(label + "-panel").hidden = label !== view;
    $("tab-" + label).setAttribute("aria-pressed", String(label === view));
  }
  $("map-caption").textContent = view === "map" && state.result?.footprint
    ? "WGS84 · batas foto dan posisi pohon" : "";
  updateZoom();
}

function updateZoom() {
  const index = state.zoom[state.view];
  const scale = scales[index];
  $("view-tools").hidden = !state.result || (state.view === "map" && !state.result.footprint);
  $("zoom-out").disabled = index === 0;
  $("zoom-in").disabled = index === scales.length - 1;
  $("zoom-reset").disabled = index === 0;
  $("zoom-reset").textContent = `${Math.round(scale * 100)}%`;
  $("photo-stage").style.setProperty("--zoom", String(scales[state.zoom.image]));
  const mapWidth = 800 / scales[state.zoom.map];
  const mapHeight = 460 / scales[state.zoom.map];
  $("map").setAttribute("viewBox", `${(800 - mapWidth) / 2} ${(460 - mapHeight) / 2} ${mapWidth} ${mapHeight}`);
}

function zoomPhotoToCenter() {
  const canvas = $("photo-canvas");
  canvas.scrollLeft = (canvas.scrollWidth - canvas.clientWidth) / 2;
  canvas.scrollTop = (canvas.scrollHeight - canvas.clientHeight) / 2;
}

function zoomBy(increment) {
  state.zoom[state.view] = Math.min(scales.length - 1, Math.max(0, state.zoom[state.view] + increment));
  updateZoom();
  if (state.view === "image") zoomPhotoToCenter();
}

function chooseFile(file) {
  const error = !file ? "" : !/\.(jpe?g|png|tiff?)$/i.test(file.name) ? "Pilih berkas JPEG, PNG, atau GeoTIFF."
    : file.size > maxFileBytes ? "Ukuran foto melebihi 45 MiB. Pilih berkas yang lebih kecil."
    : file.size === 0 ? "Berkas kosong. Pilih foto lain." : "";
  state.file = error ? null : (file || null);
  $("file-caption").textContent = state.file?.name || "Pilih atau seret foto";
  $("file-error").textContent = error;
  $("image-file").setAttribute("aria-invalid", String(Boolean(error)));
  $("upload-zone").dataset.invalid = String(Boolean(error));
  syncControls();
}

function showResult(result) {
  state.result = result;
  state.selected = null;
  state.zoom = {image: 0, map: 0};
  $("score").min = Math.round((result.threshold ?? 0) * 100);
  $("score").value = $("score").min;
  for (const item of $("classes").querySelectorAll("input")) item.checked = true;
  const endpoint = "/api/analyses/" + result.analysis_id;
  $("photo").src = endpoint + "/image";
  $("photo-canvas").hidden = false;
  $("photo-empty").hidden = true;
  $("view-title").textContent = result.source_image.name;
  const metadata = [`${result.source_image.width} × ${result.source_image.height} px`];
  if (result.elapsed_ms != null) metadata.push(`${Math.round(result.elapsed_ms)} ms`, `${result.calls} kali diperiksa`);
  if (result.crs) metadata.push(result.crs);
  if (result.method === "selective") metadata.push(result.calls === 2 ? "Diperiksa dua model" : "Satu model cukup");
  $("view-meta").textContent = metadata.join(" · ");
  $("csv").href = endpoint + "/exports/csv";
  $("geojson").href = endpoint + "/exports/geojson";
  $("csv").hidden = false;
  $("exports-empty").hidden = true;
  $("export-help").hidden = false;
  $("geojson").hidden = result.coordinate_mode !== "geographic";
  $("advanced-filters").hidden = false;
  const note = result.source === "demo" ? "Foto dan titik pada contoh peta ini dibuat untuk simulasi."
    : `${result.counts.total ? "" : "Tidak ada pohon yang lolos batas skor. "}${result.spatial_warning || ""}`;
  setNote(note.trim());
  changeView(result.source === "demo" ? "map" : "image");
  drawCounts();
  redraw();
}

async function run(url, options) {
  if (state.busy) return;
  state.busy = true;
  syncControls();
  setNote("Memproses foto…");
  try {
    showResult(await request(url, options));
  } catch (error) {
    setNote(error.message, true);
  } finally {
    state.busy = false;
    syncControls();
  }
}

for (const [key, [label, color]] of Object.entries(palette)) {
  const input = document.createElement("input");
  input.type = "checkbox";
  input.value = key;
  input.checked = true;
  input.addEventListener("change", redraw);
  const item = text("label", label);
  item.prepend(input, dot(color));
  $("classes").append(item);
}
for (const view of ["image", "map"]) $("tab-" + view).addEventListener("click", () => changeView(view));
$("score").addEventListener("input", redraw);
$("reset-filters").addEventListener("click", () => {
  $("score").value = $("score").min;
  for (const input of $("classes").querySelectorAll("input")) input.checked = true;
  redraw();
});
$("zoom-out").addEventListener("click", () => zoomBy(-1));
$("zoom-in").addEventListener("click", () => zoomBy(1));
$("zoom-reset").addEventListener("click", () => zoomBy(-state.zoom[state.view]));
$("demo").addEventListener("click", () => run("/api/demo"));
$("sample").addEventListener("click", () => run("/api/sample?method=" + $("method").value, {method: "POST"}));
$("image-file").addEventListener("change", () => chooseFile($("image-file").files[0]));
const uploadZone = $("upload-zone");
uploadZone.addEventListener("dragover", event => {
  if (!event.dataTransfer?.types.includes("Files")) return;
  event.preventDefault();
  if (!state.busy) uploadZone.dataset.drag = "true";
});
uploadZone.addEventListener("dragleave", event => {
  if (!uploadZone.contains(event.relatedTarget)) uploadZone.dataset.drag = "false";
});
uploadZone.addEventListener("drop", event => {
  event.preventDefault();
  uploadZone.dataset.drag = "false";
  if (state.busy || !event.dataTransfer.files.length) return;
  $("image-file").value = "";
  chooseFile(event.dataTransfer.files[0]);
});
$("analysis-form").addEventListener("submit", event => {
  event.preventDefault();
  const file = state.file;
  if (file) run(`/api/analyses?method=${$("method").value}&filename=${encodeURIComponent(file.name)}`,
    {method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: file});
});
drawCounts();
syncControls();
request("/api/health").then(info => {
  state.model = info.model_ready;
  state.sample = info.sample_ready;
  $("method-review").hidden = $("method-review").disabled = !info.review_ready;
  $("model-state").textContent = info.model_ready ? "Model siap"
    : "Contoh peta tersedia";
  $("model-state").dataset.ready = String(info.model_ready);
  syncControls();
}).catch(error => {
  $("model-state").textContent = "Model belum tersambung";
  setNote(error.message, true);
});
