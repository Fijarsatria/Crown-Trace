"use strict";
const $=id=>document.getElementById(id), svgNS="http://www.w3.org/2000/svg";
let ready=false, blob=null, label="", imageUrl=null, result=null, busy=false;
const message=text=>{$("message").textContent=text;};
function buttons(){$("inspect").disabled=!ready||!blob||busy;$("example").disabled=!ready||busy||$("example").dataset.available!=="true";$("tile").disabled=busy;}
function clearResults(){result=null;$("boxes").replaceChildren();$("boxes").setAttribute("hidden","");$("only").disabled=true;$("only").checked=false;$("records").replaceChildren();for(const id of ["total","candidates","notflagged"])$(id).textContent="—";$("exports").hidden=true;}
function setTile(file,name){
 if(imageUrl)URL.revokeObjectURL(imageUrl);
 blob=file;label=name;imageUrl=URL.createObjectURL(blob);$("image").src=imageUrl;$("image").hidden=false;$("empty").hidden=true;
 $("filename").textContent=label;$("viewname").textContent=label;$("viewmeta").textContent="Tile siap diperiksa";clearResults();buttons();
}
function show(){
 if(!result)return;
 const records=result.records.filter(r=>!$("only").checked||r.review_candidate);
 $("boxes").replaceChildren();$("records").replaceChildren();$("boxes").removeAttribute("hidden");
 for(const row of records){
  const [x,y,r,b]=row.bbox_px,c=row.review_candidate?"#ff77b8":"#89f0ab";
  const rect=document.createElementNS(svgNS,"rect");
  for(const [key,value] of Object.entries({x,y,width:r-x,height:b-y,stroke:c,"stroke-width":3,fill:"none"}))rect.setAttribute(key,value);
  const text=document.createElementNS(svgNS,"text");text.setAttribute("x",x+4);text.setAttribute("y",Math.max(14,y+14));text.setAttribute("fill",c);text.setAttribute("font-weight",700);text.setAttribute("font-size",13);text.setAttribute("paint-order","stroke");text.setAttribute("stroke","#102219");text.setAttribute("stroke-width",2);text.textContent=row.id;
  $("boxes").append(rect,text);
  const tr=document.createElement("tr");tr.dataset.flag=String(row.review_candidate);
  for(const v of [row.id,row.review_candidate?"Kandidat":"Tidak ditandai",row.detector_score.toFixed(3),row.review_score.toFixed(3),row.center_px.map(v=>v.toFixed(1)).join(", ")]){
   const td=document.createElement("td");td.textContent=v;tr.append(td);}
  $("records").append(tr);
 }
}
function download(name,text,mime){
 const url=URL.createObjectURL(new Blob([text],{type:mime})),a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$("tile").addEventListener("change",()=>{
 const file=$("tile").files[0];if(!file)return;
 if(file.size>5*1024*1024||!["image/jpeg","image/png"].includes(file.type)){message("Gunakan JPEG/PNG hingga 5 MiB.");return;}
 setTile(file,file.name);message("Tile dipilih.");
});
$("example").addEventListener("click",async()=>{
 try{busy=true;buttons();message("Mengambil satu tile test sumber.");const response=await fetch("/api/inspection/sample-image");if(!response.ok)throw Error("Citra uji tidak tersedia.");setTile(await response.blob(),"Ganoderma V2 · tile uji");message("Citra uji tersedia. Pilih mode dan periksa.");}
 catch(error){message(error.message);}finally{busy=false;buttons();}
});
$("inspect").addEventListener("click",async()=>{
 if(!blob||busy)return;
 try{busy=true;buttons();clearResults();message("Menjalankan model lokal…");
  const mode=document.querySelector('input[name="mode"]:checked').value;
  const response=await fetch("/api/inspection?"+new URLSearchParams({mode,filename:label}),{method:"POST",headers:{"Content-Type":blob.type||"application/octet-stream"},body:blob});
  const payload=await response.json();if(!response.ok)throw Error(payload.detail||"Pemeriksaan gagal.");
  result=payload;$("total").textContent=result.detections;$("candidates").textContent=result.review_candidates;$("notflagged").textContent=result.detections-result.review_candidates;
  $("viewmeta").textContent=(result.mode==="balanced"?"Balanced":"High Recall")+" · Ambang "+result.review_cutoff+" · hasil pilot";
  $("only").disabled=false;$("exports").hidden=false;show();message("Pemeriksaan selesai. Kandidat perlu tinjauan lapangan.");
 }catch(error){message(error.message);}finally{busy=false;buttons();}
});
$("only").addEventListener("change",show);
$("json").addEventListener("click",()=>{if(result)download("inspeksi_crowntrace.json",JSON.stringify(result,null,2),"application/json");});
$("csv").addEventListener("click",()=>{
 if(!result)return;
 const rows=[["id","candidate_review","detector_score","review_score","center_x_px","center_y_px","left_px","top_px","right_px","bottom_px"]];
 for(const r of result.records)rows.push([r.id,r.review_candidate,r.detector_score,r.review_score,...r.center_px,...r.bbox_px]);
 download("inspeksi_crowntrace.csv",rows.map(r=>r.join(",")).join("\r\n"),"text/csv");
});
(async()=>{try{const res=await fetch("/api/inspection/status");const data=await res.json();ready=Boolean(data.model_ready);$("example").dataset.available=String(data.sample_ready);$("state").textContent=ready?"Model inspeksi siap":"Model belum tersedia";$("state").dataset.ready=String(ready);}catch(_){$("state").textContent="Server belum tersedia";}finally{buttons();}})();
