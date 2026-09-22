// node fetch_ad.js <url> <prefix>  -> Volltext + Fotos einer Anzeige nach check/
const {chromium}=require('playwright');const fs=require('fs');
(async()=>{const [url,pre]=process.argv.slice(2);const b=await chromium.launch();const ctx=await b.newContext({locale:'de-DE',userAgent:'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36',viewport:{width:1300,height:1800}});
const p=await ctx.newPage();await p.goto(url,{waitUntil:'domcontentloaded',timeout:45000});await p.waitForTimeout(5000);
for(const t of ['Alle akzeptieren','Akzeptieren','Zustimmen']){const x=p.getByRole('button',{name:t}).first();if(await x.count()){await x.click().catch(()=>{});break}}
await p.waitForTimeout(1500);
const txt=(await p.innerText('main').catch(()=>p.innerText('body'))).slice(0,3000);console.log(txt);
const imgs=await p.evaluate(()=>[...new Set([...document.querySelectorAll('img')].filter(i=>i.naturalWidth>250).map(i=>i.currentSrc||i.src))]);
fs.mkdirSync('check',{recursive:true});let n=0;
for(const i of imgs.slice(0,5)){try{const r=await ctx.request.get(i);fs.writeFileSync(`check/${pre}_${n++}.jpg`,await r.body())}catch{}}
await p.screenshot({path:`check/${pre}_page.png`});console.log('IMGS',n);await b.close()})();
