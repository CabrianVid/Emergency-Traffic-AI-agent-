// Photograph the actual dispatcher application against frozen simulation data.
// All requests are fulfilled locally. The production frontend is bundled as-is.
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright-core');
const esbuild = require('esbuild');
const http = require('node:http');
const {ensureBrowser} = require('@remotion/renderer');
const video = path.resolve(__dirname, '..');
const app = path.resolve(video, '../frontend');
const work = path.join(video, '.capture');
const plates = path.join(video, 'public/plates');
const read = (name) => JSON.parse(fs.readFileSync(path.join(work, name + '.json')));

async function main() {
  fs.mkdirSync(plates, {recursive: true});
  const bundle = await esbuild.build({
    entryPoints: [path.join(app, 'src/main.tsx')], bundle: true, write: false,
    outfile: 'desk.js', jsx: 'automatic', nodePaths: [path.join(video, 'node_modules')],
    define: {'process.env.NODE_ENV': '"production"'},
  });
  const binary = await ensureBrowser();
  const browser = await chromium.launch({executablePath: binary.path, headless: true});
  let server;
  try {
    for (const name of ['city', 'decision']) {
      const state = read(name);
      const geometry = read(name === 'city' ? 'city-geometry' : 'grid-geometry');
      const page = await browser.newPage({viewport: {width: 1440, height: 810}, deviceScaleFactor: 1.5, timezoneId: 'UTC'});
      page.on('pageerror', err => console.error(err));
      await page.clock.setFixedTime(new Date('2026-11-22T08:12:00Z'));
      await page.addInitScript(({id}) => {
        sessionStorage.setItem('greeted', id);
        localStorage.setItem('left', '302');
        localStorage.setItem('right', '386');
        localStorage.setItem('dock-height', '173');
        localStorage.setItem('unit-height', '223');
        localStorage.setItem('satellite', 'true');
      }, {id: state.run_id});
      await page.routeWebSocket('**/ws', ws => ws.send(JSON.stringify(state)));
      server = http.createServer((req,res) => {
        const pathname = new URL(req.url,'http://localhost').pathname;
        const send=(mime,body)=>{res.setHeader('Content-Type',mime); res.end(body);};
        if (pathname === '/') return send('text/html','<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/desk.css"><div id="root"></div><script src="/desk.js"></script>');
        if (pathname === '/api/state') return send('application/json',JSON.stringify(state));
        if (pathname === '/api/network') return send('application/json',JSON.stringify(geometry));
        if (pathname === '/ljubljana-aerial.jpg') return send('image/jpeg',fs.readFileSync(path.join(app,'public/ljubljana-aerial.jpg')));
        const file = bundle.outputFiles.find(f => f.path.endsWith(pathname.slice(1)));
        if (file) return send(pathname.endsWith('css')?'text/css':'text/javascript',file.contents);
        res.statusCode=404; res.end();
      });
      await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
      await page.goto('http://127.0.0.1:'+server.address().port);
      await page.locator('.unit .callsign').first().waitFor();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({path:path.join(plates,name+'.png'), animations:'disabled'});
      if (name === 'decision') {
        const radios = page.getByRole('radio');
        await radios.nth(1).click();
        await page.screenshot({path:path.join(plates,'alternative.png'),animations:'disabled'});
        await radios.first().click();
        const rect = await page.locator('.decide .approve').boundingBox();
        fs.writeFileSync(path.join(video,'data/button.json'),JSON.stringify(rect));
      }
      await page.close();
      server.close();
    }
  } finally { server?.close(); await browser.close(); }
}
main().catch(err => {console.error(err); process.exitCode=1});
