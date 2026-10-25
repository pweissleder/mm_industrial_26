// User-reported fixes (2026-10-25 11:30): "I'm here" on a clicked thing, foldable navigation card,
// walking in the user profile (WASD / small stick), Auto-walk, first person kept after arrival,
// tool names on hover. No assistant calls: routes come from the developer controls.
//   PW_MODULE=<playwright-core> ARINAV_URL=... node scripts/e2e/user_fix_check.mjs [--out dir]
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE || 'playwright-core');
const args = process.argv.slice(2);
const OUT = args.includes('--out') ? args[args.indexOf('--out') + 1] : 'docs/core-final/e2e';
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
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
    console.log(`PASS ${name}${detail ? ' — ' + JSON.stringify(detail).slice(0, 260) : ''}`);
  } catch (e) {
    results.push({ name, pass: false, error: String(e).slice(0, 600) });
    console.log(`FAIL ${name} — ${String(e).slice(0, 300)}`);
    if (current) await current.screenshot({ path: join(OUT, `fail-${name}.png`) }).catch(() => {});
  }
}
async function open(viewport, extra = {}) {
  const page = await (await browser.newContext({ viewport, ignoreHTTPSErrors: true, ...extra })).newPage();
  await page.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
  await page.route('**/api/assistant/turn', (r) => r.fulfill({ status: 503, body: '{}' })); // never a paid call
  await page.goto(BASE + '?profile=immersive', { waitUntil: 'networkidle' });
  await page.addStyleTag({ content: 'astro-dev-toolbar{display:none!important}' });
  return page;
}
const narrow = (p) => (p.viewportSize()?.width ?? 1440) < 1100;
async function panel(p, openIt) {
  const btn = openIt
    ? p.getByRole('button', { name: 'Show panel' })
    : p.getByRole('button', { name: /^(Hide panel|Close panel)$/ }).first();
  if (await btn.count()) {
    await btn.click({ force: true });
    await p.waitForTimeout(350);
  }
}
async function profile(p, name) {
  if (narrow(p)) await panel(p, false);
  await p.getByRole('group', { name: 'Profile' }).getByRole('button', { name: new RegExp(name) }).click();
  await p.waitForTimeout(300);
}
async function setup(p) {
  await panel(p, true);
  if (await p.getByRole('tab', { name: 'Location' }).count()) await p.getByRole('tab', { name: 'Location' }).click();
  await p.selectOption('#building-select', 'proc-factory-dev-003');
  await p.waitForTimeout(2000);
  await profile(p, 'Developer');
  await panel(p, true);
  await p.getByRole('tab', { name: 'Developer' }).click();
  for (const [q, which] of [['Office 1.04', 'Start'], ['Kitchen 0.28', 'Target']]) {
    const box = p.getByLabel('Search endpoints');
    await box.fill('');
    await p.getByRole('group', { name: 'Search kind' }).getByRole('button', { name: 'Rooms', exact: true }).click();
    await box.fill(q);
    await p.locator('.dev-result').first().waitFor();
    await p.locator('.dev-result').first().getByRole('button', { name: which, exact: true }).click();
  }
  await p.getByRole('button', { name: 'Compute route' }).click();
  await p.locator('.guidance').waitFor();
  await profile(p, 'User');
  await panel(p, false);
}
const g = (p) =>
  p.evaluate(() => {
    const s = window.__arinavGuidance.snapshot();
    return { status: s.status, d: +s.distance_m.toFixed(2), route: !!s.route };
  });

