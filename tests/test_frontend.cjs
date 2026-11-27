"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attributes = new Map();
    this.dataset = {};
    this.handlers = {};
    this.style = {setProperty() {}};
    this.files = [];
  }
  setAttribute(key, value) { this.attributes.set(key, String(value)); }
  getAttribute(key) { return this.attributes.get(key); }
  toggleAttribute(key, show) { if (show) this.setAttribute(key, ""); else this.attributes.delete(key); }
  append(...items) { this.children.push(...items); }
  prepend(...items) { this.children.unshift(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(event, callback) { this.handlers[event] = callback; }
  querySelectorAll(selector) {
    return this.children.flatMap(item => [
      ...(selector === ".mark" ? (item.getAttribute("class") === "mark" ? [item] : [])
        : item.tagName === "input" && (selector === "input" || item.checked) ? [item] : []),
      ...item.querySelectorAll(selector),
    ]);
  }
  focus() {}
}

async function main() {
  const elements = new Map();
  const node = id => {
    if (!elements.has(id)) elements.set(id, new Element("div"));
    return elements.get(id);
  };
  const demo = {analysis_id: "demo", source: "demo", coordinate_mode: "geographic", crs: "EPSG:4326",
    footprint: [[116,-1],[117,-1],[117,-2],[116,-2]],
    source_image: {name: "Demo", width: 840, height: 620}, counts: {total: 2}, detections: [
      {id:"a", class:"Healthy", confidence:.95, bbox_px:[10,20,50,70], center_px:[30,45],
        location:{longitude:116.2,latitude:-1.6}},
      {id:"b", class:"Small", confidence:.56, bbox_px:[70,80,95,130], center_px:[82.5,105],
        location:{longitude:116.7,latitude:-1.9}},
    ]};
  const context = vm.createContext({document: {getElementById:node, createElement:tag => new Element(tag),
    createElementNS:(_, tag) => new Element(tag)}, fetch:async path => ({
      ok:true, json:async () => path === "/api/demo" ? demo : {model_ready:true, sample_ready:true, backend:"compact"},
    })});
  vm.runInContext(fs.readFileSync(new URL("../web/app.js", `file:///${__filename.replace(/\\/g, "/")}`), "utf8"), context);
  await new Promise(resolve => setImmediate(resolve));
  await node("demo").handlers.click();
  assert.equal(node("map-panel").hidden, false);
  assert.equal(node("map").attributes.has("hidden"), false);
  assert.equal(node("map").querySelectorAll(".mark").length, 2);
  assert.equal(node("counts").children[0].children[1].textContent, 2);
  node("score").value = "70";
  node("score").handlers.input();
  assert.equal(node("overlay").querySelectorAll(".mark").length, 1);
  assert.equal(node("counts").children[0].children[1].textContent, 2);
  assert.equal(node("visible-count").textContent, "1 dari 2 deteksi ditampilkan");
  node("map").querySelectorAll(".mark")[0].handlers.click();
  assert.match(node("selection").textContent, /a · Sehat · 95%/);
  assert.match(node("geojson").href, /exports\/geojson$/);
  node("zoom-in").handlers.click();
  assert.equal(node("zoom-reset").textContent, "125%");
  assert.equal(node("map").getAttribute("viewBox"), "80 46 640 368");
  node("reset-filters").handlers.click();
  assert.equal(node("visible-count").textContent, "2 dari 2 deteksi ditampilkan");
  node("tab-image").handlers.click();
  assert.equal(node("image-panel").hidden, false);
  assert.equal(node("map-panel").hidden, true);
  assert.equal(node("zoom-reset").textContent, "100%");
  node("image-file").files = [{name: "notes.txt", size: 5}];
  node("image-file").handlers.change();
  assert.equal(node("run").disabled, true);
  assert.match(node("file-error").textContent, /JPEG/);
  node("upload-zone").handlers.drop({preventDefault() {}, dataTransfer: {files: [{name: "drone.jpeg", size: 12}]}});
  assert.equal(node("run").disabled, false);
  assert.equal(node("file-caption").textContent, "drone.jpeg");
  assert.equal(node("file-error").textContent, "");
  console.log("Frontend state smoke test passed.");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
