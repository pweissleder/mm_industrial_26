// User view vs developer view in first person: walking controls only for developers.
//   PW_MODULE=<playwright-core> ARINAV_URL=http://localhost:4321 node scripts/e2e/mode_check.mjs <screenshot dir>
import { createRequire } from 'node:module';
import { login, logout } from './login.mjs';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE);
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await page.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
await page.goto(BASE + '?profile=immersive', { waitUntil: 'networkidle' });
await login(page);
await page.addStyleTag({ content: 'astro-dev-toolbar{display:none!important}' });
await page.selectOption('#building-select', 'proc-factory-dev-003');
await page.waitForTimeout(1500);
const modeBtn = (n) => page.getByRole('group', { name: 'Mode' }).getByRole('button', { name: n });
await modeBtn('Developer').click();
await page.getByRole('tab', { name: 'Developer' }).click();
const box = page.getByLabel('Search endpoints');
await page.getByRole('group', { name: 'Search kind' }).getByRole('button', { name: 'Rooms', exact: true }).click();
await box.fill('Staircase 0.17');
await page.locator('.dev-result').first().waitFor();
await page.locator('.dev-result').first().getByRole('button', { name: 'Start', exact: true }).click();
await page.waitForTimeout(500);
const out = {};
for (const m of ['Assistant', 'Developer']) {
  await modeBtn(m).click();
  await page.waitForTimeout(400);
  if (await page.getByRole('button', { name: /Walking yourself/ }).count()) await page.getByRole('button', { name: /Walking yourself/ }).click();
  await page.getByRole('button', { name: 'Overview' }).click();
  await page.getByRole('button', { name: 'First person' }).click();
  await page.waitForTimeout(500);
  const walk = await page.getByRole('button', { name: /Walk myself/ }).count();
  if (walk) await page.getByRole('button', { name: /Walk myself/ }).click();
  await page.waitForTimeout(500);
  const before = await page.evaluate(() => window.__arinavImmersive.pose());
  await page.mouse.move(800, 450);
  await page.keyboard.down('w');
  await page.waitForTimeout(600);
  await page.keyboard.up('w');
  const after = await page.evaluate(() => window.__arinavImmersive.pose());
  out[m] = {
    walk_button: walk,
    speed_slider: await page.getByLabel('Walking speed in metres per second').count(),
    wasd_help: await page.locator('.pov-hud__help').count(),
    ride_prompt: await page.locator('.pov-hud__ride').count(),
    ride_chips: await page.locator('.immersive-bar').getByRole('button', { name: /^(Stairs|Lift) to/ }).count(),
    moved_m: before && after ? +Math.hypot(after.position[0] - before.position[0], after.position[1] - before.position[1]).toFixed(2) : null,
    floor: after?.floor_id ?? null,
  };
  await page.screenshot({ path: process.argv[2] + `/mode-${m}.png` });
}
console.log(JSON.stringify(out));
await logout(page);
await browser.close();