// ------------------------------------------------------------------ phone 390 x 844, user profile
const phone = await open({ width: 390, height: 844 }, { isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
current = phone;
await setup(phone);
await check('M1_navigation_card_folds_on_the_phone', async () => {
  await phone.getByRole('button', { name: 'Start navigation' }).click();
  await phone.evaluate(() => window.__arinavGuidance.manualClock(true));
  await phone.evaluate(() => window.__arinavGuidance.advance(3));
  await phone.waitForTimeout(400);
  const folded = (await phone.locator('.guidance').boundingBox()).height;
  await phone.screenshot({ path: join(OUT, 'U-phone-card-folded-390.png') });
  await phone.getByRole('button', { name: 'Show navigation details' }).tap();
  await phone.waitForTimeout(300);
  const opened = (await phone.locator('.guidance').boundingBox()).height;
  await phone.getByRole('button', { name: 'Minimise navigation' }).tap();
  assert(folded < opened - 60 && folded < 170, `card ${folded}px folded vs ${opened}px open`);
  return { folded_px: Math.round(folded), open_px: Math.round(opened) };
});
await check('M2_walk_yourself_and_auto_walk_in_user_profile_phone', async () => {
  await phone.getByRole('button', { name: 'Change view' }).tap();
  await phone.waitForTimeout(300);
  await phone.getByRole('button', { name: 'First person' }).tap();
  await phone.waitForTimeout(800);
  await phone.getByRole('button', { name: /Walk myself/ }).tap();
  await phone.waitForTimeout(500);
  const stick = await phone.locator('.joystick').boundingBox();
  assert(stick && stick.width <= 90, `stick ${JSON.stringify(stick)}`);
  await phone.screenshot({ path: join(OUT, 'U-phone-walk-yourself-390.png') });
  await phone.getByRole('button', { name: 'Auto-walk' }).tap();
  await phone.waitForTimeout(300);
  const a = await g(phone);
  await phone.evaluate(() => window.__arinavGuidance.advance(2));
  const b = await g(phone);
  const driver = await phone.evaluate(() => window.__arinavImmersive.driver());
  assert(driver === 'autowalk' && b.d > a.d, `auto-walk: ${driver} ${a.d} -> ${b.d}`);
  return { stick_px: Math.round(stick.width), auto_from_m: a.d, auto_to_m: b.d };
});
await check('M3_arrival_keeps_first_person', async () => {
  await phone.evaluate(() => window.__arinavGuidance.advance(200, 0.5));
  await phone.waitForTimeout(3600);
  const view = await phone.evaluate(() => window.__arinavImmersive.look().view);
  const pose = await phone.evaluate(() => window.__arinavImmersive.pose());
  const s = await g(phone);
  await phone.screenshot({ path: join(OUT, 'U-phone-after-arrival-pov-390.png') });
  assert(view === 'pov' && pose && !s.route, `view ${view} pose ${!!pose} route ${s.route}`);
  return { view, standing_at: pose.position.map((v) => +v.toFixed(1)) };
});
await check('M4_selected_thing_offers_im_here', async () => {
  // back to the overview (tap the folded switch, then Overview), then pick a room
  await phone.getByRole('button', { name: 'Change view' }).tap();
  await phone.waitForTimeout(300);
  await phone.getByRole('button', { name: 'Overview' }).tap();
  await phone.waitForTimeout(600);
  await panel(phone, true);
  await phone.getByRole('tab', { name: 'Location' }).click();
  await phone.getByPlaceholder(/Search rooms/).fill('Quality lab');
  await phone.locator('text=Quality lab 0.21').first().click();
  await panel(phone, false);
  await phone.waitForTimeout(500);
  const buttons = await phone.locator('.details__group-buttons button').allInnerTexts();
  await phone.screenshot({ path: join(OUT, 'U-phone-im-here-390.png') });
  assert(buttons.some((t) => /I.m here/.test(t)) && buttons.some((t) => /Take me there/.test(t)), JSON.stringify(buttons));
  const title = await phone.locator('.details__title').first().innerText();
  const sim0 = await phone.evaluate(() => document.body.innerText.match(/Location: [^\n]*/)?.[0] ?? null);
  await phone.locator('.details__group-buttons button').first().tap();
  await phone.waitForTimeout(500);
  const loc = await phone.evaluate(() => document.body.innerText.match(/Location: [^\n]*/)?.[0] ?? null);
  assert(loc && loc.includes(title) && loc !== sim0, `card ${title}, before ${sim0}, location after "I'm here": ${loc}`);
  return { buttons, location: loc };
});

// ------------------------------------------------------------------ desktop 1440, user profile
const desk = await open({ width: 1440, height: 900 });
current = desk;
await setup(desk);
await check('D1_tool_names_on_hover', async () => {
  const zoom = desk.getByRole('button', { name: 'Zoom in' });
  await zoom.hover();
  await desk.waitForTimeout(250);
  const tip = await zoom.evaluate((el) => getComputedStyle(el, '::after').content);
  await desk.screenshot({ path: join(OUT, 'U-desktop-tool-tip-1440.png') });
  assert(tip.includes('Zoom in'), `tip ${tip}`);
  return { tip };
});
await check('D2_wasd_in_user_profile', async () => {
  // a desktop user clicks the view icon (hover has opened the menu), then First person
  await desk.getByRole('button', { name: 'Change view' }).click();
  await desk.waitForTimeout(300);
  await desk.screenshot({ path: join(OUT, 'U-desktop-switch-open-1440.png') });
  await desk.getByRole('button', { name: 'First person' }).click();
  await desk.waitForTimeout(600);
  // choosing first person without a route walker already starts walking yourself
  if (await desk.getByRole('button', { name: /Walk myself/ }).count()) await desk.getByRole('button', { name: /Walk myself/ }).click();
  await desk.mouse.move(800, 450);
  const a = await desk.evaluate(() => window.__arinavImmersive.pose());
  await desk.keyboard.down('w');
  await desk.waitForTimeout(700);
  await desk.keyboard.up('w');
  const b = await desk.evaluate(() => window.__arinavImmersive.pose());
  const moved = Math.hypot(b.position[0] - a.position[0], b.position[1] - a.position[1]);
  await desk.screenshot({ path: join(OUT, 'U-desktop-user-wasd-1440.png') });
  assert(moved > 0.2, `moved ${moved}`); // walls may stop it early; any step proves the keys work
  return { moved_m: +moved.toFixed(2) };
});
writeFileSync(join(OUT, 'user_fix_check.json'), JSON.stringify({ base: BASE, results }, null, 1));
console.log(`${results.filter((r) => r.pass).length}/${results.length} user-fix checks passed -> ${OUT}`);
await browser.close();
