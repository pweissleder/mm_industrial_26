// Browser check H01-H03 (showcase): opt-in session memory, preview not counted, recap after a walk.
// Offline backend; writes docs/night/e2e/H01-recap-1280.png. PW_MODULE=<playwright-core> node scripts/e2e/recap_check.mjs
import { createRequire } from 'node:module';
import { login, logout } from './login.mjs';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE);
const OUT = 'docs/night/e2e';
const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true, args: ['--use-angle=swiftshader','--enable-unsafe-swiftshader'] });
const p = await (await b.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
await p.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
await p.goto('http://localhost:4321/?profile=showcase&demo=1', { waitUntil: 'networkidle' });
await login(p);
await p.addStyleTag({ content: 'astro-dev-toolbar{display:none!important}' });
await p.selectOption('#building-select', 'proc-factory-dev-003').catch(async () => { await p.getByRole('button', { name: 'Show panel' }).click({ force: true }); await p.selectOption('#building-select', 'proc-factory-dev-003'); });
await p.waitForTimeout(3000);
await p.getByRole('button', { name: 'Remember session' }).click();
await p.waitForTimeout(800);
await p.getByRole('group', { name: 'Mode' }).getByRole('button', { name: 'Developer' }).click();
await p.getByRole('tab', { name: 'Developer' }).click();
for (const [q, which] of [['Office 0.38','Start'],['Kitchen 0.28','Target']]) {
  const box = p.getByLabel('Search endpoints'); await box.fill('');
  await p.getByRole('group', { name: 'Search kind' }).getByRole('button', { name: 'Rooms', exact: true }).click();
  await box.fill(q); const row = p.locator('.dev-result').first(); await row.waitFor();
  await row.getByRole('button', { name: which, exact: true }).click();
}
await p.getByRole('button', { name: 'Compute route' }).click();
await p.locator('.guidance').waitFor();
await p.evaluate(() => window.__arinavGuidance.manualClock(true));
await p.getByRole('button', { name: 'Fly-through' }).click();
await p.waitForTimeout(1500);
await p.getByRole('button', { name: /Skip preview/ }).click().catch(() => {});
await p.getByRole('button', { name: 'Start navigation' }).click();
for (let i = 0; i < 40 && (await p.evaluate(() => window.__arinavGuidance.snapshot().status)) !== 'arrived'; i++) await p.evaluate(() => window.__arinavGuidance.advance(2));
await p.waitForTimeout(1200);
await p.getByRole('button', { name: 'Session recap' }).click();
await p.waitForTimeout(1500);
const text = await p.locator('.recap-overlay').innerText();
await p.screenshot({ path: OUT + '/H01-recap-1280.png' });
console.log(text.replace(/\n+/g, ' | ').slice(0, 600));
await logout(p);
await b.close();
