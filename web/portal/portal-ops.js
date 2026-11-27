"use strict";
(() => {
const E = s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const N=v=>Number(v||0).toLocaleString("id-ID");
const LABEL={normal_visual:"Normal visual",stress_visual:"Indikasi stres visual",dead_visual:"Indikasi mati",uncertain:"Tidak dapat dinilai"};
const ACTION={review:"Tinjau / koreksi kondisi",exclude:"Keluarkan deteksi salah",add:"Tambahkan pohon terlewat",reshape:"Gambar ulang kontur",restore:"Kembalikan hasil awal"};
function qualityPanel(host,map,info,base,api,toast){
 let current=info,mode="review",target=null,center=null,vertices=[],busy=false,visible=true;
 const render=()=>{
  const s=current.summary;
  host.innerHTML=`<div class="panel-header"><div><span class="eyebrow">VERIFIKASI OBJEK</span><h2>Quality Control Center</h2><p>Layer hasil AI tidak ditimpa. ${current.released?"Koreksi rilis terkunci.":"Review objek secara terpisah sebelum rilis."}</p></div><span class="status-badge ${current.released?"ready":"quality_review"}">${current.released?"Rilis":"Draft"} · v${current.revision}</span></div><div class="panel-body qc-body"><div class="qc-metrics">${[["AI asli",s.model_count],["Hitungan terkoreksi",s.effective_count],["Sudah ditinjau",s.reviewed_count],["Menunggu",s.pending_count],["Dikeluarkan",s.excluded_count],["Ditambahkan",s.added_count]].map(([label,val])=>`<div><small>${E(label)}</small><strong>${N(val)}</strong></div>`).join("")}</div><p class="help">${N(s.corrected_contours)} kontur manual · ${N(s.field_observations)} catatan observasi lapangan. ${s.missing_geographic_coordinates?"Ada "+N(s.missing_geographic_coordinates)+" titik tanpa koordinat geografis; ekspor tidak boleh dianggap sensus GPS lengkap.":""} Ini bukan diagnosis atau ground truth independen.</p><div class="button-row"><label class="layer-control"><input id="qc-overlay-toggle" type="checkbox" ${visible?"checked":""}> Tampilkan koreksi pada peta</label><a class="btn secondary sm" href="${base}/quality/csv" download>Unduh CSV review</a><a class="btn secondary sm" href="${base}/quality/json" download>Unduh JSON review</a>${current.coordinate_mode==="geographic"?`<a class="btn secondary sm" href="${base}/quality/geojson" download>Unduh GeoJSON review</a>`:""}</div>${current.can_edit?`<form id="qc-edit" class="qc-form"><div class="form-grid"><label class="field"><span>Tindakan</span><select id="qc-action" name="action">${Object.entries(ACTION).map(([k,v])=>`<option value="${k}" ${mode===k?"selected":""}>${E(v)}</option>`).join("")}</select></label><label class="field"><span>Objek terpilih</span><input id="qc-target" readonly value="${E(target||"Klik pohon di peta")}"></label></div><p class="form-note" id="qc-hint"></p><div class="button-row" id="qc-draw-controls"><button class="btn secondary sm" id="qc-undo" type="button">Hapus titik terakhir</button><button class="btn ghost sm" id="qc-clear" type="button">Reset gambar</button><button class="btn ghost sm" id="qc-focus-map" type="button">Ke peta ↓</button></div><div class="form-grid" id="qc-label-fields"><label class="field"><span>Kondisi visual setelah ditinjau</span><select name="visual_condition">${Object.entries(LABEL).map(([k,v])=>`<option value="${k}" ${k==="uncertain"?"selected":""}>${E(v)}</option>`).join("")}</select></label><label class="field"><span>Sumber observasi</span><select name="review_source" id="qc-source"><option value="image_review">Review citra</option><option value="field_observation">Observasi lapangan (catatan tersedia)</option></select></label><label class="field qc-field-extra" hidden><span>Tanggal observasi</span><input type="date" name="observation_date"></label><label class="field qc-field-extra" hidden><span>Referensi catatan lapangan</span><input name="evidence_ref" maxlength="200" placeholder="ID catatan / formulir survei"></label></div><label class="field"><span>Alasan / catatan koreksi</span><textarea name="note" required minlength="3" maxlength="500" placeholder="Jelaskan dasar pemeriksaan. Jangan menebak kondisi lapangan."></textarea></label><div class="button-row"><button class="btn primary" type="submit">Simpan koreksi</button><button type="button" class="btn secondary" id="qc-reload">Segarkan</button></div></form>`:""}${current.can_edit?`<details class="subsection-line"><summary class="help">Prioritas review (heuristik, bukan skor akurasi)</summary><p class="help">Titik kandidat inspeksi, tepi citra dan skor detektor rendah. Klik ID untuk membuka objek.</p><div class="qc-changes">${current.review_queue.map(r=>`<button type="button" class="qc-item" data-qc-id="${E(r.id)}"><b>${E(r.id)}</b><small>${E(r.reasons.join(" · "))}</small></button>`).join("")||'<p class="help">Semua objek telah ditinjau.</p>'}</div></details>`:""}<details class="subsection-line"><summary class="help">Daftar objek yang dikoreksi (${current.changes.length})</summary><div class="qc-changes">${current.changes.length?current.changes.slice().reverse().map(r=>`<button type="button" class="qc-item" data-qc-id="${E(r.id)}"><b>${E(r.id)}</b><span>${E(r.excluded?"Dikeluarkan":LABEL[r.visual_condition]||r.review_state)}</span><small>${E(r.geometry_origin)} · ${E(r.note)}</small></button>`).join(""):'<p class="help">Belum ada perubahan per objek. Pemeriksaan umum QA tidak berarti semua pohon telah ditinjau.</p>'}</div></details></div>`;
  host.querySelector("#qc-overlay-toggle").onchange=ev=>{visible=ev.target.checked;map.request();};
  host.querySelectorAll("[data-qc-id]").forEach(b=>b.onclick=()=>{target=b.dataset.qcId;map.seek(target);updateHint();});
  if(!current.can_edit)return;
  const form=host.querySelector("#qc-edit");form.querySelector("#qc-action").onchange=ev=>{mode=ev.target.value;center=null;vertices=[];updateHint();map.request();};
  form.querySelector("#qc-source").onchange=ev=>host.querySelectorAll(".qc-field-extra").forEach(x=>{x.hidden=ev.target.value!=="field_observation";x.querySelector("input").required=!x.hidden;});
  form.querySelector("#qc-undo").onclick=()=>{if(mode==="add")center=null;else vertices.pop();updateHint();map.request();};
  form.querySelector("#qc-clear").onclick=()=>{center=null;vertices=[];updateHint();map.request();};
  form.querySelector("#qc-focus-map").onclick=()=>document.getElementById("map-surface").scrollIntoView({behavior:"smooth",block:"start"});
  form.querySelector("#qc-reload").onclick=async()=>{try{current=await api(base+"/quality");render();map.request();}catch(err){toast(err.message,true);}};
  form.onsubmit=async ev=>{
   ev.preventDefault();if(busy)return;
   const vals=Object.fromEntries(new FormData(form));
   if(mode==="add"&&!center){toast("Klik posisi pusat pohon baru.",true);return;}
   if(mode!=="add"&&!target){toast("Pilih ID pohon pada peta.",true);return;}
   if(mode==="reshape"&&vertices.length<3){toast("Gambar minimal tiga vertex di peta.",true);return;}
   const submit=form.querySelector('[type="submit"]');busy=true;submit.disabled=true;
   try{
    current=await api(base+"/quality",{method:"POST",body:{...vals,action:mode,revision:current.revision,tree_id:target,center,polygon_px:vertices}});
    center=null;vertices=[];render();map.request();toast("Koreksi tersimpan pada layer terpisah.");
   }catch(err){toast(err.message,true);}finally{busy=false;if(submit.isConnected)submit.disabled=false;}
  };
  updateHint();
 };
 function updateHint(){
  if(!current.can_edit)return;
  const form=host.querySelector("#qc-edit");if(!form)return;
  const label=form.querySelector("#qc-target");
  label.value=mode==="add"?(center?center.map(v=>v.toFixed(1)).join(", "):"Klik pusat pohon baru"):target||"Pilih objek pada peta";
  const help={review:"Klik titik pohon, lalu pilih kondisi visual dan sumber observasi.",exclude:"Pilih deteksi yang salah. Titik akan dicoret pada layer koreksi.",add:"Klik satu titik pada citra untuk pusat pohon terlewat. Koordinat geografis hanya diberikan jika affine asli tersedia.",reshape:"Pilih pohon terlebih dahulu pada mode review, lalu pilih gambar ulang dan klik vertex kontur secara berurutan; pusat harus berada di dalam kontur.",restore:"Pilih objek yang telah dikoreksi; kembalikan versi AI atau batalkan objek manual."};
  form.querySelector("#qc-hint").textContent=help[mode]+(mode==="reshape"?" Vertex: "+vertices.length:"");
  form.querySelector("#qc-label-fields").hidden=["exclude","restore"].includes(mode);
  form.querySelector("#qc-draw-controls").hidden=!["add","reshape"].includes(mode);
 }
 map.qualityClick=(x,y)=>{
  if(!current.can_edit)return false;
  if(mode==="add"){center=[x,y];updateHint();map.request();return true;}
  if(mode==="reshape"){if(!target){toast("Pilih objek pada mode review terlebih dahulu.",true);return true;}if(vertices.length<100)vertices.push([x,y]);updateHint();map.request();return true;}
  return false;
 };
 map.qualitySelected=tree=>{if(tree){target=tree.id;updateHint();}};
 map.qualityObjects=()=>current.changes.filter(r=>r.source==="manual").map(r=>({...r,visual_label:r.visual_condition||"Manual",score:null}));
 map.qualityDraw=(c,s)=>{
  if(!visible)return;
  for(const r of current.changes){
   const [x,y]=r.center;c.lineWidth=2/s;c.strokeStyle=r.excluded?"#ff8295":r.visual_condition==="stress_visual"||r.visual_condition==="dead_visual"?"#ffcc80":"#9bf3d2";
   if(r.polygon_px){c.beginPath();r.polygon_px.forEach(([a,b],i)=>i?c.lineTo(a,b):c.moveTo(a,b));c.closePath();c.fillStyle="#b6e9d226";c.fill();c.stroke();}
   c.beginPath();
   if(r.excluded){c.moveTo(x-6/s,y-6/s);c.lineTo(x+6/s,y+6/s);c.moveTo(x+6/s,y-6/s);c.lineTo(x-6/s,y+6/s);}
   else c.arc(x,y,6/s,0,Math.PI*2);
   c.stroke();
  }
  if(center){c.strokeStyle="#fff2ae";c.lineWidth=2/s;c.beginPath();c.arc(...center,8/s,0,Math.PI*2);c.stroke();}
  if(vertices.length){c.strokeStyle="#fff2ae";c.lineWidth=2/s;c.beginPath();vertices.forEach(([a,b],i)=>i?c.lineTo(a,b):c.moveTo(a,b));if(vertices.length>2)c.closePath();c.stroke();for(const [a,b] of vertices){c.fillStyle="#fff2ae";c.beginPath();c.arc(a,b,3/s,0,Math.PI*2);c.fill();}}
 };
 render();map.request();
}
function saveDownload(name,data,type){
 const link=document.createElement("a"),url=URL.createObjectURL(new Blob([data],{type}));
 link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function monitoringGraphic(data){
 const coords=data.rows.flatMap(r=>[r.before_coords,r.after_coords]).filter(Boolean);
 if(!coords.length)return "";
 const xmin=coords.reduce((a,p)=>Math.min(a,p[0]),Infinity),xmax=coords.reduce((a,p)=>Math.max(a,p[0]),-Infinity),ymin=coords.reduce((a,p)=>Math.min(a,p[1]),Infinity),ymax=coords.reduce((a,p)=>Math.max(a,p[1]),-Infinity);
 const aspect=Math.max(.1,Math.cos((ymin+ymax)*Math.PI/360)),dx=(xmax-xmin)*aspect,dy=ymax-ymin,scale=Math.min(800/Math.max(dx,1e-8),430/Math.max(dy,1e-8));
 const pos=p=>[50+(p[0]-xmin)*aspect*scale,465-(p[1]-ymin)*scale];
 const colors={changed:"#f4cf81",unchanged:"#9ce4bb",unverified:"#6c9fbc",unmatched_before:"#ee8fa4",unmatched_after:"#b5e9ab"};
 let marks="";
 const step=Math.max(1,Math.ceil(data.rows.length/4000));
 for(const r of data.rows.filter((_,i)=>i%step===0)){
  const p=r.after_coords||r.before_coords;if(!p)continue;const [x,y]=pos(p);
  marks+=`<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="2.5" fill="${colors[r.status]||"#b2c3bd"}" opacity=".9"/>`;
 }
 return `<div class="ops-spatial"><h3>Layer perubahan antar-survei</h3><p class="help">Visualisasi relatif dari koordinat hasil; bukan peta dasar atau bukti posisi lapangan. Maksimal sekitar 4.000 titik visual, semua tersedia pada ekspor.</p><svg viewBox="0 0 900 500" role="img" aria-label="Peta relatif titik cocok dan tidak cocok">${marks}</svg><div class="button-row">${[["changed","Perubahan visual"],["unchanged","Tetap"],["unverified","Belum sebanding"],["unmatched_before","Tidak cocok awal"],["unmatched_after","Tidak cocok terbaru"]].map(([k,v])=>`<small><i class="swatch" style="background:${colors[k]}"></i> ${v}</small>`).join("")}</div></div>`;
}
function monitoring(host,api,toast){
 host.innerHTML='<section class="panel pad"><span class="spinner"></span> Memuat survei rilis…</section>';
 api("/api/monitoring/candidates").then(({surveys})=>{
  if(!host.isConnected)return;
  const opts=surveys.map(s=>`<option value="${E(s.order_id+"/"+s.job_id)}">${E(s.code)} · ${E(s.title)} · v${s.version}${s.example?" (demo)":""}</option>`).join("");
  host.innerHTML=`<section class="panel pad"><h2>Bandingkan dua survei</h2><p class="help">Gunakan hasil geografis yang sudah dirilis, dengan tanggal akuisisi berbeda. Toleransi pencocokan harus sesuai akurasi posisi. Hasil tidak cocok belum berarti pohon baru atau mati.</p>${surveys.length<2?'<div class="banner warning">Diperlukan setidaknya dua hasil survei geografis yang dapat diakses. Ajukan/selesaikan survei berikutnya dari blok yang sama.</div>':`<form id="monitor-form" class="ops-form"><div class="form-grid"><label class="field"><span>Survei awal</span><select name="before" required><option value="">Pilih hasil awal</option>${opts}</select></label><label class="field"><span>Survei terbaru</span><select name="after" required><option value="">Pilih hasil terbaru</option>${opts}</select></label><label class="field"><span>Tanggal pengambilan citra awal</span><input type="date" name="before_date" required></label><label class="field"><span>Tanggal pengambilan citra terbaru</span><input type="date" name="after_date" required></label></div><label class="field"><span>Toleransi pencocokan posisi (meter)</span><input name="tolerance_m" type="number" min="0.1" max="20" step="0.1" required placeholder="Tetapkan dari ketelitian GPS/GCP"></label><label class="check-row"><input type="checkbox" name="same_area" required> Saya telah memeriksa bahwa area, cakupan, tanggal, dan akurasi koordinat memang sebanding.</label><button type="submit" class="btn primary">Bandingkan survei</button></form>`}</section><section id="monitor-results" class="stack"></section>`;
  const form=host.querySelector("#monitor-form");
  if(!form)return;
  form.onsubmit=async e=>{
   e.preventDefault();const f=Object.fromEntries(new FormData(form)),[before_order,before_job]=f.before.split("/"),[after_order,after_job]=f.after.split("/");
   const button=form.querySelector("button");button.disabled=true;const output=host.querySelector("#monitor-results");output.innerHTML='<section class="panel pad"><span class="spinner"></span> Mencocokkan titik…</section>';
   try{
    const data=await api("/api/monitoring/compare",{method:"POST",body:{before_order,before_job,after_order,after_job,before_date:f.before_date,after_date:f.after_date,tolerance_m:Number(f.tolerance_m),same_area:f.same_area==="on"}});
    if(!host.isConnected)return;
    const x=data.summary;
    const status={changed:"Perubahan visual ditinjau",unchanged:"Visual tetap",unverified:"Kondisi belum sebanding",unmatched_before:"Tidak cocok (awal)",unmatched_after:"Tidak cocok (terbaru)"};
    const display=k=>LABEL[k]||"—";
    output.innerHTML=`<section class="panel pad"><h2>Hasil perbandingan antar-survei</h2><p class="help">${E(data.before.survey_date)} → ${E(data.after.survey_date)} · toleransi ${E(data.tolerance_m)} m</p><div class="ops-kpi">${[["Titik cocok",x.matched],["Tak cocok awal",x.unmatched_before],["Tak cocok terbaru",x.unmatched_after],["Perubahan visual",x.observed_changes]].map(([k,v])=>`<div class="metric-card"><small>${E(k)}</small><strong>${N(v)}</strong></div>`).join("")}</div><div class="banner warning"><span>${E(data.note)} ${N(x.comparable_reviewed)} pasangan dengan kategori visual sebanding. ${N(x.missing_coordinates_before+x.missing_coordinates_after)} titik tanpa koordinat valid tidak dicocokkan.</span></div><div class="button-row"><button type="button" class="btn secondary" id="monitor-json">Unduh JSON</button><button type="button" class="btn secondary" id="monitor-csv">Unduh CSV</button><button type="button" class="btn secondary" id="monitor-geojson">Unduh GeoJSON</button></div>${monitoringGraphic(data)}<div class="ops-table"><table class="data-table"><thead><tr><th>ID awal</th><th>ID terbaru</th><th>Jarak (m)</th><th>Visual awal</th><th>Visual terbaru</th><th>Status</th></tr></thead><tbody>${data.rows.slice(0,150).map(r=>`<tr><td>${E(r.before_id||"—")}</td><td>${E(r.after_id||"—")}</td><td>${r.distance_m==null?"—":E(r.distance_m)}</td><td>${E(display(r.before_condition))}</td><td>${E(display(r.after_condition))}</td><td>${E(status[r.status]||r.status)}</td></tr>`).join("")}</tbody></table></div><p class="help">Menampilkan maksimal 150 baris. Unduh seluruh hasil untuk pemeriksaan lanjutan.</p></section>`;
    output.querySelector("#monitor-geojson").onclick=()=>{
     const features=data.rows.flatMap(r=>{const coord=r.after_coords||r.before_coords;return coord?[{type:"Feature",properties:{before_id:r.before_id,after_id:r.after_id,status:r.status,before_condition:r.before_condition,after_condition:r.after_condition,distance_m:r.distance_m},geometry:{type:"Point",coordinates:coord}}]:[]});
     saveDownload("crowntrace-monitoring.geojson",JSON.stringify({type:"FeatureCollection",features,crowntrace:{example:data.example,note:data.note,before:data.before,after:data.after,tolerance_m:data.tolerance_m}},null,2),"application/geo+json");
    };
    output.querySelector("#monitor-json").onclick=()=>saveDownload("crowntrace-monitoring.json",JSON.stringify(data,null,2),"application/json");
    output.querySelector("#monitor-csv").onclick=()=>{
     const fields=["before_id","after_id","distance_m","before_condition","after_condition","status","before_lon","before_lat","after_lon","after_lat"];
     const escape=v=>'"'+String(v??"").replace(/"/g,'""')+'"';
     const rows=data.rows.map(r=>({...r,before_lon:r.before_coords?.[0],before_lat:r.before_coords?.[1],after_lon:r.after_coords?.[0],after_lat:r.after_coords?.[1]}));
     saveDownload("crowntrace-monitoring.csv","\uFEFF"+fields.join(",")+"\n"+rows.map(r=>fields.map(k=>escape(r[k])).join(",")).join("\n"),"text/csv;charset=utf-8");
    };
   }catch(err){output.innerHTML=`<div class="banner warning">${E(err.message)}</div>`;toast(err.message,true);}finally{button.disabled=false;}
  };
 }).catch(err=>{if(host.isConnected)host.textContent=err.message;});
}
function updates(host,api,toast){
 const render=({unread,items})=>{
  if(!host.isConnected)return;
  host.innerHTML=`<section class="panel pad"><div class="panel-header"><div><h2>Aktivitas pesanan</h2><p>${N(unread)} pembaruan belum dibaca. Hanya pesanan yang dapat Anda akses.</p></div><button type="button" class="btn secondary" id="updates-read" ${unread?"":"disabled"}>Tandai sudah dibaca</button></div><div class="ops-updates">${items.length?items.map(r=>`<a data-nav href="/app/order/${E(r.order_id)}" class="ops-update ${r.unread?"unread":""}"><div><b>${E(r.label)}</b><p>${E(r.code)} · ${E(r.title)}</p><small>${E(new Date(r.created_at).toLocaleString("id-ID",{timeZone:"Asia/Jakarta"}))} WIB</small></div>${r.unread?'<span class="status-badge ready">Baru</span>':""}</a>`).join(""):'<p class="help">Belum ada aktivitas.</p>'}</div></section>`;
  host.querySelector("#updates-read").onclick=async()=>{try{render(await api("/api/updates/read",{method:"POST",body:{}}));}catch(err){toast(err.message,true);}};
 };
 api("/api/updates").then(render).catch(err=>{if(host.isConnected)host.textContent=err.message;});
}
window.CrownTraceOps={qualityPanel,monitoring,updates};
})();
