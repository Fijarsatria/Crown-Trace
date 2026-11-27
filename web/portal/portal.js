"use strict";
(() => {
const $=id=>document.getElementById(id);
const e=value=>String(value??"").replace(/[&<>"']/g,ch=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
const n=value=>value==null?"—":Number(value).toLocaleString("id-ID",{maximumFractionDigits:2});
const rupiah=value=>value==null?"—":"Rp "+Number(value).toLocaleString("id-ID");
const bytes=value=>value>=1048576?(value/1048576).toLocaleString("id-ID",{maximumFractionDigits:1})+" MB":Math.ceil(value/1024)+" KB";
const date=value=>{if(!value)return"—";const d=new Date(value.length===10?value+"T00:00:00+07:00":value);return isNaN(d)?"—":d.toLocaleDateString("id-ID",{day:"numeric",month:"short",year:"numeric",timeZone:"Asia/Jakarta"});};
const stamp=value=>{if(!value)return"—";const d=new Date(value);return date(value)+" · "+d.toLocaleTimeString("id-ID",{hour:"2-digit",minute:"2-digit",timeZone:"Asia/Jakarta"})+" WIB";};
const initials=name=>String(name||"CT").split(/\s+/).slice(0,2).map(s=>s[0]||"").join("").toUpperCase();
const ROLE={customer:"Pelanggan",admin:"Administrator",operator:"Operator"};
const ICONS={
 arrow:"<path d='M5 12h14m-6-6 6 6-6 6'/>",chevron:"<path d='m9 18 6-6-6-6'/>",left:"<path d='M19 12H5m6 6-6-6 6-6'/>",
 plus:"<path d='M12 5v14M5 12h14'/>",check:"<path d='m5 12 4 4L19 6'/>",checkcircle:"<circle cx='12' cy='12' r='9'/><path d='m8 12 3 3 5-6'/>",
 layers:"<path d='m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5'/>",
 map:"<path d='m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6Zm6-3v15m6-12v15'/>",
 grid:"<rect x='3' y='3' width='7' height='7' rx='1.5'/><rect x='14' y='3' width='7' height='7' rx='1.5'/><rect x='3' y='14' width='7' height='7' rx='1.5'/><rect x='14' y='14' width='7' height='7' rx='1.5'/>",
 tree:"<path d='M12 22v-10m0 2C7 13 4 10 4 6c4 0 7 2 8 5 1-3 4-5 8-5 0 4-3 7-8 8M12 10c-2-3-2-5 0-8 2 3 2 5 0 8Z'/>",
 scan:"<path d='M4 9V4h5m6 0h5v5m0 6v5h-5m-6 0H4v-5'/><circle cx='12' cy='12' r='4'/>",
 pin:"<path d='M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z'/><circle cx='12' cy='10' r='2.5'/>",
 shield:"<path d='m12 3 8 3v6c0 5-4 8-8 9-4-1-8-4-8-9V6l8-3Z'/><path d='m8 12 3 3 5-6'/>",
 spark:"<path d='m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z'/>",
 file:"<path d='M6 3h8l4 4v14H6V3Zm8 0v5h4M9 13h6m-6 4h6'/>",
 briefcase:"<rect x='3' y='7' width='18' height='14' rx='2'/><path d='M8 7V3h8v4M3 12h18m-11 0v3h4v-3'/>",
 users:"<circle cx='9' cy='8' r='3'/><path d='M3 20v-2a6 6 0 0 1 12 0v2m2-15a3 3 0 1 1 0 6m1 4a5 5 0 0 1 3 5'/>",
 user:"<circle cx='12' cy='8' r='4'/><path d='M4 21v-2a8 8 0 0 1 16 0v2'/>",
 logout:"<path d='M10 17l5-5-5-5m5 5H3m13-8h4v16h-4'/>",
 calendar:"<rect x='3' y='5' width='18' height='16' rx='2'/><path d='M7 3v4m10-4v4M3 11h18'/>",
 clock:"<circle cx='12' cy='12' r='9'/><path d='M12 7v5l3 2'/>",
 wallet:"<path d='M3 6h16a2 2 0 0 1 2 2v11H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Zm0 0 13-3v3m5 5h-5v4h5'/>",
 menu:"<path d='M4 6h16M4 12h16M4 18h16'/>",close:"<path d='m6 6 12 12M6 18 18 6'/>",eye:"<path d='M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Z'/><circle cx='12' cy='12' r='3'/>",
 eyeoff:"<path d='m3 3 18 18M5 7c-2 2-3 5-3 5s4 7 10 7c2 0 4-.5 6-2M9 5c1-.2 2-.3 3-.3 6 0 10 7.3 10 7.3s-1 2-3 4'/>",
 search:"<circle cx='10.5' cy='10.5' r='6.5'/><path d='m16 16 4 4'/>",
 upload:"<path d='M12 16V3m-5 5 5-5 5 5M3 16v5h18v-5'/>",
 download:"<path d='M12 3v13m-5-5 5 5 5-5M3 17v4h18v-4'/>",
 message:"<path d='M4 4h16v13H9l-5 4V4Z'/><path d='M8 9h8m-8 4h5'/>",
 info:"<circle cx='12' cy='12' r='9'/><path d='M12 11v5m0-8h.01'/>",
 alert:"<path d='m12 3 10 18H2L12 3Z'/><path d='M12 9v5m0 3h.01'/>",
 refresh:"<path d='M20 8a8 8 0 0 0-14-3L3 8m0-5v5h5m-4 8a8 8 0 0 0 14 3l3-3m0 5v-5h-5'/>",
 image:"<rect x='3' y='3' width='18' height='18' rx='2'/><circle cx='8' cy='8' r='2'/><path d='m3 16 6-5 4 4 3-3 5 5'/>",
 compass:"<path d='m12 2 6 18-6-4-6 4 6-18Z'/>",
 bar:"<path d='M4 20V10m5 10V4m5 16v-7m5 7V7M3 20h19'/>",
 list:"<path d='M8 6h12M8 12h12M8 18h12M3 6h.01M3 12h.01M3 18h.01'/>",
 settings:"<circle cx='12' cy='12' r='3'/><path d='M10 2h4l1 3 3 1 3 3-2 3 2 3-3 3-3 1-1 3h-4l-1-3-3-1-3-3 2-3-2-3 3-3 3-1 1-3Z'/>",
 minus:"<path d='M5 12h14'/>",expand:"<path d='M4 9V4h5m6 0h5v5m0 6v5h-5m-6 0H4v-5'/>",
 crosshair:"<circle cx='12' cy='12' r='7'/><path d='M12 2v5m0 10v5M2 12h5m10 0h5'/>",
 play:"<path d='m8 5 13 7-13 7V5Z'/>",lock:"<rect x='5' y='10' width='14' height='11' rx='2'/><path d='M8 10V7a4 4 0 0 1 8 0v3m-4 4v3'/>",
 fullscreen:"<path d='M4 9V4h5m6 0h5v5m0 6v5h-5m-6 0H4v-5'/>",dot:"<circle cx='12' cy='12' r='4'/>"
};
function icon(name,cls=""){return `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]||ICONS.info}</svg>`;}
const state={config:null,user:null,csrf:null,route:0,detail:null,orders:[],staff:[],busy:false,filter:"all",search:"",wizard:null,poll:null,map:null};
const toast=(message,error=false)=>{
  const region=$("toast");region.innerHTML=`<div class="toast ${error?"error":""}">${icon(error?"alert":"checkcircle")}<p>${e(message)}</p></div>`;
  clearTimeout(toast.timer);toast.timer=setTimeout(()=>region.replaceChildren(),4500);
};
function clearAsync(){clearTimeout(state.poll);state.poll=null;if(state.map){state.map.destroy();state.map=null;}}
function safeNext(value){return typeof value==="string"&&/^\/(?:app(?:\/[a-z0-9/?&=_.-]*)?|pesan(?:\?[a-z0-9&=_.-]*)?)$/i.test(value)?value:"/app";}
function navigate(path,replace=false){clearAsync();if(replace)history.replaceState({},"",path);else history.pushState({},"",path);renderRoute();}
function authHref(next="/app"){return "/masuk?next="+encodeURIComponent(safeNext(next))}
function orderHref(service=""){return "/pesan"+(service?"?service="+service:"")}
function detailHref(id){return "/app/order/"+id;}
function resultHref(o,jid){return detailHref(o.id)+"/result/"+jid;}
async function api(path,options={}){
 const method=options.method||"GET";const headers={"Accept":"application/json",...(options.body!==undefined?{"Content-Type":"application/json"}:{}),...options.headers};
 if(method!=="GET"&&state.csrf)headers["X-CSRF-Token"]=state.csrf;
 const res=await fetch(path,{method,headers,credentials:"same-origin",cache:"no-store",...(options.body===undefined?{}:{body:JSON.stringify(options.body)})});
 let body;try{body=await res.json();}catch{body={detail:"Respons server tidak dapat dibaca."}}
 if(!res.ok){const err=new Error(body.detail||"Permintaan gagal.");err.status=res.status;throw err;}
 return body;
}
function brand(small=false){return `<a class="brand ${small?"brand-small":""}" href="/" data-nav><img src="/assets/brand.svg" width="35" height="35" alt=""><span class="brand-name">CrownTrace<b>.</b></span></a>`;}
function linkBtn(label,href,style="primary",glyph="arrow",extra=""){return `<a data-nav class="btn ${style} ${extra}" href="${e(href)}">${e(label)}${glyph?icon(glyph):""}</a>`;}
function btn(label,action,style="primary",glyph="",extra=""){return `<button type="button" class="btn ${style} ${extra}" data-action="${action}">${glyph?icon(glyph):""}${e(label)}</button>`;}
function badge(status,name){return `<span class="status-badge ${e(status)}">${e(name||state.config?.status?.[status]||status)}</span>`;}
function panel(title,subtitle,body,action=""){return `<section class="panel"><div class="panel-header"><div><h2>${e(title)}</h2>${subtitle?`<p>${e(subtitle)}</p>`:""}</div>${action}</div><div class="panel-body">${body}</div></section>`;}
function field(label,name,type="text",value="",attrs="",help=""){return `<label class="field"><span>${e(label)}</span><input name="${e(name)}" type="${e(type)}" value="${e(value)}" ${attrs}>${help?`<small>${e(help)}</small>`:""}</label>`;}
function textarea(label,name,value="",attrs="",help=""){return `<label class="field"><span>${e(label)}</span><textarea name="${name}" ${attrs}>${e(value)}</textarea>${help?`<small>${e(help)}</small>`:""}</label>`;}
function check(label,name,checked=false){return `<label class="check-control"><input type="checkbox" name="${name}" ${checked?"checked":""}><span>${e(label)}</span></label>`;}
function selectField(label,name,options,value="",help=""){return `<label class="field"><span>${e(label)}</span><select name="${name}" required>${options.map(([key,label,disabled])=>`<option value="${e(key)}" ${key===value?"selected":""} ${disabled?"disabled":""}>${e(label)}</option>`).join("")}</select>${help?`<small>${e(help)}</small>`:""}</label>`;}
const errBox=()=>'<p class="form-error" role="alert" aria-live="polite"></p>';
function submitButton(label,glyph="arrow",style="primary"){return `<button type="submit" class="btn ${style}">${e(label)}${glyph?icon(glyph):""}</button>`;}
function empty(title,body,action="",glyph="layers"){return `<div class="empty-state"><div class="empty-icon">${icon(glyph)}</div><h3>${e(title)}</h3><p>${e(body)}</p>${action}</div>`;}
function loading(text="Memuat data"){return `<div class="loading-state" role="status"><span class="spinner"></span><strong>${e(text)}</strong><span>Mohon tunggu sebentar.</span></div>`;}
function showFormError(form,error){const target=form.querySelector(".form-error");if(target)target.textContent=error.message||String(error);toast(error.message||String(error),true);}
async function formWork(form,task){
 if(state.busy)return;state.busy=true;
 const button=form.querySelector('button[type="submit"]');const before=button?.innerHTML;
 if(button){button.disabled=true;button.innerHTML='<i class="spinner"></i> Memproses…';}
 const error=form.querySelector(".form-error");if(error)error.textContent="";
 try{await task();}catch(err){if(form.isConnected)showFormError(form,err);else toast(err.message,true);if(err.status===401){state.user=null;state.csrf=null;navigate(authHref(location.pathname+location.search),true);}}
 finally{state.busy=false;if(button?.isConnected){button.disabled=false;button.innerHTML=before;}}
}
function confirmAction(title,message,label="Lanjutkan"){
 return new Promise(resolve=>{
  const dialog=$("confirm-dialog");$("confirm-title").textContent=title;$("confirm-text").textContent=message;
  $("confirm-icon").innerHTML=icon("info");$("confirm-ok").textContent=label;dialog.setAttribute("aria-labelledby","confirm-title");dialog.setAttribute("aria-describedby","confirm-text");
  const close=value=>{dialog.close();resolve(value);};
  $("confirm-ok").onclick=()=>close(true);$("confirm-cancel").onclick=()=>close(false);
  dialog.oncancel=event=>{event.preventDefault();close(false);};
  if(!dialog.open)dialog.showModal();$("confirm-cancel").focus();
 });
}
const serviceMeta={
 inventory:{icon:"map",title:"Inventaris kebun",description:"Ketahui jumlah pohon dan persebarannya. Dapatkan titik, rekap blok dan kepadatan area yang tercakup.",tag:"Titik · Rekap blok",flow:"GeoTIFF atau tile RGB"},
 segmentation:{icon:"layers",title:"Pemetaan mahkota",description:"Lihat area setiap tajuk yang terdeteksi secara lebih rinci, dengan lapisan mahkota yang dapat ditinjau dan diekspor.",tag:"Mask · Poligon · GIS",flow:"GeoTIFF pilot atau tile RGB"},
 inspection:{icon:"scan",title:"Inspeksi kondisi",description:"Arahkan tim lapangan ke kandidat yang perlu diperiksa. Penyaringan visual mendukung prioritas, bukan diagnosis.",tag:"Tile RGB · Kandidat",flow:"Tile 640 × 640"}
};
function publicHeader(){
 const account=state.user?linkBtn("Ruang kerja","/app","secondary","", "sm"):linkBtn("Masuk","/masuk","ghost","", "sm");
 const order=state.user&&state.user.role!=="customer"?"/app":orderHref();
 return `<header class="public-header glass">${brand()}<nav aria-label="Navigasi publik"><a data-nav href="/#layanan">Layanan</a><a data-nav href="/#proses">Cara kerja</a><a data-nav href="/contoh">Contoh hasil</a><a data-nav href="/#riset">Riset</a></nav><div class="nav-actions">${account}${linkBtn("Ajukan pesanan",order,"primary","arrow","sm")}</div><button class="btn icon-btn secondary mobile-menu" data-action="public-menu" aria-label="Buka navigasi" aria-expanded="false">${icon("menu")}</button></header><nav class="mobile-public-nav glass" id="public-menu" hidden aria-label="Navigasi seluler"><a data-nav href="/#layanan">Layanan</a><a data-nav href="/#proses">Cara kerja</a><a data-nav href="/contoh">Contoh hasil</a><a data-nav href="/#riset">Riset</a>${account}${linkBtn("Ajukan pesanan",order,"primary","arrow")}</nav>`;
}
function publicFooter(){return `<footer class="public-footer"><div class="wrap"><div class="footer-inner"><div class="footer-logo">${brand(true)}<p>Layanan pemetaan dan analisis citra perkebunan sawit berbasis penelitian CrownTrace.</p></div><div class="footer-links"><a data-nav href="/#layanan">Layanan</a><a data-nav href="/#proses">Proses</a><a data-nav href="/contoh">Contoh peta</a><a data-nav href="/#faq">FAQ</a><a data-nav href="/masuk">Masuk</a></div></div><div class="footer-meta"><span>© 2026 CrownTrace · Dibangun untuk pemetaan yang dapat ditinjau.</span><span>${state.config?.demo?"Lingkungan demonstrasi · bukan transaksi nyata":"Pilot operasional lokal"}</span></div></div></footer>`;}
function atlas(){return `<div class="hero-visual"><div class="atlas-frame"><div class="atlas-toolbar"><div class="atlas-title"><i class="dot-live"></i> CrownTrace / Survey Atlas</div><span>CONTOH PENELITIAN</span></div><div class="atlas-scene" id="atlas-scene" data-show="crowns"><img class="atlas-base" src="/assets/atlas.webp" alt="Citra udara mosaik perkebunan MOPAD"><img class="layers" src="/assets/atlas-crowns.png" alt="Lapisan kontur mahkota hasil penelitian"><div class="atlas-chip">${icon("layers","sm")} Layer mahkota</div><div class="atlas-compass" aria-label="Orientasi tampilan citra">${icon("compass")}<span>Peta</span></div><div class="atlas-footer"><div class="sample-caption"><strong>MOPAD · Site 2</strong><small>Koordinat demonstrasi</small></div><div class="segmented" role="group" aria-label="Layer contoh"><button data-atlas="image" aria-pressed="false">${icon("image")} Citra</button><button data-atlas="crowns" aria-pressed="true">${icon("layers")} Mahkota</button></div></div></div><div class="atlas-statbar"><div><strong>1.698</strong><span>Deteksi seluruh contoh</span></div><div><strong>1.698</strong><span>Mask estimasi</span></div><div><strong>2</strong><span>Blok QA</span></div></div></div><div class="edge-tag glass"><span class="icon-wrap">${icon("scan")}</span><span><span class="edge-value">Setiap tajuk, dapat ditinjau</span><small>Contoh layer hasil model</small></span></div></div>`;}
function landing(){
 const services=Object.entries(serviceMeta).map(([key,m])=>`<article class="service-card reveal"><div class="service-icon">${icon(m.icon)}</div><h3>${m.title}</h3><p>${m.description}</p><div class="service-footer"><span>${m.tag}</span><a data-nav href="${orderHref(key)}">Pesan ${icon("arrow","sm")}</a></div></article>`).join("");
 const features=[["checkcircle","Tim yang mengerjakan","Anda menyampaikan kebutuhan dan data; kami mengatur pemrosesan, pemeriksaan, dan serah terima."],["layers","Hasil yang dapat ditinjau","Buka layer, rekap per blok, titik atau mask. Format hasil mengikuti jenis data yang diproses."],["shield","Hasil bukan klaim tanpa pemeriksaan","Model menghasilkan estimasi dan kandidat inspeksi; penilaian lapangan tetap diperlukan."]];
 const steps=[["Ajukan kebutuhan","Pilih layanan, kebun, dan data yang tersedia."],["Sepakati penawaran","Tim menyiapkan lingkup, biaya, jadwal dan cara pembayaran."],["Pemetaan & QA","Setelah pembayaran diperiksa, operator mengolah data dan meninjau draft."],["Terima hasil","Buka peta privat, ekspor berkas, lalu kirim koreksi bila perlu."]];
 const faq=[["Berapa biaya pemetaan?","Tidak menggunakan tarif tetap yang dipaksakan. Harga ditentukan melalui penawaran sesuai luas, kualitas input, pengambilan data, dan keluaran yang disepakati."],["Apakah saya harus menerbangkan drone sendiri?","Tidak harus. Anda dapat mengirim orthomosaic GeoTIFF RGB yang telah dipersiapkan, atau meminta tim meninjau kebutuhan pengambilan data. Foto drone mentah belum otomatis disusun menjadi orthomosaic oleh layanan ini."],["Bisakah mengetahui sawit sehat atau sakit?","CrownTrace mendukung penyaringan indikasi visual dan kandidat inspeksi pada sumber yang sesuai. Hasil bukan diagnosis penyakit atau konfirmasi sehat. Pemeriksaan lapangan diperlukan."],["Bagaimana menerima peta dan berkas?","Setelah QA dan rilis, hasil tersedia dalam akun pelanggan. Peta berkoordinat dapat menyertakan GeoPackage, GeoJSON dan CSV. Tile tanpa referensi lokasi menghasilkan koordinat piksel."],["Apakah pembayaran langsung di portal?","Portal menyediakan penawaran, konfirmasi pembayaran dan verifikasi manual. Mode demo tidak melakukan transaksi nyata; gateway pembayaran belum terhubung."]];
 return `<div class="landing page-enter">${publicHeader()}<main id="main-content"><section class="hero wrap"><div class="hero-copy"><span class="eyebrow">Pemetaan perkebunan terkelola</span><h1>Kenali kebun Anda, <em>sampai tiap tajuk.</em></h1><p class="hero-description">Ubah citra drone menjadi inventaris pohon, peta mahkota, dan prioritas inspeksi yang bisa Anda tinjau. Tim CrownTrace mengurus analisisnya, Anda fokus pada kebun.</p><div class="button-row">${linkBtn("Ajukan pemetaan",orderHref(),"primary","arrow","lg")}${linkBtn("Lihat contoh hasil","/contoh","secondary","map","lg")}</div><p class="hero-note">${icon("shield")} Penawaran sesuai kebutuhan · Hasil privat setelah pemeriksaan</p></div>${atlas()}</section><div class="hero-proof wrap"><span>${icon("map")} Pemetaan citra drone</span><span>${icon("layers")} Segmentasi per mahkota</span><span>${icon("scan")} Screening visual</span><span>${icon("file")} Ekspor GIS & CSV</span></div>
 <section class="section wrap" id="layanan"><div class="section-head"><div><span class="eyebrow">Layanan</span><h2>Mulai dari pertanyaan kebun Anda.</h2></div><p>Pilih hasil yang dibutuhkan. Lingkup, sumber citra, harga dan jadwal akan disepakati sebelum pengerjaan.</p></div><div class="service-grid">${services}</div></section>
 <section class="story wrap"><figure class="story-image reveal"><img src="/assets/field.webp" loading="lazy" alt="Citra udara tajuk sawit dari cohort Palangka Raya"><figcaption><div><b>Setiap kebun berbeda.</b><span>Citra nyata Palangka Raya · belum dianotasi</span></div>${icon("pin")}</figcaption></figure><div class="story-copy reveal"><span class="eyebrow">Peta yang berguna</span><h2>Data teknis, dijadikan informasi yang jelas.</h2><p>Anda tidak perlu menjalankan model sendiri. Dari kebutuhan awal sampai peta siap ditinjau, prosesnya ada dalam satu ruang kerja.</p><div class="feature-rows">${features.map(([glyph,title,desc])=>`<div class="feature-row">${icon(glyph)}<div><strong>${title}</strong><span>${desc}</span></div></div>`).join("")}</div></div></section>
 <section class="section flow-section" id="proses"><div class="wrap"><div class="section-head"><div><span class="eyebrow">Cara kerja</span><h2>Pesan. Kami kerjakan. Anda tinjau.</h2></div><p>Status setiap langkah terlihat di akun. Tidak ada hasil draft yang dirilis tanpa pemeriksaan tim.</p></div><div class="flow-grid">${steps.map(([title,desc],i)=>`<div class="flow-item reveal"><span class="flow-index">LANGKAH 0${i+1}</span><h3>${title}</h3><p>${desc}</p></div>`).join("")}</div></div></section>
 <section class="section wrap evidence" id="riset"><div class="section-head"><span class="eyebrow">Dibangun atas penelitian</span><h2>Terukur, dan jujur tentang batasnya.</h2><p>CrownTrace menguji model lokal pada dataset berlabel dan mengevaluasi ketepatan deteksi serta kontur. Seluruh pekerjaan tetap perlu verifikasi sumber, skala, dan kualitas lapangan.</p>${linkBtn("Jelajahi hasil contoh","/contoh","secondary","arrow")}</div><div class="evidence-block reveal"><div class="caption">${icon("bar")} Benchmark penelitian pilot</div><div class="evidence-grid"><div class="evidence-metric"><strong>93,82%</strong><span>Mask AP50 · Prompt Detail</span></div><div class="evidence-metric"><strong>37,10%</strong><span>Mask AP50:95 · Prompt Detail</span></div><div class="evidence-metric"><strong>90,48%</strong><span>F1 titik · QA inventaris MOPAD</span></div><div class="evidence-metric"><strong>1.704</strong><span>Referensi titik dalam QA</span></div></div><p>Mask dievaluasi pada holdout Trees yang digunakan ulang. Peta QA MOPAD memakai georeferensi demonstrasi. Angka tersebut bukan jaminan akurasi kebun baru.</p></div></section>
 <section class="section wrap faq-grid" id="faq"><div><span class="eyebrow">Pertanyaan umum</span><h2>Sebelum memesan.</h2><p>Temukan cara kerja layanan, kebutuhan data, dan penyerahan hasil.</p></div><div class="faq-list">${faq.map(([q,a])=>`<details><summary>${q}${icon("plus")}</summary><p>${a}</p></details>`).join("")}</div></section>
 <section class="wrap cta-band"><div><h2>Punya kebun yang ingin dipetakan?</h2><p>Mulai dari kebutuhan Anda. Tim akan meninjau data dan menyusun penawaran yang sesuai.</p></div><div class="button-row">${linkBtn("Buat permintaan",orderHref(),"primary","arrow","lg")}</div></section></main>${publicFooter()}</div>`;
}
function authPage(register=false,next="/app",order=false){
 const heading=register?"Buat akun pelanggan":(order?"Masuk untuk memesan":"Selamat datang kembali.");
 const pwd=`<label class="field"><span>Kata sandi</span><div class="password-wrap"><input name="password" type="password" autocomplete="${register?"new-password":"current-password"}" required minlength="${register?10:1}" maxlength="128" placeholder="${register?"Minimal 10 karakter":"Masukkan kata sandi"}"><button type="button" data-action="toggle-password" aria-label="Tampilkan kata sandi">${icon("eye")}</button></div></label>`;
 return `<main id="main-content" class="auth-page page-enter"><div class="auth-wrap"><div class="auth-main"><div class="auth-brandline">${brand()}<a class="home-link" data-nav href="/">${icon("left","sm")} Beranda</a></div><div class="auth-heading"><span class="eyebrow">Ruang kerja privat</span><h1>${heading}</h1><p>${register?"Ajukan pemetaan, pantau pengerjaan dan lihat hasil yang telah dirilis.":"Masuk ke akun Anda untuk melanjutkan pesanan dan pekerjaan."}</p></div>
 <form class="auth-form" data-form="${register?"register":"login"}" data-next="${e(next)}">${register?field("Nama lengkap","name","text","","required minlength=\"2\" maxlength=\"100\" autocomplete=\"name\" placeholder=\"Nama Anda\"")+field("Organisasi / perusahaan (opsional)","organization","text","","maxlength=\"120\" autocomplete=\"organization\" placeholder=\"Nama perkebunan atau organisasi\""):""}${field("Alamat email","email","email","","required autocomplete=\"email\" placeholder=\"nama@perusahaan.id\"")}${pwd}${register?field("Ulangi kata sandi","confirm_password","password","","required minlength=\"10\" autocomplete=\"new-password\""):""}${errBox()}<button type="submit" class="btn primary lg block">${register?"Buat akun":"Masuk"} ${icon("arrow")}</button></form>
 ${state.config?.demo&&!register?`<div class="demo-box"><div class="demo-label">${icon("info","sm")} Coba tiga peran demo</div><p>Simulasi lokal. Tidak ada uang yang dipindahkan atau pekerjaan kebun nyata.</p><div class="demo-buttons">${Object.entries(ROLE).map(([role,label])=>`<button type="button" class="btn secondary" data-demo-role="${role}" data-next="${e(next)}">${icon(role==="customer"?"user":role==="admin"?"shield":"layers")} ${label==="Administrator"?"Admin":label}</button>`).join("")}</div></div>`:""}
 <p class="form-bottom">${register?"Sudah punya akun?":"Belum punya akun pelanggan?"}<a data-nav href="${register?authHref(next):"/daftar?next="+encodeURIComponent(next)}">${register?"Masuk":"Daftar"}</a></p></div>
 <div class="auth-visual"><div class="auth-visual-inner"><span class="eyebrow">Survey atlas / MOPAD</span><h2>Dari citra udara, menjadi hasil yang bisa Anda gunakan.</h2><p>Kelola kebutuhan pemetaan dan tinjau hasil yang telah diperiksa dari satu ruang kerja.</p><div class="auth-result-glass glass"><div><strong>1.698</strong><span>Titik hasil penelitian QA</span></div><div>${icon("layers")}<span>Layer mahkota</span></div></div></div></div></div></main>`;
}
function modePill(){return `<span class="mode-pill ${state.config?.demo?"":"live"}"><i class="dot-live"></i>${state.config?.demo?"Workspace demo":"Lokal"}</span>`;}
function appShell(title,content,active="/app"){
 const role=state.user.role;
 const nav=[["/app","grid","Ringkasan"],["/app/orders","briefcase",role==="customer"?"Pesanan saya":role==="operator"?"Tugas saya":"Pesanan"],["/app/results","layers","Hasil pemetaan"],["/app/monitoring","map","Monitoring survei"],["/app/updates","bell","Pembaruan"]];
 if(role==="admin")nav.push(["/app/staff","users","Tim & operator"]);
 if(role==="customer")nav.push(["/app/new","plus","Buat pesanan"]);
 const navLink=([href,glyph,label])=>`<a class="side-link" href="${href}" data-nav ${active===href?'aria-current="page"':""}>${icon(glyph)}${label}</a>`;
 return `<div class="app-shell"><button type="button" class="sidebar-scrim" data-action="close-menu" aria-label="Tutup menu"></button><aside class="sidebar" id="sidebar">${brand(true)}<div class="sidebar-role">${icon(role==="customer"?"user":role==="admin"?"shield":"layers")}<span>Workspace ${ROLE[role]}</span></div><nav aria-label="Ruang kerja"><div class="nav-caption">Workspace</div>${nav.map(navLink).join("")}<div class="nav-caption" style="margin-top:17px">Pengaturan</div>${navLink(["/app/account","settings","Akun saya"])}<a class="side-link" data-nav href="/">${icon("map")} Situs layanan</a></nav><div class="sidebar-bottom"><button class="btn ghost block" data-action="logout">${icon("logout")} Keluar</button><div class="local-mode"><i class="dot-live"></i>${state.config?.demo?"Simulasi · data tersimpan lokal":"Operasional lokal · akses privat"}</div></div></aside>
 <div class="app-main"><header class="topbar"><div class="topbar-title"><button class="btn icon-btn ghost sidebar-toggle" data-action="sidebar-menu" aria-expanded="false" aria-label="Buka menu">${icon("menu")}</button><div class="crumb"><span>Workspace</span>${icon("chevron")}<b>${e(title)}</b></div></div><div class="topbar-right">${modePill()}<a data-nav href="/app/account" class="account-chip"><span class="avatar">${e(initials(state.user.name))}</span><span class="account-text"><strong>${e(state.user.name)}</strong><small>${ROLE[role]}</small></span></a></div></header><main class="workspace page-enter" id="main-content">${content}</main></div></div>`;
}
function mount(html){$("app").innerHTML=html;document.body.classList.remove("menu-open");}
function appMount(title,content,active){mount(appShell(title,content,active));}
function pageHeader(kicker,title,description,actions=""){return `<div class="workspace-header"><div><span class="eyebrow">${e(kicker)}</span><h1>${e(title)}</h1><p>${e(description)}</p></div><div class="button-row">${actions}</div></div>`;}


function metric(label,value,sub,glyph){return `<div class="metric-card"><div class="metric-head"><span>${e(label)}</span><span class="metric-icon">${icon(glyph)}</span></div><strong>${e(value)}</strong><small>${e(sub)}</small></div>`;}
const WORK=new Set(["paid","scheduled","processing","quality_review","revision_requested","processing_failed"]);
const ACTION_GROUPS={
 customer:[["Penawaran tersedia","Tinjau lingkup dan harga","file",o=>o.permissions.includes("accept_quote")||o.permissions.includes("decline_quote"),"quoted"],["Perlu konfirmasi pembayaran","Kirim referensi setelah deal","wallet",o=>o.status==="accepted","accepted"],["Hasil siap ditinjau","Baca dan unduh hasil rilis","layers",o=>o.status==="ready","ready"]],
 admin:[["Permintaan & penawaran","Susun lingkup dan estimasi biaya","file",o=>o.status==="submitted","submitted"],["Verifikasi pembayaran","Periksa konfirmasi secara manual","wallet",o=>o.status==="payment_review","payment_review"],["Siapkan pekerjaan","Validasi data dan jadwalkan tim","calendar",o=>["paid","revision_requested","processing_failed"].includes(o.status),"work"],["QA dan rilis","Periksa draft sebelum serah terima","shield",o=>o.status==="quality_review","quality_review"]],
 operator:[["Tugas siap dianalisis","Jalankan model sesuai profil","play",o=>o.status==="scheduled","scheduled"],["Pengerjaan aktif","Lihat progres job","layers",o=>o.status==="processing","processing"],["Perlu pemeriksaan","Periksa draft dan kirim koreksi","shield",o=>["quality_review","processing_failed","revision_requested"].includes(o.status),"work"]]
};
function overviewMetrics(orders){const r=state.user.role;const count=f=>orders.filter(f).length;
 if(r==="admin")return [
  ["Total pesanan",orders.length,"Seluruh permintaan","briefcase"],
  ["Perlu penawaran",count(o=>o.status==="submitted"),"Permintaan belum ditangani","file"],
  ["Verifikasi bayar",count(o=>o.status==="payment_review"),"Menunggu pemeriksaan","wallet"],
  ["Aktif & QA",count(o=>WORK.has(o.status)),"Pekerjaan belum dirilis","layers"]];
 if(r==="operator")return [
  ["Tugas saya",orders.length,"Pesanan yang ditugaskan","briefcase"],
  ["Siap dikerjakan",count(o=>o.status==="scheduled"),"Model dapat dijalankan","play"],
  ["Analisis berjalan",count(o=>o.status==="processing"),"Job aktif","layers"],
  ["Pemeriksaan",count(o=>o.status==="quality_review"),"Draft menunggu QA","shield"]];
 return [
  ["Total pesanan",orders.length,"Semua permintaan Anda","briefcase"],
  ["Menunggu tindakan",count(o=>["quoted","accepted"].includes(o.status)),"Penawaran atau pembayaran","clock"],
  ["Sedang diproses",count(o=>WORK.has(o.status)),"Termasuk antrean dan QA","layers"],
  ["Hasil tersedia",count(o=>o.published_job_id),"Peta yang sudah dirilis","map"]];
}
function filterOrders(orders,filter,search){const q=search.trim().toLowerCase();
 return orders.filter(o=>(filter==="all"||filter==="work"&&WORK.has(o.status)||filter==="released"&&o.published_job_id||o.status===filter)&&(!q||[o.title,o.code,o.location,o.service_name,o.customer?.name,o.status_name].some(v=>String(v||"").toLowerCase().includes(q))));
}
function orderRows(orders){
 const admin=state.user.role==="admin";
 if(!orders.length)return `<tr><td colspan="5">${empty("Tidak ada pesanan pada filter ini","Coba ubah status atau kata pencarian.")}</td></tr>`;
 return orders.map(o=>`<tr><td><a class="table-title" data-nav href="${detailHref(o.id)}">${e(o.title)}</a><span class="table-sub">${e(o.code)}${admin?" · "+e(o.customer?.name):""}</span></td><td><span>${e(o.service_name)}</span><span class="table-sub">${e(o.area_ha==null?o.data_plan==="tile"?"Tile RGB":"Luas belum ditetapkan":n(o.area_ha)+" ha")}</span></td><td>${badge(o.status,o.status_name)}</td><td><span>${e(o.schedule_date?date(o.schedule_date):date(o.updated_at))}</span><span class="table-sub">${o.schedule_date?"Jadwal":"Diperbarui"}</span></td><td><a class="table-action" data-nav href="${detailHref(o.id)}">Detail ${icon("chevron")}</a></td></tr>`).join("");
}
function orderTable(orders,{full=false,filter="all"}={}){
 if(!orders.length&&!full)return panel("Pesanan","Pantau semua kebutuhan pemetaan.",empty("Belum ada pesanan",(state.user.role==="customer"?"Ajukan kebutuhan untuk memulai pekerjaan pemetaan.":"Pesanan yang tersedia akan muncul di sini."),state.user.role==="customer"?linkBtn("Buat pesanan","/app/new"): "","briefcase"));
 const shown=full?filterOrders(orders,filter,state.search):orders.slice(0,6);
 const controls=full?`<div class="table-tools"><label class="search-field">${icon("search")}<input id="order-search" aria-label="Cari pesanan" type="search" value="${e(state.search)}" placeholder="Cari nama, lokasi, kode…"></label><select id="order-filter" aria-label="Filter status" class="filter-select">${[["all","Semua status"],["work","Pekerjaan aktif"],["released","Hasil tersedia"],...Object.entries(state.config.status)].map(([key,label])=>`<option value="${key}" ${key===filter?"selected":""}>${e(label)}</option>`).join("")}</select><button class="btn ghost sm" data-action="refresh-orders">${icon("refresh")} Muat ulang</button></div>`:"";
 return `<section class="panel"><div class="panel-header"><div><h2>${full?"Daftar pesanan":"Pesanan terbaru"}</h2><p>${full?"Cari dan pantau status pekerjaan.":"Status diperbarui dari alur layanan."}</p></div>${full?"":linkBtn("Lihat semua","/app/orders","ghost","chevron","sm")}</div>${controls}<div class="table-scroll"><table class="data-table order-table"><thead><tr><th>Proyek</th><th>Layanan</th><th>Status</th><th>Waktu</th><th>Aksi</th></tr></thead><tbody id="order-rows">${orderRows(shown)}</tbody></table></div><div class="table-footer"><span id="order-count">${shown.length} dari ${orders.length} pesanan</span><span>Terurut berdasarkan pembaruan</span></div></section>`;
}
function dashboard(){
 const orders=state.orders;const role=state.user.role;
 const first=state.user.name.split(" ")[0];
 const metricHtml=overviewMetrics(orders).map(r=>metric(...r)).join("");
 const queues=ACTION_GROUPS[role].map(([label,sub,glyph,predicate,filter])=>`<a data-nav href="/app/orders?status=${filter}" class="queue-item"><div class="queue-icon">${icon(glyph)}</div><div><strong>${label}</strong><p>${sub}</p></div><span class="queue-number">${orders.filter(predicate).length}</span></a>`).join("");
 const intro=role==="admin"?"Kelola penawaran, pembayaran, penugasan, dan hasil pemetaan dari satu tempat.":role==="operator"?"Tinjau pekerjaan yang ditugaskan, jalankan analisis, dan periksa draft.":"Pantau kebutuhan pemetaan dari permintaan awal hingga laporan siap ditinjau.";
 const title=role==="customer"?`Halo, ${first}.`:role==="admin"?"Ringkasan operasi":"Ruang kerja pemetaan";
 return pageHeader(role==="customer"?"Portal pelanggan":role==="admin"?"Pengelolaan layanan":"Workspace operator",title,intro,
 role==="customer"?linkBtn("Buat pesanan","/app/new","primary","plus"):btn("Perbarui","refresh-orders","secondary","refresh"))+
 `${state.config.demo?`<div class="banner warning">${icon("info")}<span><strong>Lingkungan demo.</strong> Pesanan dan pembayaran contoh tidak merepresentasikan transaksi nyata. Analisis menggunakan model serta dataset penelitian yang tersedia.</span></div>`:""}<div class="metric-grid">${metricHtml}</div><div class="workspace-grid"><div>${orderTable(orders)}</div><div class="stack">${panel("Perlu ditindaklanjuti","Prioritas sesuai peran Anda.",`<div class="queue-list">${queues}</div>`)}<div class="quick-sample"><div><h3>Atlas contoh penelitian</h3><p>Jelajahi layer dan rekap dari sumber MOPAD.</p>${linkBtn("Buka contoh peta","/contoh","secondary","map")}</div></div></div></div>`;
}
function ordersPage(){
 return pageHeader("Alur layanan",state.user.role==="operator"?"Tugas saya":state.user.role==="customer"?"Pesanan saya":"Manajemen pesanan",
 "Tinjau status, lingkup, pembayaran dan pengerjaan setiap proyek.",state.user.role==="customer"?linkBtn("Pesan layanan","/app/new","primary","plus"):btn("Perbarui","refresh-orders","secondary","refresh"))+
 orderTable(state.orders,{full:true,filter:state.filter});
}
function updateRows(){
 const rows=$("order-rows"),count=$("order-count");if(!rows)return;
 const shown=filterOrders(state.orders,state.filter,state.search);rows.innerHTML=orderRows(shown);
 if(count)count.textContent=shown.length+" dari "+state.orders.length+" pesanan";
}
function resultsPage(){
 const results=[];for(const o of state.orders){
  if(state.user.role!=="customer"&&o.status==="quality_review"&&o.current_job_id!==o.published_job_id)
   results.push({order:o,job:o.current_job_id,draft:true});
  if(o.published_job_id)results.push({order:o,job:o.published_job_id,draft:false});
 }
 const items=results.map(({order:o,job,draft})=>`<article class="result-card"><div class="preview"><img src="/api/orders/${o.id}/results/${job}/files/preview.png" alt="Pratinjau hasil ${e(o.title)}" loading="lazy"></div><div>${draft?badge("quality_review","Draft internal"):badge("ready","Hasil dirilis")}<h2 style="margin-top:10px">${e(o.title)}</h2><p>${e(o.code)} · ${e(o.service_name)}</p></div><div class="button-row">${linkBtn(draft?"Periksa draft":"Buka hasil",resultHref(o,job),"primary","map")}${linkBtn("Detail pesanan",detailHref(o.id),"ghost","chevron")}</div></article>`).join("");
 return pageHeader("Layer & penyerahan","Hasil pemetaan","Peta dan berkas yang dapat diakses sesuai hak akun Anda.",linkBtn("Contoh publik","/contoh","secondary","map"))+
 (items?`<div class="result-grid">${items}</div>`:`<section class="panel">${empty("Belum ada hasil yang tersedia","Peta muncul di sini setelah job selesai dan tim merilis hasil.",linkBtn("Lihat pesanan","/app/orders","secondary","arrow"),"layers")}</section>`);
}
function staffPage(){
 const rows=state.staff.map(u=>`<div class="staff-row"><span class="avatar">${e(initials(u.name))}</span><div><strong>${e(u.name)}</strong><small>${e(u.email)} · ${ROLE[u.role]}</small></div><span class="mode-pill live">Aktif</span></div>`).join("");
 return pageHeader("Admin","Tim & operator","Buat akun operator untuk mengerjakan proyek. Penawaran, pembayaran dan rilis tetap berada di bawah admin.")+
 `<div class="team-grid">${panel("Anggota tim",state.staff.length+" akun staf",`<div class="staff-list">${rows}</div>`)}${panel("Tambahkan operator","Akun baru hanya memiliki akses pada pekerjaan yang ditugaskan.",`<form class="action-form" data-form="staff">${field("Nama operator","name","text","","required minlength=\"2\" maxlength=\"100\"")}${field("Email","email","email","","required")}${field("Kata sandi awal","password","password","","required minlength=\"10\" maxlength=\"128\" autocomplete=\"new-password\"","Sampaikan secara aman kepada operator; mereka dapat menggantinya setelah masuk.")}${errBox()}${submitButton("Buat akun operator","plus")}</form>`)}</div>`;
}
function accountPage(){
 return pageHeader("Pengaturan","Akun saya","Kelola identitas masuk dan keamanan akun lokal.")+
 `<section class="panel pad account-panel"><div class="account-details"><span class="avatar">${e(initials(state.user.name))}</span><div><h2>${e(state.user.name)}</h2><p>${e(state.user.email)} · ${ROLE[state.user.role]}</p><p>${e(state.user.organization||"Organisasi belum diisi")}</p></div></div><h2 class="section-title" style="margin-bottom:15px">Ganti kata sandi</h2><form class="action-form" data-form="change-password">${field("Kata sandi saat ini","old_password","password","","required autocomplete=\"current-password\"")}${field("Kata sandi baru","password","password","","required minlength=\"10\" maxlength=\"128\" autocomplete=\"new-password\"")}${field("Konfirmasi kata sandi baru","confirm_password","password","","required minlength=\"10\" autocomplete=\"new-password\"")}${errBox()}<p class="help">Setelah mengganti kata sandi, Anda akan diminta masuk kembali.</p><div class="button-row">${submitButton("Simpan kata sandi","check")}</div></form></section>`;
}
function newWizard(service){
 state.wizard={step:0,values:{service:serviceMeta[service]?service:"segmentation",data_plan:service==="inspection"?"tile":"geotiff",title:"",location:"",area_ha:"",target_date:"",brief:""}};
}
function wizardChoice(name,key,title,desc,glyph,value){
 return `<label class="choice"><input type="radio" name="${name}" value="${key}" required ${value===key?"checked":""}><span><strong>${e(title)}</strong><small>${e(desc)}</small></span><span class="choice-icon">${icon(glyph)}</span></label>`;
}
function wizardPage(){
 const w=state.wizard,v=w.values;const step=w.step;
 const steps=["Pilih layanan","Data proyek","Tinjau permintaan"];
 let inside="";
 if(step===0){
  inside=`<h2>Layanan apa yang Anda butuhkan?</h2><p>Pilih satu jenis analisis. Detail lingkup akan dibahas sebelum penawaran.</p><div class="choice-stack">${Object.entries(serviceMeta).map(([key,m])=>wizardChoice("service",key,m.title,m.description,m.icon,v.service)).join("")}</div><h2 style="font-size:17px;margin:24px 0 12px">Sumber data</h2><div class="choice-stack">${(v.service==="inspection"?[["tile","Tile RGB 640 × 640","Citra overhead sebanding data Ganoderma; koordinat piksel.","image"]]:[["geotiff","Saya punya orthomosaic GeoTIFF","Citra RGB sudah dirakit dan memiliki CRS.","map"],["tile","Saya punya tile RGB 640 × 640","Hasil dalam koordinat piksel, tanpa hektar/GPS.","image"],["field","Butuh persiapan data","Tim akan meninjau kebutuhan survei atau orthomosaic secara manual.","pin"]]).map(([key,title,desc,glyph])=>wizardChoice("data_plan",key,title,desc,glyph,v.data_plan)).join("")}</div>`;
 }else if(step===1){
  inside=`<h2>Informasi kebun</h2><p>Beritahu tim lokasi, estimasi luas, serta keluaran yang ingin Anda terima.</p><div class="form-grid"><div class="full">${field("Nama proyek / kebun","title","text",v.title,"required minlength=\"3\" maxlength=\"110\" placeholder=\"Contoh: Inventaris Blok A\"")}</div><div class="full">${field("Lokasi kebun","location","text",v.location,"required minlength=\"3\" maxlength=\"180\" placeholder=\"Kabupaten, provinsi atau wilayah\"")}</div>${field("Estimasi luas (ha, opsional)","area_ha","number",v.area_ha,"min=\"0.01\" step=\"any\" max=\"1000000\" placeholder=\"Contoh: 50\"")}${field("Target tanggal (opsional)","target_date","date",v.target_date,'min="'+state.config.today+'"')}<div class="full">${textarea("Kebutuhan dan catatan (opsional)","brief",v.brief,'maxlength="2000" placeholder="Misalnya batas blok, kebutuhan ekspor, kondisi data atau prioritas inspeksi."')}</div></div>`;
 }else{
  inside=`<h2>Tinjau permintaan</h2><p>Tim CrownTrace akan meninjau informasi ini untuk menyiapkan penawaran. Belum ada biaya yang dibebankan.</p><dl class="summary-grid"><div><dt>Layanan</dt><dd>${e(serviceMeta[v.service].title)}</dd></div><div><dt>Jenis data</dt><dd>${e(state.config.data_plans[v.data_plan])}</dd></div><div><dt>Proyek</dt><dd>${e(v.title)}</dd></div><div><dt>Lokasi</dt><dd>${e(v.location)}</dd></div><div><dt>Luas estimasi</dt><dd>${v.area_ha?n(v.area_ha)+" ha":"Belum diketahui"}</dd></div><div><dt>Target</dt><dd>${date(v.target_date)}</dd></div><div class="full"><dt>Catatan</dt><dd>${e(v.brief||"—")}</dd></div></dl><div class="form-note">Pembayaran baru dilakukan setelah Anda menyepakati penawaran. Hasil citra berupa estimasi model dan harus ditinjau terhadap kondisi lapangan.</div>${check("Saya memahami bahwa permintaan ini belum merupakan penawaran yang mengikat.","acknowledge")}`;
 }
 const summary=`<div class="mini-steps"><div class="mini-step">${icon("file")}<span>Tim menyusun penawaran sesuai kebutuhan.</span></div><div class="mini-step">${icon("wallet")}<span>Sepakati biaya sebelum pengerjaan.</span></div><div class="mini-step">${icon("shield")}<span>Peta dirilis setelah pemeriksaan kualitas.</span></div></div>`;
 return pageHeader("Pesanan baru","Ajukan pemetaan","Cukup isi kebutuhan kebun Anda. Tim yang mengurus analisis.",linkBtn("Kembali ke pesanan","/app/orders","ghost","left"))+
 `<div class="order-wizard"><section class="panel wizard-panel"><div class="wizard-steps">${steps.map((s,i)=>`<div class="wizard-step ${i===step?"active":i<step?"done":""}"><i>${i<step?icon("check","sm"):i+1}</i>${s}</div>`).join("")}</div><form data-form="wizard" id="wizard-form">${inside}${errBox()}<div class="wizard-actions">${step?btn("Kembali","wizard-back","secondary","left"):linkBtn("Batal","/app/orders","ghost","left")}${submitButton(step===2?"Ajukan permintaan":"Lanjutkan","arrow")}</div></form></section><aside class="wizard-summary">${panel("Ringkasan kebutuhan","Tidak ada tarif otomatis.",`<div class="queue-item"><div class="queue-icon">${icon(serviceMeta[v.service].icon)}</div><div><strong>${e(serviceMeta[v.service].title)}</strong><p>${e(state.config.data_plans[v.data_plan])}</p></div></div>${summary}`)}</aside></div>`;
}
function captureWizard(form){if(form){for(const [key,value] of new FormData(form)){if(key in state.wizard.values)state.wizard.values[key]=value;}}}


const STATUS_HINT={
 submitted:"Tim meninjau kebutuhan Anda sebelum menyusun penawaran.",quoted:"Tinjau penawaran, lingkup pekerjaan dan masa berlaku.",
 accepted:"Penawaran telah disepakati. Pembayaran belum terverifikasi.",payment_review:"Konfirmasi telah masuk dan menunggu pemeriksaan manual.",
 paid:"Pembayaran telah diperiksa. Siapkan sumber citra, validasi, dan penugasan.",scheduled:"Data dan profil sudah dipilih. Tim dapat menjalankan analisis.",
 processing:"Model mengolah data yang sudah divalidasi.",quality_review:"Draft tersedia untuk pemeriksaan internal sebelum rilis.",
 ready:"Hasil sudah dirilis. Pelanggan dapat meninjau dan mengunduhnya.",completed:"Pelanggan telah menerima hasil; laporan tetap tersedia.",
 revision_requested:"Tim perlu menangani koreksi. Hasil rilis sebelumnya tetap dapat dibuka.",processing_failed:"Job gagal atau terputus. Tim dapat memperbaiki data atau menjalankan ulang.",
 cancelled:"Pesanan dibatalkan sebelum verifikasi pembayaran. Tidak ada pengembalian dana otomatis."
};
const STAGES={submitted:0,quoted:1,accepted:2,payment_review:2,paid:3,scheduled:3,processing:3,quality_review:3,revision_requested:3,processing_failed:3,ready:4,completed:5,cancelled:-1};
function statusSteps(o){
 const stage=STAGES[o.status]??0;
 return `<section class="panel steps-card"><div class="status-steps">${["Permintaan","Penawaran","Pembayaran","Pengerjaan","Hasil"].map((label,i)=>`<div class="status-step ${i<stage?"done":i===stage?"active":""}"><div class="step-line"></div><span class="step-label">${icon(i<stage?"check":"dot","sm")}${label}</span></div>`).join("")}</div><p class="step-note">${e(STATUS_HINT[o.status]||o.status_name)}</p></section>`;
}
function actionCard(title,desc,glyph,inner){return `<section class="panel action-panel"><div class="action-heading">${icon(glyph)}<div><h2>${e(title)}</h2><p>${e(desc)}</p></div></div>${inner}</section>`;}
function actionForm(action,inner,label,style="primary",glyph="arrow"){return `<form class="action-form" data-form="order-action" data-order-action="${action}">${inner}${errBox()}<div class="button-row">${submitButton(label,glyph,style)}</div></form>`;}
function uploadForm(kind,caption,accept,help){
 return `<form class="upload-form" data-form="upload" data-kind="${kind}"><label class="field"><span>${e(caption)}</span><input type="file" name="upload_file" accept="${e(accept)}" required><small>${e(help)}</small></label><div class="upload-status" hidden><small class="help upload-label">Mengirim berkas…</small><div class="progress-track"><i style="width:0%"></i></div></div>${errBox()}<div class="button-row">${submitButton("Unggah "+(kind==="payment_proof"?"bukti":kind==="boundary"?"batas":"citra"),"upload","secondary")}</div></form>`;
}
function downloadUpload(o,f){return `<a href="/api/orders/${o.id}/uploads/${f.id}" download aria-label="Unduh ${e(f.name)}" title="Unduh berkas">${icon("download")}</a>`;}
function fileRow(o,f){return `<div class="file-row"><div class="file-icon">${icon(f.kind==="source"?"image":f.kind==="boundary"?"map":"file")}</div><div class="file-info"><strong title="${e(f.name)}">${e(f.name)}</strong><small>${f.kind==="source"?"Citra":f.kind==="boundary"?"Batas blok":"Bukti pembayaran"} · ${bytes(f.size)}${f.id===o.source_file_id||f.id===o.boundary_file_id?" · Aktif":""}</small></div>${downloadUpload(o,f)}</div>`;}
function dateAfter(days){const d=new Date(state.config.today+"T00:00:00Z");d.setUTCDate(d.getUTCDate()+days);return d.toISOString().slice(0,10)}
function orderActions(o){
 const a=new Set(o.permissions);const q=o.quote;const p=o.payment;const out=[];
 if(a.has("quote")){
  const inner=`<div class="form-grid">${field("Nominal (Rp)","amount","number",q?.amount||"","required min=\"1\" max=\"1000000000000\" step=\"1\" placeholder=\"Contoh: 2500000\"")}${field("Estimasi durasi (hari)","estimated_days","number",q?.estimated_days||5,"required min=\"1\" max=\"365\" step=\"1\"")}<div class="full">${textarea("Lingkup dan keluaran","scope",q?.scope||"","required minlength=\"5\" maxlength=\"2000\" placeholder=\"Jelaskan batas area, jenis analisis dan keluaran yang akan diserahkan.\"")}</div><div class="full">${field("Masa berlaku hingga","expires_on","date",dateAfter(7),'required min="'+state.config.today+'"')}</div>${state.config.demo?'':'<div class="full">'+textarea("Petunjuk pembayaran (opsional)","payment_instruction",q?.payment_instruction||"",'maxlength="1200"',"Isi hanya metode yang benar-benar telah disepakati. Tidak ada payment gateway.")+'</div>'}</div>${state.config.demo?'<p class="form-note">Nominal merupakan simulasi, bukan tarif atau transaksi nyata.</p>':""}`;
  out.push(actionCard(q&&o.status==="quoted"?"Perbarui penawaran":"Siapkan penawaran","Pelanggan akan menerima versi penawaran baru untuk keputusan.", "file",actionForm("quote",inner,"Kirim penawaran")));
 }
 if(a.has("accept_quote")||a.has("decline_quote")){
  const inner=`<div class="quote-amount">${rupiah(q?.amount)}</div><p class="scope-text" style="margin-bottom:14px">${e(q?.scope)}</p><p class="help">Berlaku hingga ${date(q?.expires_on)} · Estimasi ${n(q?.estimated_days)} hari setelah prasyarat terpenuhi.</p><div class="button-row" style="margin-top:13px">${a.has("accept_quote")?btn("Terima penawaran","accept_quote","primary","check"):'<span class="form-note">Penawaran kedaluwarsa; minta tim memperbaruinya.</span>'}</div><details class="subsection-line"><summary class="help">Tolak atau minta perubahan penawaran</summary><div style="margin-top:12px">${actionForm("decline_quote",textarea("Alasan","reason","","required minlength=\"3\" maxlength=\"1000\""),"Tolak penawaran","secondary","arrow")}</div></details>`;
  out.push(actionCard("Putuskan penawaran","Pastikan lingkup sesuai kebutuhan. Penerimaan mencatat deal dalam portal.","file",inner));
 }
 if(a.has("submit_payment")){
  const proofs=o.files.filter(f=>f.kind==="payment_proof");
  const inner=`<p class="scope-text">${e(q?.payment_instruction||"Hubungi tim melalui pesan pesanan untuk metode pembayaran yang disepakati.")}</p><div class="subsection-line">${a.has("upload_payment_proof")?uploadForm("payment_proof","Bukti pembayaran (opsional)","image/jpeg,image/png,application/pdf,.pdf","JPG/PNG/PDF maksimal 8 MB. Unggah sebelum mengirim konfirmasi."):""}</div><div class="subsection-line">${actionForm("submit_payment",field("Nominal disepakati (Rp)","amount","number",q?.amount,'required readonly')+field("Referensi pembayaran","reference","text","","required minlength=\"3\" maxlength=\"120\" placeholder=\"Nomor transaksi atau referensi\"")+
  `<label class="field"><span>Lampirkan bukti</span><select name="proof_file_id"><option value="">Tanpa berkas</option>${proofs.map(f=>`<option value="${f.id}">${e(f.name)}</option>`).join("")}</select></label>`+
  '<p class="form-note">Konfirmasi ini belum mengubah status menjadi lunas. Admin memeriksa secara manual.</p>',"Kirim konfirmasi","primary","wallet")}</div>`;
  out.push(actionCard("Konfirmasi pembayaran","Deal telah diterima. Gunakan informasi pembayaran yang benar-benar disepakati.","wallet",inner));
 }
 if(a.has("approve_payment")){
  const proof=o.files.find(f=>f.id===p?.proof_file_id);
  const inner=`<div class="quote-amount">${rupiah(p?.amount)}</div><dl class="kv"><div><dt>Referensi</dt><dd>${e(p?.reference)}</dd></div><div><dt>Diterima</dt><dd>${stamp(p?.submitted_at)}</dd></div></dl><div style="margin-top:14px">${proof?fileRow(o,proof):'<p class="help">Tidak ada berkas bukti terlampir.</p>'}</div><p class="form-note" style="margin:13px 0">${state.config.demo?"Simulasi saja. Tidak ada mutasi rekening nyata.":"Periksa pembayaran di luar sistem terlebih dahulu. Jangan menyetujui hanya dari unggahan bukti."}</p>${actionForm("approve_payment",check(state.config.demo?"Saya memeriksa referensi simulasi.":"Saya telah mencocokkan pembayaran melalui saluran yang disepakati.","verified")+textarea("Catatan verifikasi","note","","required minlength=\"3\" maxlength=\"1000\""),"Verifikasi pembayaran","primary","check")}<details class="subsection-line"><summary class="help">Konfirmasi belum sesuai?</summary><div style="margin-top:12px">${actionForm("reject_payment",textarea("Alasan perbaikan","note","","required minlength=\"3\" maxlength=\"1000\""),"Minta konfirmasi ulang","secondary","arrow")}</div></details>`;
  out.push(actionCard("Verifikasi pembayaran","Admin harus menilai konfirmasi sebelum pekerjaan diizinkan.","wallet",inner));
 }
 if(a.has("validate_data")){
  out.push(actionCard("Validasi data citra","Cek sumber, hak penggunaan, resolusi/GSD dan cakupan yang disepakati. Pemeriksaan metadata berjalan di server.","shield",
   actionForm("validate_data",check("Saya telah memeriksa sumber, skala, cakupan, dan izin penggunaan data.","acknowledge")+textarea("Catatan validasi","note",o.data_review?.review_note||"","required minlength=\"5\" maxlength=\"1000\" placeholder=\"Contoh: tile 640 px, sebanding profil Trees; data sumber disetujui.\""),"Validasi data","primary","check")));
 }
 if(a.has("schedule")){
  const profs=o.profiles.map(p=>[p.id,p.name+(state.config.models[p.id]?"":" (Belum siap)"),!state.config.models[p.id]]);
  const operators=state.staff.filter(u=>u.active).map(u=>[u.id,u.name+" · "+ROLE[u.role]]);
  const selected=o.profile||profs.find(p=>!p[2])?.[0]||"";
  const inner=selectField("Operator / penanggung jawab","assigned_to",[["","Pilih anggota tim",true],...operators],o.assigned_to||"")+
   selectField("Profil model","profile",[["","Pilih profil",true],...profs],selected,"Profil dibatasi oleh layanan dan format input.")+
   `<p class="help" id="profile-note">${e(state.config.profiles[selected]?.note||"")}</p>`+
   field("Tanggal pengerjaan","schedule_date","date",o.schedule_date&&o.schedule_date>=state.config.today?o.schedule_date:state.config.today,'required min="'+state.config.today+'"')+
   textarea("Catatan penugasan","note",o.schedule_note||"","maxlength=\"1000\"");
  out.push(actionCard("Atur pengerjaan","Pilih anggota tim, tanggal, dan profil model yang sudah tersedia.","calendar",actionForm("schedule",inner,"Simpan penugasan","primary","calendar")));
 }
 if(a.has("start_job")){
  const inner=`<dl class="kv"><div><dt>Profil model</dt><dd>${e(state.config.profiles[o.profile]?.name||"—")}</dd></div><div><dt>Operator</dt><dd>${e(o.assigned_name||"—")}</dd></div><div class="full"><dt>Mode</dt><dd>${e(state.config.profiles[o.profile]?.note||"")}</dd></div></dl><div class="button-row" style="margin-top:17px">${btn(o.status==="processing_failed"?"Jalankan ulang analisis":"Mulai analisis","start_job","primary","play")}</div>`;
  out.push(actionCard("Jalankan pemetaan","Sumber tersimpan, profil terkunci. Job berjalan pada mesin analisis lokal.","play",inner));
 }
 if(o.status==="quality_review"&&state.user.role!=="customer"){
  const inner=`<div class="button-row" style="margin-bottom:17px">${linkBtn("Periksa draft peta",resultHref(o,o.current_job_id),"primary","map")}</div>${a.has("publish")?actionForm("publish",
  check("Sumber, cakupan dan skala telah ditinjau.","source_checked")+check("Hitungan, layer dan berkas ekspor telah diperiksa.","outputs_checked")+check("Lingkup hasil sesuai dengan kesepakatan.","scope_checked")+check("Keterbatasan model dan kebutuhan pemeriksaan lapangan dijelaskan.","review_checked")+
  textarea("Catatan QA / batas hasil","note","","required minlength=\"10\" maxlength=\"2000\" placeholder=\"Sebutkan cakupan, keterbatasan dan tindak lanjut hasil.\""),"Rilis hasil","primary","check"):
  '<p class="help">Draft ini hanya tersedia untuk tim. Admin yang akan merilis hasil.</p>'}
  <details class="subsection-line"><summary class="help">Draft belum layak? Kembalikan untuk perbaikan.</summary><div style="margin-top:12px">${actionForm("rework",textarea("Catatan koreksi","note","","required minlength=\"5\" maxlength=\"1000\""),"Kembalikan draft","secondary","refresh")}</div></details>`;
  out.push(actionCard("Pemeriksaan & serah terima","Buka hasil analisis dan lakukan QA. Hanya rilis admin yang membuka akses pelanggan.","shield",inner));
 }
 if(a.has("complete")){
  out.push(actionCard("Tinjau hasil yang dirilis","Lihat peta dan unduh berkas. Terima hasil atau ajukan revisi jika diperlukan.","layers",
  `<div class="button-row">${linkBtn("Buka hasil",resultHref(o,o.published_job_id),"primary","map")}${btn("Terima hasil","complete","secondary","check")}</div><details class="subsection-line"><summary class="help">Saya perlu koreksi hasil</summary><div style="margin-top:12px">${actionForm("request_revision",textarea("Catatan revisi","note","","required minlength=\"5\" maxlength=\"2000\""),"Kirim permintaan revisi","secondary","message")}</div></details>`));
 }
 return out.join("");
}
function quotePanel(o){
 if(state.user.role==="operator")return "";
 const q=o.quote,p=o.payment;
 if(!q)return panel("Penawaran & pembayaran","Lingkup dan biaya ditetapkan tim.",'<p class="empty-inline">Penawaran belum tersedia. Belum ada biaya yang disepakati.</p>');
 const proof=o.files.find(f=>f.id===p?.proof_file_id);
 return panel("Penawaran & pembayaran",`Penawaran versi ${q.version} · ${q.decision==="accepted"?"Disepakati":q.decision==="pending"?"Menunggu keputusan":q.decision==="declined"?"Ditolak":"Tidak aktif"}`,
 `<div class="quote-amount">${rupiah(q.amount)}</div><dl class="kv"><div><dt>Estimasi durasi</dt><dd>${q.estimated_days} hari</dd></div><div><dt>Berlaku hingga</dt><dd>${date(q.expires_on)}</dd></div></dl><div class="subsection-line"><p class="help">Lingkup</p><p class="scope-text">${e(q.scope)}</p></div><div class="subsection-line"><p class="help">Status pembayaran</p><div style="margin-top:8px">${p?badge(p.status==="approved"?"paid":p.status==="pending"?"payment_review":"accepted",p.status==="approved"?"Terverifikasi manual":p.status==="pending"?"Dalam pemeriksaan":p.status==="rejected"?"Perlu konfirmasi ulang":"Dibatalkan"):badge("accepted","Belum dibayar")}</div>${p?`<dl class="kv" style="margin-top:13px"><div><dt>Referensi</dt><dd>${e(p.reference)}</dd></div><div><dt>Nominal</dt><dd>${rupiah(p.amount)}</dd></div></dl>${p.review_note?`<p class="scope-text" style="margin-top:12px">${e(p.review_note)}</p>`:""}${proof?`<div style="margin-top:13px">${fileRow(o,proof)}</div>`:""}`:""}</div>${o.quote_history?.length>1?`<details class="subsection-line"><summary class="help">Riwayat penawaran (${o.quote_history.length})</summary>${o.quote_history.map(v=>`<p class="help" style="margin-top:8px">Versi ${v.version} · ${rupiah(v.amount)} · ${e(v.decision)} · ${date(v.created_at)}</p>`).join("")}</details>`:""}`);
}
function dataPanel(o){
 const a=new Set(o.permissions);const active=o.files.filter(f=>f.kind!=="payment_proof");
 const links=active.length?`<div class="file-list">${active.map(f=>fileRow(o,f)).join("")}</div>`:'<p class="empty-inline">Belum ada citra sumber yang disimpan untuk pesanan ini.</p>';
 const uploads=`${a.has("use_sample")?`<div class="button-row" style="margin-top:13px">${btn("Gunakan citra contoh","use_sample","secondary","image")}</div><p class="help">Contoh akan diberi penanda penelitian, khusus workspace demo.</p>`:""}
 ${a.has("upload_source")?uploadForm("source","Citra sumber",o.data_plan==="tile"?"image/png,image/jpeg,.jpg,.jpeg,.png":".tif,.tiff,image/tiff",o.data_plan==="tile"?"Tile RGB 640 × 640; batas 5–12 MB sesuai layanan.":"Orthomosaic GeoTIFF RGB, maksimal 2 GiB. Unggah setelah dirakit dengan CRS."): ""}
 ${a.has("upload_boundary")?uploadForm("boundary","Batas blok (opsional)",".geojson,.json","GeoJSON FeatureCollection WGS84, maksimal 4 MB."):""}`;
 const validation=o.data_review?`<div class="subsection-line"><p class="help">${badge("paid","Data divalidasi")} · ${date(o.data_review.checked_at)}</p><dl class="kv" style="margin-top:12px"><div><dt>Dimensi</dt><dd>${n(o.data_review.width)} × ${n(o.data_review.height)} px</dd></div><div><dt>Koordinat</dt><dd>${e(o.data_review.crs||"Piksel")}</dd></div><div class="full"><dt>Catatan</dt><dd>${e(o.data_review.review_note)}</dd></div></dl></div>`:"";
 return panel("Data pemetaan",o.data_plan==="field"?"Persiapan/pengambilan data diatur bersama tim; unggah orthomosaic akhir.":"Sumber dan batas blok yang digunakan untuk pekerjaan.",links+uploads+validation);
}
function jobHTML(o){
 const j=o.job;if(!j)return "";
 const phase={queued:"Dalam antrean mesin lokal",preparing:"Menyiapkan model dan sumber",persiapan:"Menyiapkan raster",deteksi:"Deteksi pohon",mahkota:"Delineasi mahkota",ekspor:"Menyusun layer dan ekspor",done:"Analisis selesai",failed:"Analisis gagal",interrupted:"Analisis terputus"};
 const current=o.status==="processing"&&["queued","running"].includes(j.status);
 const fraction=j.total>0?Math.max(0,Math.min(100,j.completed/j.total*100)):0;
 const known=j.status==="running"&&j.total>1;
 return `<section class="panel job-panel" id="job-panel"><div class="job-status"><div><h3>${current?"Proses pemetaan":"Analisis terakhir"}</h3><small>Percobaan ${j.attempt} · ${e(state.config.profiles[j.profile]?.name||j.profile)}</small></div>${badge(current?"processing":j.status==="succeeded"?"paid":"processing_failed",j.status==="queued"?"Antrean":j.status==="running"?"Berjalan":j.status==="succeeded"?"Selesai":"Perlu ulang")}</div><div class="progress-track ${current&&!known?"indeterminate":""}" ${current&&known?'role="progressbar" aria-valuemin="0" aria-valuemax="'+j.total+'" aria-valuenow="'+j.completed+'"':""}><i style="width:${j.status==="succeeded"?100:fraction}%"></i></div><p class="job-stage">${e(phase[j.phase]||j.phase)}${known?` · ${j.completed}/${j.total} unit`:""}</p>${current?'<p class="help">Status diperbarui otomatis. Anda dapat meninggalkan halaman dan kembali nanti.</p>':""}${j.error?`<p class="job-error">${e(j.error)}</p>`:""}</section>`;
}
function publishedPanel(o){
 if(!o.publications.length&&!(o.status==="quality_review"&&state.user.role!=="customer"))return "";
 const list=o.publications.map(p=>`<div class="file-row"><div class="file-icon">${icon("layers")}</div><div class="file-info"><strong>Rilis versi ${p.version}</strong><small>${date(p.released_at)}</small></div><a data-nav href="${resultHref(o,p.job_id)}" aria-label="Buka hasil versi ${p.version}">${icon("arrow")}</a></div><p class="help" style="padding:0 3px 10px;white-space:pre-wrap;overflow-wrap:anywhere">${e(p.note)}</p>`).join("");
 return panel("Hasil pemetaan","Rilis tersimpan sebagai versi terpisah.",`<div class="file-list">${list}${o.status==="quality_review"&&state.user.role!=="customer"?linkBtn("Buka draft internal",resultHref(o,o.current_job_id),"secondary","layers"):""}</div>`);
}
function timelinePanel(o){
 return panel("Riwayat pesanan","Jejak keputusan dan perubahan status.",`<div class="timeline-list">${o.timeline.map(t=>`<div class="timeline-item"><span class="timeline-dot"></span><div><strong>${e(t.label)}</strong><small>${e(t.actor_name)} · ${stamp(t.created_at)}</small>${t.note?`<p>${e(t.note)}</p>`:""}</div></div>`).join("")}</div>`);
}
function messagePanel(o){
 const can=o.permissions.includes("message");
 return panel("Pesan proyek","Komunikasi yang tersimpan di pesanan ini.",`<div class="message-list">${o.messages.length?o.messages.map(m=>`<div class="message-row ${m.actor_id===state.user.id?"mine":""}"><div class="message-meta"><strong>${e(m.actor_name)}</strong><small>${stamp(m.created_at)}</small></div><p>${e(m.body)}</p></div>`).join(""):'<p class="empty-inline">Belum ada percakapan. Gunakan pesan jika ada pertanyaan tentang pekerjaan.</p>'}</div>${can?actionForm("message",textarea("Tulis pesan","body","","required maxlength=\"2000\" placeholder=\"Tanyakan atau sampaikan kebutuhan tambahan.\""),"Kirim pesan","secondary","message"):""}`);
}
function detailPage(o){
 const a=new Set(o.permissions);
 const release=o.published_job_id?linkBtn("Lihat hasil",resultHref(o,o.published_job_id),"primary","map"):"";
 const invoice=state.user.role!=="operator"&&o.quote?.decision==="accepted"?`<a class="btn secondary" href="/api/orders/${e(o.id)}/invoice" download>${icon("file")} Ringkasan tagihan</a>`:"";
 const header=`<div class="workspace-header order-head"><div><div class="order-id"><a data-nav class="table-action" href="/app/orders">${icon("left")} Pesanan</a><span>/</span><span>${e(o.code)}</span>${badge(o.status,o.status_name)}</div><h1>${e(o.title)}</h1><p>${e(o.location)} · ${e(o.service_name)}</p></div><div class="button-row">${release}${invoice}${btn("Muat ulang","refresh-detail","secondary","refresh")}</div></div>`;
 const brief=panel("Ringkasan proyek","Kebutuhan yang disampaikan saat pemesanan.",`<dl class="kv"><div><dt>Layanan</dt><dd>${e(o.service_name)}</dd></div><div><dt>Pelanggan</dt><dd>${e(o.customer?.name)}${o.customer?.organization?" · "+e(o.customer.organization):""}</dd></div><div><dt>Sumber data</dt><dd>${e(state.config.data_plans[o.data_plan])}</dd></div><div><dt>Luas estimasi</dt><dd>${o.area_ha==null?"Belum diketahui":n(o.area_ha)+" ha"}</dd></div><div><dt>Target pelanggan</dt><dd>${date(o.target_date)}</dd></div><div><dt>Jadwal tim</dt><dd>${date(o.schedule_date)}</dd></div><div><dt>Operator</dt><dd>${e(o.assigned_name||"Belum ditugaskan")}</dd></div><div><dt>Diajukan</dt><dd>${date(o.created_at)}</dd></div></dl><p class="brief-text">${e(o.brief||"Tidak ada catatan tambahan.")}</p>${o.schedule_note?`<p class="brief-text">${e(o.schedule_note)}</p>`:""}`);
 const cancel=a.has("cancel")?panel("Pembatalan","Tersedia hanya sebelum pembayaran diverifikasi.",`<details><summary class="help">Batalkan pesanan</summary><div style="padding-top:13px">${actionForm("cancel",textarea("Alasan","reason","","required minlength=\"3\" maxlength=\"1000\"")+'<p class="form-note">Jika sudah melakukan transfer, hubungi tim terlebih dahulu. Pembatalan tidak otomatis mengembalikan dana.</p>',"Batalkan pesanan","danger","close")}</div></details>`):"";
 return header+(o.example?`<div class="banner warning">${icon("info")}<span>Pesanan / data contoh penelitian. Nominal bukan transaksi nyata, koordinat demonstrasi tidak mewakili lokasi perkebunan sebenarnya.</span></div>`:"")+
 statusSteps(o)+`<div class="order-layout"><div class="stack">${orderActions(o)}${jobHTML(o)}${publishedPanel(o)}${brief}${dataPanel(o)}</div><aside class="stack">${quotePanel(o)}${timelinePanel(o)}${messagePanel(o)}${cancel}</aside></div>`;
}
function renderDetail(o,{preserve=false}={}){
 const previous=preserve?document.querySelector('form[data-order-action="message"] textarea[name="body"]')?.value:null;
 state.detail=o;appMount("Detail pesanan",detailPage(o),"/app/orders");
 if(previous){const input=document.querySelector('form[data-order-action="message"] textarea[name="body"]');if(input)input.value=previous;}
 if(o.status==="processing")startPoll(o.id);
}
function startPoll(id){
 clearTimeout(state.poll);
 const run=async()=>{
  if(!location.pathname.endsWith("/order/"+id))return;
  if(state.busy){state.poll=setTimeout(run,1500);return;}
  try{
   const updated=await api("/api/orders/"+id);
   if(updated.status!==state.detail.status||updated.version!==state.detail.version){renderDetail(updated,{preserve:true});if(updated.status==="processing")return;toast(updated.status==="quality_review"?"Draft analisis selesai dan siap diperiksa.":"Status pekerjaan diperbarui.");return;}
   state.detail=updated;const node=$("job-panel");if(node){const wrapper=document.createElement("div");wrapper.innerHTML=jobHTML(updated);node.replaceWith(wrapper.firstElementChild)}
  }catch(err){if(err.status===401){state.user=null;state.csrf=null;navigate(authHref(location.pathname),true);return;}}
  if(location.pathname.endsWith("/order/"+id))state.poll=setTimeout(run,1500);
 };
 state.poll=setTimeout(run,1500);
}


const LAYER_INFO={imagery:["Citra dasar","image","#7baf9d"],points:["Titik pohon","pin","#9ce4bb"],crowns:["Batas mahkota","layers","#b5f6cc"],blocks:["Batas blok","map","#a5d2ea"],density:["Kepadatan per petak","bar","#d9df9f"],coverage:["Area kurang tercakup","grid","#e9c57c"],review:["Kandidat inspeksi / indikasi","scan","#f09eaa"],boxes:["Kotak deteksi","scan","#84d9c5"]};
const visualLabels={Healthy:"Tampak normal",Small:"Tajuk kecil",Yellow:"Tajuk menguning",Dead:"Indikasi mati",Unassigned:"Belum dinilai",palm_crown:"Mahkota terdeteksi"};
function reportPage(rep,base,title,{order=null,publication=null,draft=false}={}){
 const geo=rep.coordinate_mode==="geographic";
 const third=geo?metric("Area tercakup",n(rep.covered_ha)+" ha",n(rep.coverage_pct)+"% dari area blok","map"):
 rep.review_candidates!=null?metric("Kandidat inspeksi",n(rep.review_candidates),"Belum terkonfirmasi lapangan","scan"):
 metric("Koordinat","Piksel",n(rep.width)+" × "+n(rep.height)+" px","map");
 const options=rep.layers.map(key=>{const meta=LAYER_INFO[key];if(!meta)return"";
  return `<label class="layer-control">${icon(meta[1])}<span>${meta[0]}</span><input type="checkbox" data-layer="${key}" ${["imagery","points","crowns","blocks","boxes"].includes(key)?"checked":""}></label>`;
 }).join("");
 const downloads=rep.files.filter(f=>!["map.json","preview.png","mask-layer.png"].includes(f.name)).map(f=>`<a href="${base}/files/${f.name}?download=1" download>${icon("download")}<span>${e(f.label)}</span><small>${bytes(f.bytes)}</small></a>`).join("");
 const blocks=rep.blocks.length?panel("Rekap blok","Hektar didasarkan pada cakupan raster yang tersedia.",`<div class="table-scroll"><table class="data-table"><thead><tr><th>Blok</th><th>Deteksi</th><th>Cakupan</th><th>Pohon/ha tercakup</th></tr></thead><tbody>${rep.blocks.map((b,i)=>`<tr><td><button type="button" class="btn ghost" data-block-focus="${i}">${e(b.block)}${icon("arrow","sm")}</button></td><td>${n(b.trees)}</td><td>${n(b.coverage_pct)}%</td><td>${n(b.trees_per_covered_ha)}</td></tr>`).join("")}</tbody></table></div>`):"";
 const counts=Object.entries(rep.visual_counts||{}).filter(([,value])=>value>0);
 const countBars=panel(rep.kind==="inspection"?"Penyaringan kandidat":"Kategori visual","Bukan konfirmasi kesehatan atau penyakit.",counts.length?`<div class="mini-bars">${counts.map(([label,value])=>`<div><div class="mini-bar-head"><span>${e(visualLabels[label]||label)}</span><strong>${n(value)}</strong></div><div class="progress-track"><i style="width:${Math.min(100,Math.max(0,value/Math.max(1,rep.trees)*100))}%"></i></div></div>`).join("")}</div>`:'<p class="empty-inline">Tidak ada kategori visual yang dilaporkan.</p>');
 const metadata=panel("Provenance model","Profil serta referensi koordinat hasil analisis.",`<dl class="kv"><div><dt>Profil</dt><dd>${e(rep.profile_name)}</dd></div><div><dt>CRS</dt><dd>${e(rep.crs||"Tidak ada (piksel)")}</dd></div><div class="full"><dt>Detektor</dt><dd>${e(rep.detector||"—")}</dd></div>${rep.segmenter?`<div class="full"><dt>Segmenter</dt><dd>${e(rep.segmenter)}</dd></div>`:""}${rep.classifier?`<div class="full"><dt>Model penyaring</dt><dd>${e(rep.classifier)}</dd></div>`:""}${rep.elapsed_seconds!=null?`<div><dt>Waktu engine</dt><dd>${n(rep.elapsed_seconds)} s</dd></div>`:""}</dl>`);
 const note=`<div class="banner ${rep.example||draft?"warning":""}">${icon("info")}<div><strong>${draft?"Draft internal — belum dirilis.":rep.example?"Contoh penelitian.":"Catatan hasil."}</strong> ${e(rep.note)}${publication?.note?`<p style="margin-top:6px">Catatan QA: ${e(publication.note)}</p>`:""}</div></div>`;
 return pageHeader(order?"Hasil proyek":"Survey atlas",title,geo?"Jelajahi titik, mahkota, batas blok dan cakupan.":"Jelajahi hasil tile dalam koordinat piksel.",order?linkBtn("Detail pesanan",detailHref(order.id),"secondary","left"):linkBtn("Ajukan pemetaan",orderHref(),"primary","arrow"))+
 note+`<div class="map-page"><div class="map-metrics">${metric("Pohon terdeteksi",n(rep.trees),"Hasil model","tree")}${metric(rep.crowns?"Mahkota":"Jumlah mask",n(rep.crowns),rep.crowns?"Kontur estimasi":"Tidak diprediksi profil ini","layers")}${third}</div><div class="map-layout"><div class="map-surface" id="map-surface"><div class="map-toolbar"><span>${icon("map")} ${geo?"Peta hasil · "+e(rep.crs):"Tile hasil model"}</span><div class="button-row"><button class="btn icon-btn secondary" data-map-action="zoomout" title="Perkecil" aria-label="Perkecil peta">${icon("minus")}</button><button class="btn icon-btn secondary" data-map-action="zoomin" title="Perbesar" aria-label="Perbesar peta">${icon("plus")}</button><button class="btn icon-btn secondary" data-map-action="reset" title="Kembali ke seluruh area" aria-label="Atur ulang peta">${icon("crosshair")}</button><button class="btn icon-btn secondary" data-map-action="fullscreen" title="Perbesar tampilan peta" aria-label="Tampilan layar penuh">${icon("fullscreen")}</button></div></div><div class="map-canvas-container" id="map-container"><canvas id="result-canvas" tabindex="0" role="img" aria-label="Peta hasil analisis. Gunakan tombol zoom, pan, atau pencarian ID untuk meninjau titik."></canvas><div id="map-cover" class="map-cover" role="status"><span class="spinner"></span> Memuat citra dan layer</div><div class="map-scale glass" id="map-zoom">1×</div><div class="map-legend glass" id="map-legend"></div></div><div class="table-footer"><span id="map-coords">Pilih titik untuk detail</span><span>Seret · Zoom + / −</span></div></div>
 <aside class="map-sidebar"><section class="panel pad"><h2>Layer peta</h2><div class="layer-list">${options}</div>${geo&&rep.blocks.length?`<div class="subsection-line"><label class="field"><span>Fokus blok</span><select id="block-focus"><option value="">Semua blok</option>${rep.blocks.map((b,i)=>`<option value="${i}">${e(b.block)}</option>`).join("")}</select></label></div>`:""}</section>
 <section class="panel pad"><h2>Detail objek</h2><form class="action-form" id="tree-search-form"><label class="field"><span>Cari ID pohon</span><input id="tree-search-input" placeholder="ID titik pada peta" maxlength="30"></label><button class="btn secondary block" type="submit">${icon("search")} Cari titik</button></form><div class="selected-object subsection-line" id="selected-object">Klik titik atau petak pada peta untuk melihat detail.</div></section>
 <section class="panel pad"><h2>Unduh hasil</h2><div class="download-list">${downloads||'<p class="help">Tidak ada berkas yang tersedia.</p>'}</div></section></aside></div>${blocks}<div class="map-detail-grid">${countBars}${metadata}</div></div>`;
}
async function showResult(seq,oid=null,jid=null){
 const publicView=!oid;
 const base=publicView?"/api/example":"/api/orders/"+oid+"/results/"+jid;
 const payload=await api(base+(publicView?"/report":""));
 const order=publicView?null:await api("/api/orders/"+oid);
 if(seq!==state.route)return;
 const report=publicView?payload:payload.report;
 const html=reportPage(report,base,publicView?"Peta contoh penelitian":order.title,{order,publication:payload.publication,draft:!publicView&&!payload.publication});
 if(publicView)mount(`<div class="landing page-enter">${publicHeader()}<main id="main-content" class="wrap map-standalone">${html}</main>${publicFooter()}</div>`);
 else appMount("Hasil pemetaan",html,"/app/results");
 try{
  const map=await api(base+"/files/"+report.map_file);
  if(seq!==state.route)return;
  state.map=new window.CrownTraceMap({report,map,base,escape:e,number:n,visualLabels,icon,toast});
  await state.map.init();
  if(seq!==state.route)return;
  if(report.review_tiers){
   const tri=document.createElement("section");tri.className="panel pad";
   const tier=report.review_tiers;
   tri.innerHTML='<h2>Prioritas inspeksi</h2><div class="ops-kpi">'+
     [["Prioritas tinggi",tier.high_priority||0],["Perlu review",tier.needs_review||0],["Tidak ditandai",tier.not_flagged||0]].map(([k,v])=>'<div class="metric-card"><small>'+e(k)+'</small><strong>'+n(v)+'</strong></div>').join("")+
     '</div><p class="help">Dua ambang yang sudah dipilih pada validasi; tidak ditandai bukan konfirmasi sehat. Periksa di lapangan.</p>';
   document.querySelector(".map-layout").before(tri);
  }
  if(!publicView){
   const qc=document.createElement("section");qc.className="panel qc-panel";qc.id="qc-workbench";
   qc.innerHTML=loading("Memuat pemeriksaan objek");
   document.querySelector(".map-layout").before(qc);
   try{const info=await api(base+"/quality");if(seq===state.route)window.CrownTraceOps.qualityPanel(qc,state.map,info,base,api,toast);}
   catch(err){if(seq===state.route)qc.textContent="Pemeriksaan objek belum dapat dimuat: "+err.message;}
  }
 }catch(err){if(seq===state.route){$("map-cover").innerHTML=`${icon("alert")}<span>${e(err.message)}</span>${btn("Muat ulang","refresh-page","secondary","refresh")}`;}}
}
async function renderRoute(){
 const seq=++state.route;clearAsync();state.detail=null;
 const path=location.pathname.replace(/\/+$/,"")||"/";
 const params=new URLSearchParams(location.search);
 if(path==="/"){
  mount(landing());document.title="CrownTrace — Pemetaan hingga tiap tajuk";
  requestAnimationFrame(()=>{if(location.hash){const el=$(decodeURIComponent(location.hash.slice(1)));if(el)el.scrollIntoView({behavior:"smooth"});}else window.scrollTo(0,0);});
  animateReveals();return;
 }
 if(path==="/masuk"||path==="/daftar"||path==="/pesan"){
  const requested=path==="/pesan";
  const next=requested?"/app/new"+(params.get("service")&&serviceMeta[params.get("service")]?"?service="+params.get("service"):""):safeNext(params.get("next")||"/app");
  if(state.user&&requested){navigate(state.user.role==="customer"?next:"/app",true);return;}
  if(state.user){navigate(state.user.role==="customer"?next:"/app",true);return;}
  mount(authPage(path==="/daftar",next,requested));window.scrollTo(0,0);document.title="Masuk · CrownTrace";return;
 }
 if(path==="/contoh"){
  mount(`<div class="landing">${publicHeader()}<main class="wrap map-standalone" id="main-content">${loading("Memuat peta contoh")}</main></div>`);
  document.title="Contoh hasil · CrownTrace";window.scrollTo(0,0);
  try{await showResult(seq);}catch(err){if(seq===state.route)mount(`${publicHeader()}<main class="wrap map-standalone" id="main-content">${empty("Peta contoh belum tersedia",err.message,btn("Coba lagi","refresh-page","secondary","refresh"))}</main>`);}
  return;
 }
 if(!path.startsWith("/app")){navigate("/",true);return;}
 if(!state.user){navigate(authHref(path+location.search),true);return;}
 document.title="Workspace · CrownTrace";window.scrollTo(0,0);
 const role=state.user.role;
 if(path==="/app/new"){
  if(role!=="customer"){navigate("/app",true);return;}
  newWizard(params.get("service"));appMount("Buat pesanan",wizardPage(),"/app/new");return;
 }
 if(path==="/app/monitoring"||path==="/app/updates"){
  const monitoring=path==="/app/monitoring",title=monitoring?"Monitoring survei":"Pembaruan";
  appMount(title,`<div class="workspace-header"><div><h1>${title}</h1><p>${monitoring?"Bandingkan inventaris dari dua tanggal survei":"Pantau perubahan status dan rilis pekerjaan"}</p></div></div><div class="stack" id="ops-host"><section class="panel pad">Memuat…</section></div>`,path);
  window.CrownTraceOps[monitoring?"monitoring":"updates"]($("ops-host"),api,toast);return;
 }
 if(path==="/app/account"){appMount("Akun saya",accountPage(),"/app/account");return;}
 if(path==="/app/staff"){
  if(role!=="admin"){navigate("/app",true);return;}
  appMount("Tim & operator",loading(),"/app/staff");
  try{const res=await api("/api/staff");if(seq!==state.route)return;state.staff=res.staff;appMount("Tim & operator",staffPage(),"/app/staff");}
  catch(err){routeError(err,seq);}return;
 }
 const result=path.match(/^\/app\/order\/([a-f0-9]{32})\/result\/([a-f0-9]{32})$/);
 if(result){appMount("Hasil pemetaan",loading("Memuat peta hasil"),"/app/results");try{await showResult(seq,result[1],result[2]);}catch(err){routeError(err,seq);}return;}
 const detail=path.match(/^\/app\/order\/([a-f0-9]{32})$/);
 if(detail){
  appMount("Detail pesanan",loading("Memuat detail pesanan"),"/app/orders");
  try{
   const [o,staff]=await Promise.all([api("/api/orders/"+detail[1]),role==="admin"?api("/api/staff"):Promise.resolve({staff:[]})]);
   if(seq!==state.route)return;state.staff=staff.staff;renderDetail(o);
  }catch(err){routeError(err,seq);}return;
 }
 if(["/app","/app/orders","/app/results"].includes(path)){
  appMount("Ringkasan",loading("Memuat ruang kerja"),path);
  try{const res=await api("/api/orders");if(seq!==state.route)return;state.orders=res.orders;
   if(path==="/app/orders"){const p=params.get("status");state.filter=p==="work"||p==="released"||state.config.status[p]?p:"all";state.search="";appMount("Pesanan",ordersPage(),path);}
   else if(path==="/app/results")appMount("Hasil pemetaan",resultsPage(),path);
   else appMount("Ringkasan",dashboard(),path);
  }catch(err){routeError(err,seq);}return;
 }
 navigate("/app",true);
}
function routeError(err,seq){
 if(seq!==state.route)return;
 if(err.status===401){state.user=null;state.csrf=null;navigate(authHref(location.pathname+location.search),true);return;}
 appMount("Ruang kerja",empty("Data tidak dapat dibuka",err.message,linkBtn("Kembali ke workspace","/app","secondary","left"),"alert"),"/app");
}
function animateReveals(){
 if(!("IntersectionObserver" in window)||matchMedia("(prefers-reduced-motion: reduce)").matches)return;
 const obs=new IntersectionObserver(entries=>entries.forEach(entry=>{if(entry.isIntersecting){entry.target.classList.add("animating");obs.unobserve(entry.target);}}),{threshold:.12});
 document.querySelectorAll(".reveal").forEach(el=>obs.observe(el));
}
function closeMenus(){const shell=document.querySelector(".app-shell");shell?.classList.remove("menu-open");const menu=$("public-menu");if(menu)menu.hidden=true;document.querySelectorAll('[data-action="sidebar-menu"],[data-action="public-menu"]').forEach(el=>el.setAttribute("aria-expanded","false"));}
function setAuth(res,next){state.user=res.user;state.csrf=res.csrf;state.config.user=res.user;navigate(res.user.role==="customer"?safeNext(next):"/app",true);toast("Berhasil masuk ke workspace.");}
const CONFIRMS={
 accept_quote:["Terima penawaran?","Anda menyetujui lingkup dan nominal penawaran. Pengerjaan menunggu verifikasi pembayaran.","Terima penawaran"],
 approve_payment:["Verifikasi pembayaran?","Status berubah menjadi pembayaran terverifikasi. Lakukan hanya setelah pemeriksaan yang benar-benar diperlukan.","Verifikasi"],
 publish:["Rilis hasil ke pelanggan?","Versi hasil ini akan tersedia bagi pelanggan yang memiliki pesanan. Pastikan semua pemeriksaan telah selesai.","Rilis hasil"],
 complete:["Terima hasil pemetaan?","Pesanan menjadi selesai. Berkas hasil tetap dapat diakses.","Terima hasil"],
 cancel:["Batalkan pesanan?","Pembatalan hanya sebelum verifikasi pembayaran dan bukan pengembalian dana otomatis.","Batalkan"],
 start_job:["Mulai job pemetaan?","Sumber dan profil yang telah divalidasi akan diproses oleh model lokal. Hasil tetap draft sampai admin merilisnya.","Mulai analisis"],
 request_revision:["Kirim revisi?","Versi yang telah dirilis tetap dapat dibuka selama tim menyiapkan koreksi.","Kirim revisi"],
 rework:["Kembalikan draft?","Tim dapat menjalankan analisis ulang. Draft belum dibuka untuk pelanggan.","Kembalikan"]
};
async function performOrderAction(action,body={}){
 if(!state.detail)throw new Error("Pesanan belum dimuat.");
 const id=state.detail.id;const confirm=CONFIRMS[action];if(confirm&&!(await confirmAction(...confirm)))return false;
 const res=await api("/api/orders/"+id+"/actions/"+action,{method:"POST",body:{...body,version:state.detail.version}});
 if(location.pathname.endsWith("/order/"+id)){renderDetail(res);toast(action==="start_job"?"Job masuk antrean.":"Perubahan tersimpan.");}
 return true;
}
function uploadFile(form){
 const file=form.elements.upload_file.files[0];if(!file)throw new Error("Pilih berkas terlebih dahulu.");
 const order=state.detail,kind=form.dataset.kind,params=new URLSearchParams({kind,version:String(order.version),name:file.name});
 const status=form.querySelector(".upload-status");status.hidden=false;
 return new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();xhr.open("POST","/api/orders/"+order.id+"/files?"+params);xhr.setRequestHeader("X-CSRF-Token",state.csrf);xhr.setRequestHeader("Content-Type",file.type||"application/octet-stream");xhr.responseType="json";
  xhr.upload.addEventListener("progress",ev=>{if(ev.lengthComputable){const pct=ev.loaded/ev.total*100;status.querySelector("i").style.width=pct+"%";status.querySelector(".upload-label").textContent="Mengunggah "+Math.round(pct)+"%";}});
  xhr.upload.addEventListener("load",()=>{status.querySelector(".upload-label").textContent="Memeriksa berkas…";});
  xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300){resolve(xhr.response);}else{const error=new Error(xhr.response?.detail||"Unggahan gagal.");error.status=xhr.status;reject(error);}};
  xhr.onerror=()=>reject(new Error("Koneksi unggah terputus."));xhr.send(file);
 });
}
document.addEventListener("submit",ev=>{
 const form=ev.target;
 if(form.id==="tree-search-form"){ev.preventDefault();state.map?.seek($("tree-search-input").value);return;}
 if(!form.dataset.form)return;
 ev.preventDefault();
 formWork(form,async()=>{
  const kind=form.dataset.form;const body=Object.fromEntries(new FormData(form));
  if(kind==="login"||kind==="register"){
   if(kind==="register"&&body.password!==body.confirm_password)throw new Error("Konfirmasi kata sandi tidak sama.");
   const res=await api("/api/"+kind,{method:"POST",body});setAuth(res,form.dataset.next);return;
  }
  if(kind==="wizard"){
   captureWizard(form);
   if(state.wizard.step<2){state.wizard.step++;appMount("Buat pesanan",wizardPage(),"/app/new");window.scrollTo(0,0);return;}
   if(!body.acknowledge)throw new Error("Konfirmasi pemahaman sebelum mengajukan.");
   const o=await api("/api/orders",{method:"POST",body:state.wizard.values});state.wizard=null;navigate(detailHref(o.id));toast("Permintaan pemetaan berhasil diajukan.");return;
  }
  if(kind==="upload"){
   const detail=await uploadFile(form);renderDetail(detail);toast("Berkas berhasil diunggah.");return;
  }
  if(kind==="order-action"){
   const action=form.dataset.orderAction;
   if(action==="publish")body.checklist=Object.fromEntries(["source_checked","outputs_checked","scope_checked","review_checked"].map(k=>[k,body[k]==="on"]));
   if(action==="validate_data")body.acknowledge=body.acknowledge==="on";
   if(action==="approve_payment")body.verified=body.verified==="on";
   await performOrderAction(action,body);return;
  }
  if(kind==="staff"){
   await api("/api/staff",{method:"POST",body});state.staff=(await api("/api/staff")).staff;appMount("Tim & operator",staffPage(),"/app/staff");toast("Akun operator berhasil dibuat.");return;
  }
  if(kind==="change-password"){
   if(body.password!==body.confirm_password)throw new Error("Konfirmasi kata sandi tidak sama.");
   await api("/api/account/password",{method:"POST",body});state.user=null;state.csrf=null;navigate("/masuk",true);toast("Kata sandi diperbarui. Silakan masuk lagi.");
  }
 });
});
document.addEventListener("click",async ev=>{
 const link=ev.target.closest("a[data-nav]");
 if(link&&ev.button===0&&!ev.metaKey&&!ev.ctrlKey&&!ev.shiftKey&&!ev.altKey){
  const target=new URL(link.href,location.href);
  if(target.origin===location.origin){ev.preventDefault();closeMenus();navigate(target.pathname+target.search+target.hash);return;}
 }
 const atlasButton=ev.target.closest("[data-atlas]");
 if(atlasButton){const key=atlasButton.dataset.atlas;const scene=$("atlas-scene");if(scene){scene.dataset.show=key;scene.querySelectorAll("[data-atlas]").forEach(el=>el.setAttribute("aria-pressed",String(el===atlasButton)));const chip=scene.querySelector(".atlas-chip");chip.innerHTML=icon(key==="image"?"image":"layers","sm")+(key==="image"?" Citra udara":" Layer mahkota");}return;}
 const demo=ev.target.closest("[data-demo-role]");
 if(demo){
  if(state.busy||!state.config.demo)return;state.busy=true;const prev=demo.innerHTML;demo.disabled=true;demo.innerHTML='<i class="spinner"></i> Masuk';
  try{const res=await api("/api/demo-login",{method:"POST",body:{role:demo.dataset.demoRole}});setAuth(res,demo.dataset.next);}
  catch(err){toast(err.message,true);if(demo.isConnected){demo.disabled=false;demo.innerHTML=prev;}}finally{state.busy=false;}return;
 }
 const block=ev.target.closest("[data-block-focus]");if(block){state.map?.focusBlock(Number(block.dataset.blockFocus));$("map-surface")?.scrollIntoView({behavior:"smooth",block:"start"});return;}
 const mapBtn=ev.target.closest("[data-map-action]");if(mapBtn){state.map?.action(mapBtn.dataset.mapAction);return;}
 const control=ev.target.closest("[data-action]");if(!control)return;const action=control.dataset.action;
 if(action==="public-menu"){const menu=$("public-menu");menu.hidden=!menu.hidden;control.setAttribute("aria-expanded",String(!menu.hidden));return;}
 if(action==="sidebar-menu"){const shell=document.querySelector(".app-shell");const shown=shell.classList.toggle("menu-open");control.setAttribute("aria-expanded",String(shown));return;}
 if(action==="close-menu"){closeMenus();return;}
 if(action==="toggle-password"){const input=control.parentElement.querySelector("input");const show=input.type==="password";input.type=show?"text":"password";control.innerHTML=icon(show?"eyeoff":"eye");control.setAttribute("aria-label",show?"Sembunyikan kata sandi":"Tampilkan kata sandi");return;}
 if(action==="wizard-back"){captureWizard($("wizard-form"));state.wizard.step=Math.max(0,state.wizard.step-1);appMount("Buat pesanan",wizardPage(),"/app/new");window.scrollTo(0,0);return;}
 if(action==="refresh-page"){renderRoute();return;}
 if(action==="refresh-orders"){renderRoute();return;}
 if(action==="refresh-detail"){try{renderDetail(await api("/api/orders/"+state.detail.id),{preserve:true});toast("Detail diperbarui.");}catch(err){toast(err.message,true);}return;}
 if(state.busy)return;
 state.busy=true;const old=control.innerHTML;control.disabled=true;control.innerHTML='<i class="spinner"></i> Memproses';
 try{
  if(action==="logout"){await api("/api/logout",{method:"POST",body:{}});state.user=null;state.csrf=null;navigate("/",true);toast("Anda telah keluar.");}
  else if(["accept_quote","complete","start_job","use_sample"].includes(action))await performOrderAction(action);
 }catch(err){toast(err.message,true);if(err.status===401){state.user=null;state.csrf=null;navigate(authHref(location.pathname),true);}}
 finally{state.busy=false;if(control.isConnected){control.disabled=false;control.innerHTML=old;}}
});
document.addEventListener("change",ev=>{
 const el=ev.target;
 if(el.id==="order-filter"){state.filter=el.value;updateRows();return;}
 if(el.dataset.layer){state.map?.setLayer(el.dataset.layer,el.checked);return;}
 if(el.id==="block-focus"){if(el.value==="")state.map?.action("reset");else state.map?.focusBlock(Number(el.value));return;}
 if(el.name==="profile"&&$("profile-note")){$("profile-note").textContent=state.config.profiles[el.value]?.note||"";return;}
 if(el.closest('#wizard-form')&&el.name==="service"&&el.checked){
  captureWizard($("wizard-form"));state.wizard.values.service=el.value;
  if(el.value==="inspection")state.wizard.values.data_plan="tile";
  appMount("Buat pesanan",wizardPage(),"/app/new");document.querySelector('input[name="service"]:checked')?.focus();return;
 }
});
document.addEventListener("input",ev=>{if(ev.target.id==="order-search"){state.search=ev.target.value;updateRows();}});
document.addEventListener("keydown",ev=>{if(ev.key==="Escape")closeMenus();});
window.addEventListener("popstate",renderRoute);
async function start(){
 try{
  const config=await api("/api/bootstrap");state.config=config;state.user=config.user;state.csrf=config.csrf;
  $("boot").classList.add("done");await renderRoute();
 }catch(err){
  $("boot").classList.add("done");
  mount(`<main class="auth-page" id="main-content">${empty("CrownTrace belum dapat dimuat",err.message,'<button class="btn primary" id="retry-boot">Coba lagi</button>',"alert")}</main>`);
  $("retry-boot").onclick=start;
 }
}
start();
})();

