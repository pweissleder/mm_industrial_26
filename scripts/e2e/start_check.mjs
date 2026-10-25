// "Set as start" replaces where the person walked to; the route starts there (also the same start again).
//   PW_MODULE=<playwright-core> ARINAV_URL=http://localhost:4321 node scripts/e2e/start_check.mjs
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE);
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await page.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
await page.goto(BASE + '?profile=immersive', { waitUntil: 'networkidle' });
await page.addStyleTag({ content: 'astro-dev-toolbar{display:none!important}' });
await page.selectOption('#building-select', 'proc-factory-dev-003');
await page.waitForTimeout(1500);
await page.getByRole('group', { name: 'Mode' }).getByRole('button', { name: 'Developer' }).click();
await page.getByRole('tab', { name: 'Developer' }).click();
async function endpoint(q, which) {
  const box = page.getByLabel('Search endpoints');
  await box.fill('');
  await page.getByRole('group', { name: 'Search kind' }).getByRole('button', { name: 'Rooms', exact: true }).click();
  await box.fill(q);
  const row = page.locator('.dev-result').first();
  await row.waitFor({ timeout: 15000 });
  await row.getByRole('button', { name: which, exact: true }).click();
}
const routeStartOf = async () => {
  const req = page.waitForRequest((r) => r.url().endsWith('/route') && r.method() === 'POST');
  await page.getByRole('button', { name: 'Compute route' }).click();
  return JSON.parse((await req).postData()).start;
};
const out = {};
await endpoint('Office corridor 0.16', 'Start');
await endpoint('Kitchen 0.28', 'Target');
out.first = await routeStartOf();
// walk yourself somewhere else and stand still (becomes the simulated location)
await page.getByRole('button', { name: 'First person' }).click();
if (await page.getByRole('button', { name: /Walk myself/ }).count()) await page.getByRole('button', { name: /Walk myself/ }).click();
await page.mouse.move(800, 450);
await page.keyboard.down('w');
await page.waitForTimeout(1200);
await page.keyboard.up('w');
await page.waitForTimeout(1500);
out.walked_pose = await page.evaluate(() => window.__arinavImmersive.pose());
out.after_walk = await routeStartOf();
out.start_row_after_walk = (await page.locator('.endpoint').first().innerText()).replace(/\s+/g, ' ');
// the same start again
await endpoint('Office corridor 0.16', 'Start');
await page.waitForTimeout(500);
out.driver_after_set = await page.evaluate(() => window.__arinavImmersive.driver());
out.after_set_again = await routeStartOf();
out.location_chip = (await page.locator('text=/Location:/').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
console.log(JSON.stringify(out, null, 1));
await browser.close();
