// Screenshots of the simulated person (close, mid, far) while it walks a route; visual review only.
//   PW_MODULE=<playwright-core> ARINAV_URL=... node scripts/e2e/walker_shots.mjs [--out dir]
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE || 'playwright-core');
const args = process.argv.slice(2);
const OUT = args.includes('--out') ? args[args.indexOf('--out') + 1] : 'docs/core-final/e2e';
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
await page.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
await page.goto(BASE + '?profile=immersive', { waitUntil: 'networkidle' });
await page.addStyleTag({ content: 'astro-dev-toolbar{display:none!important}' });
await page.selectOption('#building-select', 'proc-factory-dev-003');
await page.waitForTimeout(2500);
await page.getByRole('group', { name: 'Profile' }).getByRole('button', { name: /Developer/ }).click();
await page.getByRole('tab', { name: 'Developer' }).click();
for (const [q, which] of [['Office 1.04', 'Start'], ['Kitchen 0.28', 'Target']]) {
  const box = page.getByLabel('Search endpoints');
  await box.fill('');
  await page.getByRole('group', { name: 'Search kind' }).getByRole('button', { name: 'Rooms', exact: true }).click();
  await box.fill(q);
  await page.locator('.dev-result').first().waitFor();
  await page.locator('.dev-result').first().getByRole('button', { name: which, exact: true }).click();
}
await page.getByRole('button', { name: 'Compute route' }).click();
await page.locator('.guidance').waitFor();
await page.getByRole('button', { name: 'Start navigation' }).click();
await page.evaluate(() => window.__arinavGuidance.manualClock(true));
await page.evaluate(() => window.__arinavGuidance.advance(40, 0.05));
await page.getByRole('button', { name: 'Hide panel' }).click({ force: true }).catch(() => {});
const shot = async (name, off) => {
  await page.evaluate((off) => {
    const s = window.__arinavScene;
    const p = s.walkerGroup.position;
    s.follow = false;
    s.controls.target.set(p.x, p.y + 0.9, p.z);
    s.camera.position.set(p.x + off[0], p.y + off[1], p.z + off[2]);
    s.controls.update();
    s.needsRender = true;
  }, off);
  await page.evaluate(() => window.__arinavGuidance.advance(0.3, 0.05));
  await page.waitForTimeout(700);
  await page.screenshot({ path: join(OUT, `walker-${name}-1440.png`) });
};
await shot('close', [3.2, 2.2, 3.2]);
await shot('mid', [9, 9, 9]);
await shot('far', [30, 38, 30]);
await shot('close', [2.6, 2.0, 2.6]);
console.log('walker shots ->', OUT);
await browser.close();
