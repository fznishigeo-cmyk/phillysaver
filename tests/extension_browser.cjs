/* Real offline MV3 integration check. Exit 77 only for a blocked Chromium launch. */
"use strict";
process.env.PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS = "1";
const {chromium} = require("playwright");
const fs = require("node:fs"), path = require("node:path"), https = require("node:https");
const {execFileSync} = require("node:child_process"), assert = require("node:assert/strict");
const scratch = process.argv[2];
if (!scratch) throw Error("Pass a scratch directory");
const extension = path.resolve(__dirname, "../extension");
const key = "bestbuy:6522738", entry = {kind:"live",store:"bestbuy",item:"Local widget",price:77,at:new Date().toISOString(),verdict:"Exact fixture verdict — unchanged",p_path:"/p/local-widget/",deal_path:"/deal/local-widget/"};
const pageHTML = '<!doctype html><title>Offline product fixture</title><a id="original" href="https://retailer.example.invalid/?tag=original">Buy</a>';
const requests = [];
// A full version-1 fixture; actual Python-encoded Unicode/nested facts are also tested.
function index() {
  const fields = Object.keys(entry).sort(), strings = fields.map(f => JSON.stringify(entry[f]));
  return {version:1,generated_at:new Date().toISOString(),fields,values:strings.map(s=>[0,s]),entries:{[key]:fields.map((_,i)=>i)}};
}
async function waitFor(check, message) {
  for (let i=0;i<120;i++) {if (await check()) return; await new Promise(resolve=>setTimeout(resolve,50));}
  assert.fail(message);
}
(async () => {
  fs.mkdirSync(scratch, {recursive:true});
  let browser, server;
  try {
    try {
      browser = await chromium.launchPersistentContext(path.join(scratch,"profile"), {
        executablePath:"/opt/pw-browsers/chromium", headless:true, ignoreDefaultArgs:["--disable-extensions"],
        args:["--headless=new", "--no-sandbox", "--disable-extensions-except="+extension, "--load-extension="+extension, "--ignore-certificate-errors", "--no-proxy-server", "--host-resolver-rules=MAP www.bestbuy.com 127.0.0.1:18443, MAP wowpriced.com 127.0.0.1:18443"],
        ignoreHTTPSErrors:true
      });
    } catch (error) {
      if (!/Operation not permitted|Permission denied/.test(error.message)) throw error;
      console.log(JSON.stringify({blocked:true,passed:0,failed:0,skipped:1,reason:"Chromium launch blocked by sandbox permissions"}));
      process.exitCode = 77; return;
    }
    execFileSync("openssl", ["req","-x509","-newkey","rsa:2048","-nodes","-keyout",path.join(scratch,"key.pem"),"-out",path.join(scratch,"cert.pem"),"-days","1","-subj","/CN=wowpriced.com"], {stdio:"ignore"});
    server = https.createServer({key:fs.readFileSync(path.join(scratch,"key.pem")),cert:fs.readFileSync(path.join(scratch,"cert.pem"))}, (req,res) => {
      requests.push({host:req.headers.host,path:req.url});
      if (req.headers.host === "www.bestbuy.com" && req.url.startsWith("/site/")) {res.setHeader("Content-Type","text/html"); res.end(pageHTML);}
      else if (req.headers.host === "wowpriced.com" && req.url === "/check/data/all.json") {res.setHeader("Content-Type","application/json");res.end(JSON.stringify(index()));}
      else res.writeHead(204).end();
    });
    await new Promise((resolve,reject) => {server.once("error",reject);server.listen(18443,"127.0.0.1",resolve);});
    const worker = browser.serviceWorkers()[0] || await browser.waitForEvent("serviceworker",{timeout:10000});
    // Installation may run before the local fixture server is ready. Prime its
    // download explicitly; subsequent refresh is driven by a real Chrome alarm.
    await worker.evaluate(() => refreshIndex());
    const stored = await worker.evaluate(() => new Promise(resolve => chrome.storage.local.get("priceIndex", resolve)));
    assert(stored.priceIndex, "initial public index downloaded to local storage");
    assert.equal(await worker.evaluate(() => new Promise(resolve => chrome.alarms.get("price-index", a=>resolve(a.periodInMinutes)))),360);
    assert.equal(await worker.evaluate(() => new Promise(resolve => chrome.storage.sync.get("enabled", value => resolve(value.enabled)))), undefined);
    const page = await browser.newPage(), outgoing = [];
    browser.on("request", request => {if (/^https?:/.test(request.url())) outgoing.push(request.url());});
    const count = requests.filter(r=>r.host==="wowpriced.com").length;
    await page.goto("https://www.bestbuy.com/site/local-widget/6522738.p?skuId=6522738");
    const before = await page.locator("#original").evaluate(el => el.outerHTML);
    const cdp = await browser.newCDPSession(page);
    await cdp.send("Accessibility.enable");
    const hasCard = async () => (await cdp.send("Accessibility.getFullAXTree")).nodes.some(n=>n.name?.value===entry.verdict);
    await waitFor(hasCard,"downloaded local price appears");
    function flatten(node) {return [node,...(node.children||[]).flatMap(flatten),...(node.shadowRoots||[]).flatMap(flatten)];}
    const nodes = flatten((await cdp.send("DOM.getDocument", {depth:-1,pierce:true})).root);
    assert(nodes.some(n => n.nodeName === "A" && (n.attributes||[]).includes("https://wowpriced.com/p/local-widget/?ref=ext")));
    await page.evaluate(() => history.pushState({}, "", "/site/other/1234567.p?skuId=1234567"));
    await waitFor(async()=>!await hasCard(),"SPA miss clears previous product");
    await page.evaluate(() => history.back());
    await waitFor(hasCard,"Back restores the local match");
    assert.equal(requests.filter(r=>r.host==="wowpriced.com").length,count,"page/SPA/Back caused zero extension requests");
    assert(!outgoing.some(address=>address.startsWith("https://wowpriced.com/")),JSON.stringify(outgoing));
    // Make the actual Chrome alarm fire now, retaining the six-hour period.
    await worker.evaluate(() => chrome.alarms.create("price-index",{when:Date.now(),periodInMinutes:360}));
    await waitFor(()=>requests.filter(r=>r.host==="wowpriced.com").length>count,"alarm downloaded a new index");
    await worker.evaluate(() => pending);
    assert(requests.filter(r=>r.host==="wowpriced.com").every(r=>r.path==="/check/data/all.json"));
    await browser.setOffline(true);
    const popup = await browser.newPage();
    await popup.goto("chrome-extension://"+new URL(worker.url()).host+"/popup.html");
    await popup.locator("#product-url").fill("https://www.bestbuy.com/site/widget/6522738.p?skuId=6522738");
    await popup.locator("#check-form button[type=submit]").click();
    await popup.locator("#result").getByText(entry.verdict,{exact:true}).waitFor();
    assert.equal(await page.locator("#original").evaluate(el=>el.outerHTML),before);
    // Advance the worker's clock without touching the price or cache, then
    // navigate client-side: >24h data must be invisible even while offline.
    await worker.evaluate(() => {const realNow=Date.now; Date.now=()=>realNow()+25*60*60*1000;});
    await page.evaluate(() => history.pushState({}, "", "/site/local-widget/6522738.p?skuId=6522738&fresh=1"));
    await waitFor(async()=>!await hasCard(),"stale index shows no card");
    await popup.locator("#check-form button[type=submit]").click();
    await popup.locator("#result").getByText(entry.verdict,{exact:true}).waitFor({state:"detached"});
    assert.equal(requests.filter(r=>r.host==="wowpriced.com").length,count+1,"offline and stale lookups never fetch");
    console.log(JSON.stringify({blocked:false,passed:5,failed:0,skipped:0,checks:["zero page requests","alarm refresh","offline popup","stale hidden","SPA/Back"],requests:requests.length}));
  } finally {if(browser) await browser.close();if(server) await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error.stack.slice(-2200));process.exitCode=1;});
