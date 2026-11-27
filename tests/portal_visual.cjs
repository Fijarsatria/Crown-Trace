const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const {chromium}=require(process.env.PORTAL_PLAYWRIGHT_MODULE||"playwright-core");
const url=process.env.PORTAL_URL||"http://127.0.0.1:8791";
const output=process.env.PORTAL_SCREENSHOTS||path.join(process.cwd(),"results","portal_visual");
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.PORTAL_CHROME||"C:/Program Files/Google/Chrome/Application/chrome.exe",headless:true,
  args:["--disable-background-networking","--disable-component-update","--disable-default-apps","--disable-extensions","--no-first-run"]});
 const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
 await context.route("**/*",route=>route.request().url().startsWith(url+"/")?route.continue():route.abort());
 const page=await context.newPage(),errors=[];page.on("pageerror",err=>errors.push(err.message));
 const capture=async(name,full=false)=>{await page.evaluate(()=>document.fonts.ready);await page.waitForTimeout(230);await page.screenshot({path:path.join(output,name+".png"),fullPage:full,animations:"disabled"});};
 await page.goto(url);await page.getByRole("heading",{name:"Kenali kebun Anda, sampai tiap tajuk."}).waitFor();await page.locator("#boot.done").waitFor({state:"attached"});
 await page.waitForTimeout(400);
 assert(await page.evaluate(()=>document.fonts.check("600 16px Jakarta")));
 await capture("01_Landing_Desktop",true);
 await page.locator('[data-atlas="image"]').click();assert.equal(await page.locator("#atlas-scene").getAttribute("data-show"),"image");
 await page.locator('[data-atlas="crowns"]').click();
 await page.getByRole("link",{name:"Lihat contoh hasil",exact:true}).click();
 await page.locator("#result-canvas").waitFor();await page.locator("#map-cover").waitFor({state:"hidden"});
 await page.locator('[data-layer="density"]').check();await page.waitForTimeout(250);
 await page.locator("#tree-search-input").fill("CT-000011");await page.locator("#tree-search-form button").click();assert.match(await page.locator("#selected-object").textContent(),/CT-000011/);
 await page.locator('[data-map-action="reset"]').click();await page.locator('[data-layer="density"]').uncheck();
 await capture("02_Contoh_Peta");
 await page.goto(url+"/masuk");await page.locator('[data-form="login"]').waitFor();await capture("03_Login");
 await page.locator('[data-demo-role="customer"]').click();await page.getByRole("heading",{name:/Halo, Ari/}).waitFor();await capture("04_Pelanggan");
 await page.getByRole("link",{name:"Buat pesanan",exact:true}).last().click();await page.getByRole("heading",{name:"Ajukan pemetaan"}).waitFor();await capture("05_Form_Pesanan");
 await page.goto(url+"/masuk"); // already logged in, use logout then admin
 await page.locator('[data-action="logout"]').waitFor();await page.locator('[data-action="logout"]').click();await page.getByRole("heading",{name:/Kenali kebun/}).waitFor();
 await page.goto(url+"/masuk");await page.locator('[data-demo-role="admin"]').click();await page.getByRole("heading",{name:"Ringkasan operasi"}).waitFor();await capture("06_Admin");
 await page.getByRole("link",{name:"Kebun uji · permintaan inventaris",exact:true}).click();await page.getByRole("heading",{name:"Siapkan penawaran"}).waitFor();await capture("07_Admin_Penawaran",true);
 await page.goto(url+"/app/results");await page.getByRole("link",{name:"Buka hasil",exact:true}).first().click();await page.locator("#result-canvas").waitFor();await page.locator("#map-cover").waitFor({state:"hidden"});await capture("08_Hasil_Privat");
 await page.setViewportSize({width:390,height:844});await page.goto(url);await page.getByRole("heading",{name:/Kenali kebun/}).waitFor();await capture("09_Landing_Mobile",true);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.goto(url+"/app");await page.getByRole("heading",{name:"Ringkasan operasi"}).waitFor();await capture("10_Admin_Mobile");assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.locator('[data-action="sidebar-menu"]').click();await page.getByRole("link",{name:"Pesanan",exact:true}).waitFor({state:"visible"});await capture("11_Menu_Mobile");
 await page.locator('[data-action="close-menu"]').click();
 assert.deepEqual(errors,[]);await browser.close();console.log("Portal visual and interaction smoke tests passed.");
})().catch(err=>{console.error(err);process.exit(1)});

