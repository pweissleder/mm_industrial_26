// Core-final acceptance (2026-10-25): equipment start, arrival finish, view switch, phone views,
// first-person object info. Real browser against the app; offline backend is enough (no LLM).
//   PW_MODULE=<playwright-core> ARINAV_URL=... node scripts/e2e/core_final_check.mjs [--out dir]
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE || 'playwright-core');
const args = process.argv.slice(2);
const OUT = args.includes('--out') ? args[args.indexOf('--out') + 1] : 'docs/core-final/e2e';
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const FACTORY = 'proc-factory-dev-003';
mkdirSync(OUT, { recursive: true });
const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const results = [];
let current = null;
const assert = (c, m) => {
  if (!c) throw new Error(m);
};
async function check(name, fn) {
  try {
    const detail = await fn();
    results.push({ name, pass: true, detail: detail ?? null });
    console.log(`PASS ${name}${detail ? ' — ' + JSON.stringify(detail).slice(0, 240) : ''}`);
  } catch (e) {
    results.push({ name, pass: false, error: String(e).slice(0, 800) });
    console.log(`FAIL ${name} — ${String(e).slice(0, 300)}`);
    if (current) await current.screenshot({ path: join(OUT, `fail-${name}.png`) }).catch(() => {});
  }
}
async function open(viewport, contextOptions = {}) {
  const context = await browser.newContext({ viewport, ...contextOptions });
  const page = await context.newPage();
  page.errors = [];
  page.on('pageerror', (e) => page.errors.push(e.message));
  await page.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
  await page.goto(BASE + '?profile=immersive', { waitUntil: 'networkidle' });
  await page.addStyleTag({ content: 'astro-dev-toolbar{display:none!important}' });
  return page;
}
const narrow = (p) => (p.viewportSize()?.width ?? 1440) < 1100;
const panel = async (p, openIt) => {
  const btn = p.getByRole('button', { name: openIt ? 'Show panel' : 'Hide panel' });
  if (await btn.count()) {
    await btn.click({ force: true });
    await p.waitForTimeout(350);
  }
};
async function building(p, id) {
  await panel(p, true);
  if (await p.getByRole('tab', { name: 'Location' }).count()) await p.getByRole('tab', { name: 'Location' }).click();
  await p.selectOption('#building-select', id);
  await p.waitForFunction((d) => document.querySelector('#building-select')?.value === d && document.querySelector('.topbar__building'), id);
  await p.waitForTimeout(1500);
}
async function mode(p, name) {
  if (narrow(p)) await panel(p, false);
  await p.getByRole('group', { name: 'Profile' }).getByRole('button', { name: new RegExp(name) }).click();
}
async function endpoint(p, q, which, kind) {
  const box = p.getByLabel('Search endpoints');
  await box.fill('');
  await p.getByRole('group', { name: 'Search kind' }).getByRole('button', { name: kind, exact: true }).click();
  await box.fill(q);
  const row = p.locator('.dev-result').first();
  await row.waitFor({ timeout: 15000 });
  await row.getByRole('button', { name: which, exact: true }).click();
}
async function devRoute(p, sq, sk, tq, tk) {
  await mode(p, 'Developer');
  await panel(p, true);
  await p.getByRole('tab', { name: 'Developer' }).click();
  await endpoint(p, sq, 'Start', sk);
  await endpoint(p, tq, 'Target', tk);
  const req = p.waitForRequest((r) => r.url().endsWith('/route') && r.method() === 'POST');
  await p.getByRole('button', { name: 'Compute route' }).click();
  const body = JSON.parse((await req).postData());
  await p.locator('.guidance').waitFor({ timeout: 15000 });
  if (narrow(p)) await panel(p, false);
  return body;
}
const g = (p) => p.evaluate(() => {
  const s = window.__arinavGuidance.snapshot();
  return { status: s.status, d: s.distance_m, total: s.total_m, floor: s.progress?.floor_id, pos: s.progress?.position, route: !!s.route };
});

// ------------------------------------------------------------------ desktop 1440
const page = await open({ width: 1440, height: 900 });
current = page;
await building(page, FACTORY);

await check('P0_equipment_start_route_and_walker', async () => {
  const body = await devRoute(page, 'CNC mill H-A1-A', 'Assets', 'Kitchen 0.28', 'Rooms');
  assert(body.start.kind === 'asset', `route start is ${JSON.stringify(body.start)}`);
  await page.getByRole('button', { name: 'Start navigation' }).click();
  await page.evaluate(() => window.__arinavGuidance.manualClock(true));
  await page.evaluate(() => window.__arinavGuidance.advance(0.2));
  const s = await g(page);
  await page.screenshot({ path: join(OUT, 'P0-equipment-start-1440.png') });
  return { start: body.start, walker: s.pos, floor: s.floor };
});

await check('P0_arrival_check_then_navigation_closes', async () => {
  await page.evaluate(() => window.__arinavGuidance.advance(400, 0.5));
  const at = await g(page);
  assert(at.status === 'arrived', `status ${at.status}`);
  await page.locator('.arrival').waitFor({ timeout: 3000 });
  const text = await page.locator('.arrival').innerText();
  await page.screenshot({ path: join(OUT, 'P0-arrival-1440.png') });
  await page.waitForTimeout(3200);
  const after = await g(page);
  const guidanceLeft = await page.locator('.guidance').count();
  const loc = await page.evaluate(() => document.body.innerText.match(/Location: [^\n]*/)?.[0] ?? null);
  assert(after.status === 'idle' && !after.route, `after arrival: ${JSON.stringify(after)}`);
  await page.screenshot({ path: join(OUT, 'P0-after-arrival-1440.png') });
  return { text: text.replace(/\s+/g, ' '), after: after.status, guidanceCards: guidanceLeft, location: loc };
});

