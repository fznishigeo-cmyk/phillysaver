// Genuine screenshots of the publisher's local synthetic site, never retailer pages.
'use strict';
const {chromium}=require('playwright');
const fs=require('node:fs'),http=require('node:http'),path=require('node:path');
const {once}=require('node:events');
const root=path.resolve(process.argv[2]||'');
if(!process.argv[2]||!fs.existsSync(path.join(root,'store-fixture.json')))throw Error('Run build_fixture.py first and pass its output directory');
const output=path.resolve(process.argv[3]||path.join(__dirname,'screenshots'));
const fixture=JSON.parse(fs.readFileSync(path.join(root,'store-fixture.json')));
(async()=>{
  let browser;
  const server=http.createServer((req,res)=>{
    let file=path.resolve(root,'.'+new URL(req.url,'http://localhost').pathname);
    if(file!==root&&!file.startsWith(root+path.sep)){res.writeHead(403).end();return;}
    if(fs.existsSync(file)&&fs.statSync(file).isDirectory())file=path.join(file,'index.html');
    if(!fs.existsSync(file)){res.writeHead(404).end();return;}
    res.setHeader('Content-Type',{'.html':'text/html','.json':'application/json','.js':'application/javascript','.png':'image/png','.webmanifest':'application/manifest+json'}[path.extname(file)]||'text/plain');
    res.end(fs.readFileSync(file));
  });
  try {
    browser=await chromium.launch({executablePath:'/opt/pw-browsers/chromium',headless:true,args:['--no-sandbox']});
    server.listen(0,'127.0.0.1');await once(server,'listening');
    const origin='http://127.0.0.1:'+server.address().port;
    const context=await browser.newContext({viewport:{width:1080,height:1920},deviceScaleFactor:1,colorScheme:'light'});
    await context.route('**/*',r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());
    await context.addInitScript(()=>{
      document.addEventListener('DOMContentLoaded',()=>document.documentElement.classList.add('in-app'),{once:true});
    });
    const page=await context.newPage();
    fs.mkdirSync(output,{recursive:true});
    for(const [name,url] of [
      ['check-hit','/check/?src=twa&u='+encodeURIComponent(fixture.url)],
      ['share-result','/check/?text='+encodeURIComponent('Look '+fixture.url)],
      ['deal-page',fixture.deal],['offline','/offline.html']
    ]) {
      await page.goto(origin+url);
      if(name==='check-hit'||name==='share-result')await page.locator('#check-result .check-actions a').first().waitFor();
      await page.screenshot({path:path.join(output,name+'.png'),fullPage:false});
    }
    console.log('Rendered 4 genuine local screenshots, 1080x1920.');
  } finally {server.close();if(browser)await browser.close();}
})().catch(e=>{console.error(e.message.slice(-1200));process.exitCode=1;});
