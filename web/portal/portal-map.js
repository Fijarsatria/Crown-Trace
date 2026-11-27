"use strict";
(() => {
const $=id=>document.getElementById(id);
const COLORS={Healthy:"#a3e7bd",Small:"#9abbe9",Yellow:"#f1cb83",Dead:"#f08ca6",Unassigned:"#b7c5c1",
              palm_crown:"#b3ecc6","Kandidat pemeriksaan":"#f08ca6","Belum dinilai":"#a3e7bd"};
const REVIEW=new Set(["Yellow","Dead","Kandidat pemeriksaan"]);
function image(src){return new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error("Citra peta tidak dapat dimuat."));img.src=src;});}
function rings(geometry){if(geometry?.type==="Polygon")return [geometry.coordinates];if(geometry?.type==="MultiPolygon")return geometry.coordinates;return [];}
function flatten(geometry){return rings(geometry).flat(1);}
class CrownTraceMap {
 constructor(options){
  Object.assign(this,options);this.canvas=$("result-canvas");this.container=$("map-container");this.ctx=this.canvas.getContext("2d");
  this.trees=this.map.trees||[];this.blocks=this.map.blocks||[];this.width=this.report.width;this.height=this.report.height;
  if(!Number.isFinite(this.width)||!Number.isFinite(this.height)||this.width<=0||this.height<=0)throw new Error("Dimensi peta tidak valid.");
  this.layers=Object.fromEntries([...document.querySelectorAll("[data-layer]")].map(el=>[el.dataset.layer,el.checked]));
  this.view={zoom:1,x:0,y:0};this.fit=1;this.selected=null;this.cell=null;this.pending=false;this.destroyed=false;
  this.listeners=[];this.pointers=new Map();this.dragged=false;this.maxDensity=Math.max(1,...(this.report.grid||[]).map(g=>Number(g.trees_per_covered_ha)||0));
 }
 on(target,type,fn,options){target.addEventListener(type,fn,options);this.listeners.push(()=>target.removeEventListener(type,fn,options));}
 async init(){
  const [baseImage,maskImage]=await Promise.all([image(this.base+"/files/"+this.report.preview_file),
   this.report.mask_file?image(this.base+"/files/"+this.report.mask_file):Promise.resolve(null)]);
  if(this.destroyed)return;
  this.baseImage=baseImage;this.maskImage=maskImage;
  this.resize(true);
  this.observer=new ResizeObserver(()=>this.resize(false));this.observer.observe(this.container);
  this.on(this.canvas,"pointerdown",event=>this.down(event));
  this.on(this.canvas,"pointermove",event=>this.move(event));
  this.on(this.canvas,"pointerup",event=>this.up(event));
  this.on(this.canvas,"pointercancel",event=>{this.pointers.delete(event.pointerId);this.canvas.classList.remove("dragging");});
  this.on(this.canvas,"wheel",event=>{if(document.activeElement!==this.canvas&&!event.ctrlKey&&!event.metaKey)return;
   event.preventDefault();const pos=this.position(event);this.zoomAt(Math.exp(-event.deltaY*.0015),pos.x,pos.y);},{passive:false});
  this.on(this.canvas,"keydown",event=>{
   if(["+","=","-","ArrowUp","ArrowDown","ArrowLeft","ArrowRight","Home"].includes(event.key)){
    event.preventDefault();
    if(event.key==="+"||event.key==="=")this.action("zoomin");
    else if(event.key==="-")this.action("zoomout");
    else if(event.key==="Home")this.action("reset");
    else{this.view.x+=event.key==="ArrowLeft"?60:event.key==="ArrowRight"?-60:0;this.view.y+=event.key==="ArrowUp"?60:event.key==="ArrowDown"?-60:0;this.request();}
   }
  });
  this.on(document,"keydown",event=>{if(event.key==="Escape"&&$("map-surface")?.classList.contains("fullscreen"))this.action("fullscreen");});
  $("map-cover").hidden=true;this.updateLegend();this.request();
 }
 destroy(){this.destroyed=true;if(this.observer)this.observer.disconnect();for(const off of this.listeners)off();this.listeners=[];if(this.frame)cancelAnimationFrame(this.frame);}
 resize(reset=false){
  if(this.destroyed||!this.canvas.isConnected)return;
  const rect=this.container.getBoundingClientRect();const w=Math.max(1,rect.width),h=Math.max(1,rect.height);
  const oldWidth=this.cssWidth||w,oldHeight=this.cssHeight||h;
  const oldScale=this.fit*this.view.zoom;
  const center=reset?[this.width/2,this.height/2]:[(oldWidth/2-this.view.x)/oldScale,(oldHeight/2-this.view.y)/oldScale];
  this.cssWidth=w;this.cssHeight=h;this.dpr=Math.min(2,window.devicePixelRatio||1);this.canvas.width=Math.round(w*this.dpr);this.canvas.height=Math.round(h*this.dpr);
  this.fit=Math.min(w/this.width,h/this.height)*.94;
  if(reset)this.view.zoom=1;
  const scale=this.fit*this.view.zoom;
  this.view.x=w/2-center[0]*scale;this.view.y=h/2-center[1]*scale;this.request();
 }
 position(event){const b=this.canvas.getBoundingClientRect();return {x:event.clientX-b.left,y:event.clientY-b.top};}
 world(x,y){const scale=this.fit*this.view.zoom;return [(x-this.view.x)/scale,(y-this.view.y)/scale];}
 zoomAt(factor,x,y){
  const before=this.world(x,y);this.view.zoom=Math.max(.7,Math.min(35,this.view.zoom*factor));
  const scale=this.fit*this.view.zoom;this.view.x=x-before[0]*scale;this.view.y=y-before[1]*scale;this.request();
 }
 action(action){
  if(this.destroyed)return;
  if(action==="zoomin")this.zoomAt(1.5,this.cssWidth/2,this.cssHeight/2);
  if(action==="zoomout")this.zoomAt(1/1.5,this.cssWidth/2,this.cssHeight/2);
  if(action==="reset"){this.selected=null;this.cell=null;this.resize(true);this.showSelection();}
  if(action==="fullscreen"){const shell=$("map-surface");const open=shell.classList.toggle("fullscreen");shell.querySelector('[data-map-action="fullscreen"]')?.setAttribute("aria-pressed",String(open));setTimeout(()=>this.resize(false),50);}
 }
 setLayer(name,checked){this.layers[name]=checked;this.updateLegend();this.request();}
 down(ev){
  if(ev.pointerType==="mouse"&&ev.button!==0)return;
  this.canvas.focus({preventScroll:true});
  this.canvas.setPointerCapture(ev.pointerId);const pos=this.position(ev);
  this.pointers.set(ev.pointerId,{...pos,startX:pos.x,startY:pos.y});
  if(this.pointers.size===1){this.dragged=false;this.last=pos;}
  else this.dragged=true;
  this.canvas.classList.add("dragging");
 }
 move(ev){
  if(!this.pointers.has(ev.pointerId))return;
  const old=this.pointers.get(ev.pointerId);const current=this.position(ev);
  if(this.pointers.size===1){
   const dx=current.x-old.x,dy=current.y-old.y;
   if(Math.hypot(current.x-old.startX,current.y-old.startY)>4)this.dragged=true;
   if(this.dragged){this.view.x+=dx;this.view.y+=dy;this.request();}
  }else{
   const other=[...this.pointers.entries()].find(([id])=>id!==ev.pointerId)?.[1];
   if(other){const d0=Math.hypot(old.x-other.x,old.y-other.y);const d1=Math.hypot(current.x-other.x,current.y-other.y);
    if(d0>1){this.zoomAt(d1/d0,(current.x+other.x)/2,(current.y+other.y)/2);}}
  }
  this.pointers.set(ev.pointerId,{...current,startX:old.startX,startY:old.startY});
 }
 up(ev){
  const last=this.pointers.get(ev.pointerId);if(!last)return;this.pointers.delete(ev.pointerId);
  if(!this.pointers.size){this.canvas.classList.remove("dragging");if(!this.dragged)this.pick(last.x,last.y);}
 }
 request(){if(this.pending||this.destroyed)return;this.pending=true;this.frame=requestAnimationFrame(()=>{this.pending=false;this.draw();});}
 draw(){
  if(this.destroyed||!this.baseImage||!this.canvas.isConnected)return;
  const c=this.ctx,w=this.cssWidth,h=this.cssHeight,s=this.fit*this.view.zoom;
  c.setTransform(this.dpr,0,0,this.dpr,0,0);c.clearRect(0,0,w,h);c.fillStyle="#0b1720";c.fillRect(0,0,w,h);
  c.save();c.translate(this.view.x,this.view.y);c.scale(s,s);
  c.fillStyle="#13232b";c.fillRect(0,0,this.width,this.height);
  c.save();c.beginPath();c.rect(0,0,this.width,this.height);c.clip();
  if(this.layers.imagery)c.drawImage(this.baseImage,0,0,this.width,this.height);
  if(this.layers.density||this.layers.coverage){
   for(const cell of this.report.grid||[]){
    if(!cell.core)continue;
    const [x,y,r,b]=cell.core;
    if(this.layers.density&&cell.trees_per_covered_ha!==null){
     const q=Math.min(1,(cell.trees_per_covered_ha||0)/this.maxDensity);
     c.fillStyle=`rgba(${Math.round(28+170*q)},${Math.round(85+125*q)},${Math.round(105+40*q)},.43)`;
     c.fillRect(x,y,r-x,b-y);
    }
    if(this.layers.coverage&&(cell.coverage_pct??cell.coverage)<100){
     c.fillStyle="rgba(233,197,124,.22)";c.fillRect(x,y,r-x,b-y);
     c.strokeStyle="#e9c57c9c";c.lineWidth=1/s;c.setLineDash([5/s,4/s]);c.strokeRect(x,y,r-x,b-y);c.setLineDash([]);
    }else if(this.layers.density){c.strokeStyle="#d5e6d525";c.lineWidth=.7/s;c.strokeRect(x,y,r-x,b-y);}
   }
  }
  for(const tree of this.trees){
   if(!tree.center)continue;
   const [x,y]=tree.center;const xx=x*s+this.view.x,yy=y*s+this.view.y;
   if(xx<-80||xx>w+80||yy<-80||yy>h+80)continue;
   if(this.layers.crowns&&!this.maskImage&&Array.isArray(tree.rings)){
    c.fillStyle="rgba(140,232,183,.09)";c.strokeStyle="rgba(180,250,207,.78)";c.lineWidth=Math.max(1/s,.15);
    for(const ring of tree.rings){
     if(ring.length<2)continue;c.beginPath();ring.forEach(([a,b],i)=>i===0?c.moveTo(a,b):c.lineTo(a,b));c.closePath();c.fill();c.stroke();
    }
   }
   if(this.layers.boxes&&tree.bbox){const [a,b,r,t]=tree.bbox;c.strokeStyle=tree.review_candidate?"#f08ca6":"#99e3c7";c.lineWidth=1.6/s;c.strokeRect(a,b,r-a,t-b);}
  }
  if(this.layers.crowns&&this.maskImage)c.drawImage(this.maskImage,0,0,this.width,this.height);
  if(this.layers.blocks){
   c.strokeStyle="#bbdfea";c.lineWidth=1.7/s;c.setLineDash([7/s,5/s]);
   for(let i=0;i<this.blocks.length;i++){
    const lines=flatten(this.blocks[i]);const all=lines.flat();if(!all.length)continue;
    for(const ring of lines){c.beginPath();ring.forEach(([x,y],i)=>i===0?c.moveTo(x,y):c.lineTo(x,y));c.closePath();c.stroke();}
    const xs=all.map(p=>p[0]),ys=all.map(p=>p[1]);const x=(Math.min(...xs)+Math.max(...xs))/2,y=Math.min(...ys)+25/s;
    const label=this.report.blocks[i]?.block;if(label){c.font=`600 ${12/s}px DM`;c.textAlign="center";const size=c.measureText(label).width;c.fillStyle="#07161ddd";c.fillRect(x-size/2-6/s,y-10/s,size+12/s,19/s);c.fillStyle="#eef8f0";c.fillText(label,x,y+3/s);}
   }
   c.setLineDash([]);
  }
  for(const tree of this.trees){
   const [x,y]=tree.center||[-1e7,-1e7];const xx=x*s+this.view.x,yy=y*s+this.view.y;
   if(xx<-20||xx>w+20||yy<-20||yy>h+20)continue;
   const review=tree.review_candidate||REVIEW.has(tree.visual_label);
   if(this.layers.points||this.layers.review&&review){
    const radius=(this.report.kind==="inspection"?3.8:Math.min(3.6,1.7+this.view.zoom*.23))/s;
    c.fillStyle=review&&this.layers.review?"#f08ca6":COLORS[tree.visual_label]||"#b3ecc6";
    c.strokeStyle="#14271f";c.lineWidth=.65/s;c.beginPath();c.arc(x,y,radius,0,Math.PI*2);c.fill();c.stroke();
    if(review&&this.layers.review){c.strokeStyle="#f8bbce";c.lineWidth=1.25/s;c.beginPath();c.arc(x,y,6/s,0,Math.PI*2);c.stroke();}
   }
  }
  this.qualityDraw?.(c,s);
  if(this.selected){
   const [x,y]=this.selected.center;c.beginPath();c.arc(x,y,11/s,0,Math.PI*2);c.lineWidth=2/s;c.strokeStyle="#fff9dc";c.stroke();
   c.font=`600 ${12/s}px DM`;c.textAlign="left";c.fillStyle="#102723f2";const tw=c.measureText(this.selected.id).width;c.fillRect(x+13/s,y-9/s,tw+13/s,19/s);c.fillStyle="#f5ffe9";c.fillText(this.selected.id,x+19/s,y+4/s);
  }else if(this.cell){
   const [x,y,r,b]=this.cell.core;c.strokeStyle="#d7f3b5";c.lineWidth=2/s;c.strokeRect(x,y,r-x,b-y);
  }
  c.restore();c.restore();
  $("map-zoom").textContent=this.view.zoom.toFixed(this.view.zoom>=10?0:1)+"×";
 }
 pick(sx,sy){
  const [x,y]=this.world(sx,sy),tolerance=16/(this.fit*this.view.zoom);
  if(x<0||y<0||x>this.width||y>this.height){this.selected=null;this.cell=null;this.showSelection();this.request();return;}
  if(this.qualityClick?.(x,y))return;
  let nearest=null,best=tolerance;
  if(this.layers.points||this.layers.crowns||this.layers.boxes||this.layers.review){
   for(const tree of [...this.trees,...(this.qualityObjects?.()||[])]){
    if(!tree.center)continue;
    if(!this.layers.points&&!this.layers.crowns&&!this.layers.boxes&&!tree.review_candidate&&!REVIEW.has(tree.visual_label))continue;
    const d=Math.hypot(x-tree.center[0],y-tree.center[1]);
    if(d<best){best=d;nearest=tree;}
   }
  }
  this.selected=nearest;
  this.cell=nearest?null:(this.report.grid||[]).find(g=>g.core&&x>=g.core[0]&&x<g.core[2]&&y>=g.core[1]&&y<g.core[3]);
  this.showSelection();this.qualitySelected?.(this.selected);this.request();
 }
 showSelection(){
  const host=$("selected-object");if(!host)return;
  const e=this.escape,n=this.number;
  const detail=(name,value)=>`<div><dt>${e(name)}</dt><dd>${e(value)}</dd></div>`;
  if(this.selected){
   const t=this.selected;const p=t.center;const geo=t.coordinates;
   const rows=[["Kategori",this.visualLabels[t.visual_label]||t.visual_label||"Belum dinilai"],["Skor detektor",t.score==null?"—":Number(t.score).toFixed(3)]];
   if(t.review_score!=null)rows.push(["Skor penyaringan",Number(t.review_score).toFixed(4)]);
   if(t.review_tier)rows.push(["Prioritas",({high_priority:"Prioritas tinggi",needs_review:"Perlu review",not_flagged:"Tidak ditandai; bukan sehat"})[t.review_tier]||t.review_tier]);
   if(t.block)rows.push(["Blok",t.block]);
   if(t.crown_m2!=null)rows.push(["Area mahkota (estimasi)",n(t.crown_m2)+" m²"]);
   if(t.mask_px!=null)rows.push(["Area mask",n(t.mask_px)+" px"]);
   rows.push(["Pusat piksel",n(p[0])+", "+n(p[1])]);
   if(geo)rows.push(["Koordinat"+(this.report.example?" demo":""),Number(geo[0]).toFixed(5)+", "+Number(geo[1]).toFixed(5)]);
   host.innerHTML=`<strong>${e(t.id)}</strong><dl>${rows.map(([k,v])=>detail(k,v)).join("")}</dl><small>Skor dan kategori adalah keluaran model, bukan diagnosis.</small>`;
   $("map-coords").textContent=t.id+" · "+(geo?"Geografis"+(this.report.example?" demo":""):"Piksel");
  }else if(this.cell){
   const cell=this.cell;
   host.innerHTML=`<strong>Petak ${e(cell.tile)}</strong><dl>${detail("Pohon terdeteksi",n(cell.trees))}${detail("Cakupan",n(cell.coverage_pct??cell.coverage)+"%")}${detail("Pohon/ha tercakup",n(cell.trees_per_covered_ha))}${detail("Luas tercakup",n(cell.covered_ha)+" ha")}</dl>`;
   $("map-coords").textContent="Petak "+cell.tile;
  }else{host.textContent="Klik titik atau petak pada peta untuk melihat detail.";$("map-coords").textContent="Pilih titik untuk detail";}
 }
 seek(value){
  const text=String(value||"").trim().toLowerCase();
  if(!text){this.toast("Isi ID pohon yang ingin dicari.",true);return;}
  const t=[...this.trees,...(this.qualityObjects?.()||[])].find(t=>String(t.id).toLowerCase()===text);
  if(!t){this.toast("ID pohon tidak ditemukan pada hasil ini.",true);return;}
  this.selected=t;this.cell=null;this.layers.points=true;const toggle=document.querySelector('[data-layer="points"]');if(toggle)toggle.checked=true;
  this.view.zoom=Math.max(3.2,this.view.zoom);const s=this.fit*this.view.zoom;this.view.x=this.cssWidth/2-t.center[0]*s;this.view.y=this.cssHeight/2-t.center[1]*s;
  this.showSelection();this.qualitySelected?.(this.selected);this.updateLegend();this.request();
 }
 focusBlock(index){
  const geometry=this.blocks[index];if(!geometry)return;
  const p=flatten(geometry).flat();if(!p.length)return;
  const xs=p.map(v=>v[0]),ys=p.map(v=>v[1]);const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  const scale=Math.min(this.cssWidth/Math.max(1,x1-x0),this.cssHeight/Math.max(1,y1-y0))*.87;
  this.view.zoom=Math.min(35,Math.max(.7,scale/this.fit));
  const s=this.fit*this.view.zoom;this.view.x=this.cssWidth/2-(x0+x1)/2*s;this.view.y=this.cssHeight/2-(y0+y1)/2*s;
  this.request();
 }
 updateLegend(){
  const legends=[];
  if(this.layers.points){
   const categories=[...new Set(this.trees.map(t=>t.visual_label))];
   for(const label of categories){if(!label)continue;legends.push([this.visualLabels[label]||label,COLORS[label]||"#a3e7bd"]);}
  }
  if(this.layers.crowns)legends.push(["Mahkota","#b3ecc6"]);
  if(this.layers.blocks)legends.push(["Batas blok","#bbdfea"]);
  if(this.layers.density)legends.push(["Densitas rendah","#1c5569"],["Densitas tinggi","#c6d291"]);
  if(this.layers.coverage)legends.push(["Cakupan parsial","#e9c57c"]);
  if(this.layers.review)legends.push(["Kandidat / indikasi","#f08ca6"]);
  const box=$("map-legend");if(box)box.innerHTML=legends.map(([label,color])=>`<span class="legend-item"><i class="swatch" style="background:${color}"></i>${this.escape(label)}</span>`).join("");
 }
}
window.CrownTraceMap=CrownTraceMap;
})();