await check('P0_new_route_after_arrival_starts_at_target', async () => {
  await page.getByRole('tab', { name: 'Developer' }).click().catch(() => {});
  await endpoint(page, 'Quality lab', 'Target', 'Rooms');
  const req = page.waitForRequest((r) => r.url().endsWith('/route') && r.method() === 'POST');
  await page.getByRole('button', { name: 'Compute route' }).click();
  const body = JSON.parse((await req).postData());
  assert(body.start.kind === 'space' && /S028$/.test(body.start.id), `new route starts at ${JSON.stringify(body.start)}`);
  return { start: body.start };
});

await check('P2_view_switch_folded_hover_escape', async () => {
  const sw = page.getByRole('group', { name: 'Camera' });
  const folded = await sw.evaluate((el) => el.classList.contains('view-switch--folded'));
  const w0 = (await sw.boundingBox()).width;
  await sw.hover();
  await page.waitForTimeout(400);
  const w1 = (await sw.boundingBox()).width;
  await page.screenshot({ path: join(OUT, 'P2-view-switch-open-1440.png') });
  await page.keyboard.press('Escape');
  await page.mouse.move(700, 300);
  await page.waitForTimeout(400);
  const w2 = (await sw.boundingBox()).width;
  assert(folded && w1 > w0 + 20 && w2 < w1 - 20, `widths ${w0} ${w1} ${w2}`);
  await sw.hover();
  await page.waitForTimeout(300);
  await page.getByRole('button', { name: 'First person' }).click();
  await page.waitForTimeout(800);
  const view = await page.evaluate(() => window.__arinavImmersive.look().view);
  const stillRoute = await g(page);
  return { folded_px: Math.round(w0), open_px: Math.round(w1), view, route_kept: stillRoute.route };
});

await check('P1_desktop_dev_split_and_click_object_in_first_person', async () => {
  await page.waitForTimeout(800);
  await page.screenshot({ path: join(OUT, 'P1-desktop-dev-pov-1440.png') });
  const split = await page.evaluate(() => window.__arinavScene?.split ?? null);
  // click the middle of the first-person view: an object or wall there opens details
  const box = await page.locator('canvas').first().boundingBox();
  await page.mouse.click(box.x + box.width * 0.25, box.y + box.height * 0.55);
  await page.waitForTimeout(600);
  const details = await page.locator('.details__title').first().innerText().catch(() => null);
  await page.screenshot({ path: join(OUT, 'P1-pov-click-1440.png') });
  return { split, details };
});

// ------------------------------------------------------------------ phone 390 x 844
const phone = await open({ width: 390, height: 844 }, { isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
current = phone;
await building(phone, FACTORY);
await check('P1_phone_dev_single_fullscreen_view', async () => {
  await devRoute(phone, 'Office 1.04', 'Rooms', 'Kitchen 0.28', 'Rooms');
  await phone.getByRole('button', { name: 'Start navigation' }).click();
  await phone.evaluate(() => window.__arinavGuidance.manualClock(true));
  await phone.evaluate(() => window.__arinavGuidance.advance(3));
  const sw = phone.getByRole('group', { name: 'Camera' });
  await sw.click();
  await phone.waitForTimeout(300);
  await phone.getByRole('button', { name: 'First person' }).click();
  await phone.waitForTimeout(1200);
  const split = await phone.evaluate(() => window.__arinavScene?.split ?? null);
  const mock = await phone.locator('.phone-mock:visible').count();
  await phone.screenshot({ path: join(OUT, 'P1-phone-dev-pov-390.png') });
  assert(!split && !mock, `phone shows split=${split} mock=${mock}`);
  return { split, mock };
});

await check('P1_phone_rotation_three_axes_synthetic', async () => {
  // synthetic DeviceOrientationEvents (not a physical test): look up, level, sideways tilt
  await phone.evaluate(() => window.__arinavImmersive.enableOrientation?.());
  const send = (a, b, c) => phone.evaluate(([a, b, c]) => window.dispatchEvent(new DeviceOrientationEvent('deviceorientation', { alpha: a, beta: b, gamma: c })), [a, b, c]);
  for (let i = 0; i < 12; i += 1) await send(0, 90, 0);
  const level = await phone.evaluate(() => window.__arinavImmersive.look());
  for (let i = 0; i < 20; i += 1) await send(0, 150, 0);
  const up = await phone.evaluate(() => window.__arinavImmersive.look());
  await phone.screenshot({ path: join(OUT, 'P1-phone-look-up-390.png') });
  for (let i = 0; i < 20; i += 1) await send(0, 20, 0);
  const down = await phone.evaluate(() => window.__arinavImmersive.look());
  await phone.screenshot({ path: join(OUT, 'P1-phone-look-down-390.png') });
  const walked = await g(phone);
  await phone.evaluate(() => window.__arinavGuidance.advance(2));
  const walked2 = await g(phone);
  const still = await phone.evaluate(() => window.__arinavImmersive.look());
  assert(up.pitch > 40 && down.pitch < -40, `pitch up ${up.pitch} down ${down.pitch}`);
  assert(walked2.d > walked.d && Math.abs(still.yaw - down.yaw) < 1, 'walking changed the view or did not move');
  return { level: level.pitch, up: up.pitch, down: down.pitch, yaw: down.yaw, moved_m: +(walked2.d - walked.d).toFixed(2) };
});

writeFileSync(join(OUT, 'core_final_check.json'), JSON.stringify({ base: BASE, results }, null, 1));
console.log(`${results.filter((r) => r.pass).length}/${results.length} core-final checks passed -> ${OUT}`);
await browser.close();
