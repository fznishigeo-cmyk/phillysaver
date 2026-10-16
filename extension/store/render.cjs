/* Developer-only local renderer; no npm/runtime dependencies in the extension. */
"use strict";
const {chromium} = require("playwright");
const http = require("node:http"), fs = require("node:fs"), path = require("node:path");
const scratch = process.argv[2];
if (!scratch) throw Error("Pass a scratch directory");
const root = path.resolve(__dirname, "../..");
const server = http.createServer((req, res) => {
  const file = path.resolve(root, "." + new URL(req.url, "http://localhost").pathname);
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) {res.writeHead(404).end(); return;}
  res.setHeader("Content-Type", {".html":"text/html", ".js":"application/javascript", ".png":"image/png"}[path.extname(file)] || "text/plain");
  res.end(fs.readFileSync(file));
});
(async () => {
  let browser;
  try {
    fs.mkdirSync(scratch, {recursive:true});
    browser = await chromium.launchPersistentContext(scratch, {executablePath:"/opt/pw-browsers/chromium", headless:true, args:["--headless=new", "--no-sandbox"], viewport:{width:1280,height:800}});
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    const origin = "http://127.0.0.1:" + server.address().port;
    await browser.route("**/*", route => route.request().url().startsWith(origin + "/") ? route.continue() : route.abort());
    const page = await browser.newPage();
    for (const [name, query] of [["price-check", ""], ["amazon-check", "?amazon=1"]]) {
      await page.goto(origin + "/extension/store/fixture.html" + query);
      await page.screenshot({path:path.join(__dirname, "screenshots", name + ".png")});
    }
    await page.setViewportSize({width:440,height:280});
    await page.goto(origin + "/extension/store/promo.html");
    await page.screenshot({path:path.join(__dirname, "screenshots", "promo-440x280.png")});
    console.log("Rendered 2 local example screenshots, 1280x800, and the 440x280 promo tile.");
  } finally {if (browser) await browser.close(); server.close();}
})().catch(error => {console.error(error.message.slice(-900)); process.exitCode = 1;});
