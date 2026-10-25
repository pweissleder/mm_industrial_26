// Fly-through phase screenshots (destination, pull-out, drone flight, final overview) for visual
// review; camera only, the walker must stay at 0 m (printed per shot). Pipeline starts are blocked.
//   PW_MODULE=<playwright-core> ARINAV_URL=http://localhost:4321 [FROM=.. TO=.. TO_KIND=Assets]
//   [AT=1200,2700,..] node scripts/e2e/flythrough_shots.mjs <out dir>
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE);
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const OUT = process.argv[2];
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await page.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
await page.goto(BASE + '?profile=immersive', { waitUntil: 'networkidle' });
await page.addStyleTag({ content: 'astro-dev-toolbar{display:none!important}' });
await page.selectOption('#building-select', 'proc-factory-dev-003');
await page.waitForTimeout(1500);
await page.getByRole('group', { name: 'Mode' }).getByRole('button', { name: 'Developer' }).click();
await page.getByRole('tab', { name: 'Developer' }).click();
async function endpoint(q, which, kind) {
  const box = page.getByLabel('Search endpoints');
  await box.fill('');
  await page.getByRole('group', { name: 'Search kind' }).getByRole('button', { name: kind, exact: true }).click();
  await box.fill(q);
  const row = page.locator('.dev-result').first();
  await row.waitFor({ timeout: 15000 });
  await row.getByRole('button', { name: which, exact: true }).click();
}
await endpoint(process.env.FROM || 'Office 0.38', 'Start', 'Rooms');
await endpoint(process.env.TO || 'Kitchen 0.28', 'Target', process.env.TO_KIND || 'Rooms');
await page.getByRole('button', { name: 'Compute route' }).click();
await page.locator('.guidance').waitFor({ timeout: 15000 });
await page.waitForTimeout(1500);
const len = await page.evaluate(() => window.__arinavGuidance.snapshot().total_m);
await page.getByRole('button', { name: 'Fly-through' }).click();
const t0 = Date.now();
const shots = (process.env.AT || '1200,2700,4500,6500,9000,16000').split(',').map(Number);
const states = [];
for (const at of shots) {
  await page.waitForTimeout(Math.max(0, at - (Date.now() - t0)));
  const s = await page.evaluate(() => window.__arinavGuidance.snapshot());
  states.push({ at, status: s.status, d: s.distance_m, skip: await page.getByRole('button', { name: /Skip preview/ }).count() });
  await page.screenshot({ path: join(OUT, `fly-${String(at).padStart(5, '0')}ms.png`) });
}
console.log(JSON.stringify({ route_m: len, states }));
await browser.close();
