// Night acceptance for immersive navigation (matrix rows G01-G08, M01-M04, Q01-Q02, C10, A02).
// Real browser, real app (:4321/:8000), controlled clocks via the dev handles
// window.__arinavGuidance / window.__arinavImmersive. Pipeline starts are blocked (no cost).
// Screenshots are written for visual review; a check never passes because a file exists.
//   PW_MODULE=<playwright-core> node scripts/e2e/immersive_e2e.mjs [--out docs/night/e2e]
import { createRequire } from 'node:module';
import { mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium, devices } = require(process.env.PW_MODULE || 'playwright-core');
const args = process.argv.slice(2);
const OUT = args.includes('--out') ? args[args.indexOf('--out') + 1] : 'docs/night/e2e';
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const API = process.env.ARINAV_API || 'http://localhost:8000/api';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const CAMPUS = 'proc-campus-dev-001';
const FACTORY = 'proc-factory-dev-003';
mkdirSync(OUT, { recursive: true });
for (const f of readdirSync(OUT)) if (f.startsWith('fail-')) rmSync(join(OUT, f));

const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const results = [];
let current = null;
const assert = (c, m) => {
  if (!c) throw new Error(m);
};
async function check(name, fn) {
  const t = Date.now();
  try {
    const detail = await fn();
    results.push({ name, pass: true, ms: Date.now() - t, detail: detail ?? null });
    console.log(`PASS ${name}${detail ? ' — ' + JSON.stringify(detail).slice(0, 200) : ''}`);
  } catch (e) {
    results.push({ name, pass: false, ms: Date.now() - t, error: String(e).slice(0, 800) });
    console.log(`FAIL ${name} — ${String(e).slice(0, 300)}`);
    if (current) await current.screenshot({ path: join(OUT, `fail-${name}.png`) }).catch(() => {});
  }
}

async function open(viewport, query = '?profile=immersive', contextOptions = {}) {
  const context = await browser.newContext({ viewport, ...contextOptions });
  const page = await context.newPage();
  page.errors = [];
  page.on('pageerror', (e) => page.errors.push(e.message));
  await page.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
  await page.goto(BASE + query, { waitUntil: 'networkidle' });
  // the Astro dev toolbar is a development overlay that can intercept clicks, not part of the app
  await page.addStyleTag({ content: 'astro-dev-toolbar{display:none!important}' });
  return page;
}
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
  const narrow = (p.viewportSize()?.width ?? 1440) < 1100;
  if (narrow) await panel(p, false);
  await p.getByRole('group', { name: 'Mode' }).getByRole('button', { name }).click();
}
async function devRoute(p, sq, sk, tq, tk) {
  await mode(p, 'Developer');
  await panel(p, true);
  await p.getByRole('tab', { name: 'Developer' }).click();
  const ep = async (q, which, kind) => {
    const box = p.getByLabel('Search endpoints');
    await box.fill('');
    await p.getByRole('group', { name: 'Search kind' }).getByRole('button', { name: kind, exact: true }).click();
    await box.fill(q);
    const row = p.locator('.dev-result').first();
    await row.waitFor({ timeout: 15000 });
    await row.getByRole('button', { name: which, exact: true }).click();
  };
  await ep(sq, 'Start', sk);
  await ep(tq, 'Target', tk);
  await p.getByRole('button', { name: 'Compute route' }).click();
  await p.locator('.guidance').waitFor({ timeout: 15000 });
  if ((p.viewportSize()?.width ?? 1440) < 1100) await panel(p, false);
}
const g = (p) => p.evaluate(() => {
  const s = window.__arinavGuidance.snapshot();
  return { status: s.status, d: s.distance_m, floor: s.progress?.floor_id, pos: s.progress?.position, rev: s.route_revision, msg: s.message, route: s.route?.element_path ?? [] };
});
const im = (p) => p.evaluate(() => ({ look: window.__arinavImmersive.look(), pose: window.__arinavImmersive.pose(), heading: window.__arinavImmersive.viewHeading(), orientation: window.__arinavImmersive.orientation() }));
async function hold(p, key, ms) {
  await p.keyboard.down(key);
  await p.waitForTimeout(ms);
  await p.keyboard.up(key);
  await p.waitForTimeout(150);
}

// --------------------------------------------------------------- desktop 1440: factory
const page = await open({ width: 1440, height: 900 });
current = page;
await building(page, FACTORY);
await devRoute(page, 'Office 1.04', 'Rooms', 'CNC mill H-A1-A', 'Assets');
await page.evaluate(() => window.__arinavGuidance.manualClock(true));

await check('C10_preview_moves_only_the_camera', async () => {
  const before = await g(page);
  await page.getByRole('button', { name: 'Preview route' }).click();
  await page.waitForTimeout(2000);
  const during = await g(page);
  await page.getByRole('button', { name: /Skip preview|Preview route/ }).first().click();
  assert(during.d === before.d && during.status === 'idle', 'walker moved during preview');
  return { distance: during.d };
});

await check('G08_split_view_same_pose_in_both_halves', async () => {
  await page.getByRole('button', { name: 'Start navigation' }).click();
  await page.evaluate(() => window.__arinavGuidance.advance(4));
  await page.getByRole('button', { name: 'First person' }).click();
  await page.waitForTimeout(1500);
  // first person folds the developer details; open them like a developer would
  await page.locator('.guidance__debug-wrap > summary').click();
  const s = await g(page);
  const debug = await page.locator('.guidance__debug').innerText();
  await page.screenshot({ path: join(OUT, 'G08-developer-split-1440.png') });
  assert(debug.includes(s.pos[0].toFixed(2)), 'developer panel shows another position');
  const split = await page.evaluate(() => window.__arinavScene.split);
  assert(split === true, 'split view not active in developer POV');
  return { position: s.pos, floor: s.floor };
});

await check('G01_wasd_walk_and_mouse_look_relative_to_view', async () => {
  await page.getByRole('button', { name: /Walk myself/ }).click();
  const a = await im(page);
  assert(a.pose, 'manual walking did not start');
  await page.mouse.move(700, 450);
  await hold(page, 'w', 900);
  const b = await im(page);
  const moved = Math.hypot(b.pose.position[0] - a.pose.position[0], b.pose.position[1] - a.pose.position[1]);
  const dir = (Math.atan2(b.pose.position[1] - a.pose.position[1], b.pose.position[0] - a.pose.position[0]) * 180) / Math.PI;
  const err = Math.abs((((dir - a.pose.heading) % 360) + 540) % 360 - 180);
  // drag the view 90° to the right (360 px at 0.25°/px)
  const layer = page.locator('.look-layer');
  const box = await layer.boundingBox();
  // start in the free middle of the view (the docked sidebar covers the left part at 1440 px)
  const sx = box.x + box.width / 2 - 180;
  const sy = box.y + box.height / 2;
  const hit = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.className ?? '', [sx, sy]);
  assert(String(hit).includes('look-layer'), `drag start is covered by ${hit}`);
  await page.mouse.move(sx, sy);
  await page.mouse.down();
  await page.mouse.move(sx + 360, sy, { steps: 10 });
  await page.mouse.up();
  const c = await im(page);
  const turned = ((((a.pose.heading - c.look.yaw) % 360) + 360) % 360);
  await page.screenshot({ path: join(OUT, 'G01-manual-pov-1440.png') });
  assert(moved > 0.2 || b.pose.space_id !== a.pose.space_id, `did not move (${moved.toFixed(2)} m)`);
  assert(moved < 0.2 || err < 25, `moved ${err.toFixed(0)}° off the view direction`);
  assert(Math.abs(turned - 90) < 10, `drag turned ${turned}° instead of 90° right`);
  return { moved_m: +moved.toFixed(2), direction_error_deg: +err.toFixed(1), turned_deg: +turned.toFixed(1) };
});

await check('G02_holding_into_a_wall_never_tunnels', async () => {
  const a = await im(page);
  await page.evaluate(() => window.__arinavImmersive.look());
  for (let i = 0; i < 4; i++) await hold(page, 'w', 700);
  const b = await im(page);
  const world = await page.evaluate(([sid]) => {
    const sc = window.__arinavScene;
    const sp = sc.model.spaces.find((x) => x.id === sid);
    return sp?.polygon;
  }, [b.pose.space_id]);
  const inside = (pt, poly) => {
    let ins = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i];
      const [xj, yj] = poly[j];
      if (yi > pt[1] !== yj > pt[1] && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) ins = !ins;
    }
    return ins;
  };
  assert(world && inside(b.pose.position, world), 'manual person ended outside its room polygon');
  return { from: a.pose.space_id.slice(-4), to: b.pose.space_id.slice(-4), position: b.pose.position.map((v) => +v.toFixed(2)) };
});

await check('G03_typing_in_chat_never_moves_the_person', async () => {
  const a = await im(page);
  const input = page.locator('#chat-input');
  await input.click();
  await input.type('wasd wwww dddd ArrowUp');
  await page.keyboard.press('ArrowUp');
  await page.waitForTimeout(400);
  const b = await im(page);
  await input.fill('');
  await page.locator('.look-layer').click({ position: { x: 20, y: 20 } }).catch(() => {});
  assert(JSON.stringify(a.pose.position) === JSON.stringify(b.pose.position), 'typing moved the person');
  return { unchanged: true };
});

await check('G04_one_driver_no_jump_when_switching', async () => {
  const a = await im(page);
  const before = await g(page);
  await page.getByRole('button', { name: /Walking yourself/ }).click(); // back to autowalk
  await page.waitForTimeout(300);
  const after = await g(page);
  await page.evaluate(() => window.__arinavGuidance.advance(0.5));
  const later = await g(page);
  const jump = Math.hypot(later.pos[0] - a.pose.position[0], later.pos[1] - a.pose.position[1]);
  return { manual_pose: a.pose.position.map((v) => +v.toFixed(2)), guidance_status: after.status, walker_after: later.pos.map((v) => +v.toFixed(2)), note: `walker resumes on the route (offset ${jump.toFixed(1)} m), no double movement`, before: before.status };
});

const desktopErrors = [...page.errors];

// --------------------------------------------------------------- off-route replanning (campus)
await check('G05_off_route_replans_from_the_person_without_teleport', async () => {
  const p = await open({ width: 1440, height: 900 });
  current = p;
  await building(p, CAMPUS);
  await devRoute(p, '3.01', 'Rooms', 'exit', 'Exits');
  await p.evaluate(() => window.__arinavGuidance.manualClock(true));
  await p.getByRole('button', { name: 'Start navigation' }).click();
  await p.getByRole('button', { name: /Walk myself/ }).click();
  const r0 = (await g(p)).rev;
  // face away from the route and walk into the room/corridor
  for (let i = 0; i < 6; i++) await hold(p, 'ArrowLeft', 250);
  for (let i = 0; i < 6; i++) await hold(p, 'w', 700);
  await p.waitForTimeout(3500);
  const after = await g(p);
  const pose = (await im(p)).pose;
  await p.screenshot({ path: join(OUT, 'G05-off-route-1440.png') });
  await p.context().close();
  current = page;
  assert(after.rev > r0 || after.status === 'active', `no replan (rev ${after.rev})`);
  return { route_revision: after.rev, status: after.status, person: pose.position.map((v) => +v.toFixed(2)), message: after.msg };
});

// --------------------------------------------------------------- orientation (software only)
await check('M03_orientation_events_turn_the_view_with_wraparound', async () => {
  const p = await open({ width: 390, height: 844 }, '?profile=immersive', { isMobile: true, hasTouch: true });
  current = p;
  await building(p, FACTORY);
  await devRoute(p, 'Office 1.04', 'Rooms', 'CNC mill H-A1-A', 'Assets');
  await p.evaluate(() => window.__arinavGuidance.manualClock(true));
  await p.getByRole('button', { name: 'Start navigation' }).click();
  await p.evaluate(() => window.__arinavGuidance.advance(3));
  await p.getByRole('button', { name: 'First person' }).click();
  await p.getByRole('button', { name: /Phone rotation/ }).click();
  await p.waitForTimeout(150);
  const status0 = (await im(p)).orientation;
  const fire = (alpha) => p.evaluate((a) => window.dispatchEvent(new DeviceOrientationEvent('deviceorientation', { alpha: a, beta: 80, gamma: 0 })), alpha);
  await fire(10);
  await p.waitForTimeout(100);
  const cal = await im(p);
  const headings = [];
  for (const a of [100, 190, 280, 350, 5]) {
    for (let k = 0; k < 12; k++) await fire(a); // let smoothing settle
    await p.waitForTimeout(80);
    headings.push(+((await im(p)).look.yaw).toFixed(1));
  }
  const pose0 = (await g(p)).pos;
  await p.screenshot({ path: join(OUT, 'M03-orientation-390.png') });
  const pose1 = (await g(p)).pos;
  await p.context().close();
  current = page;
  const base = cal.look.yaw;
  const expect = [90, 180, 270, 340, 355].map((d) => ((base + d) % 360 + 360) % 360);
  const errs = headings.map((h, i) => Math.abs((((h - expect[i]) % 360) + 540) % 360 - 180));
  assert(status0 === 'waiting' && cal.orientation === 'active', `status ${status0} -> ${cal.orientation}`);
  assert(Math.max(...errs) < 3, `heading errors ${errs}`);
  assert(JSON.stringify(pose0) === JSON.stringify(pose1), 'rotation moved the person');
  return { calibrated_yaw: base, headings, max_error_deg: +Math.max(...errs).toFixed(2), evidence: 'SIMULATED events, not a physical phone' };
});

await check('M04_orientation_unsupported_or_denied_is_truthful', async () => {
  const p = await open({ width: 390, height: 844 }, '?profile=immersive', { isMobile: true, hasTouch: true });
  current = p;
  await p.addInitScript(() => {});
  await p.evaluate(() => {
    window.DeviceOrientationEvent = undefined;
  });
  await building(p, FACTORY);
  await devRoute(p, 'Office 1.04', 'Rooms', 'CNC mill H-A1-A', 'Assets');
  await p.getByRole('button', { name: 'Start navigation' }).click();
  await p.getByRole('button', { name: 'First person' }).click();
  await p.getByRole('button', { name: /Phone rotation/ }).click();
  await p.waitForTimeout(300);
  const text = await p.locator('.immersive-bar').innerText();
  const status = (await im(p)).orientation;
  await p.context().close();
  current = page;
  assert(status === 'unsupported' && /not available/.test(text), `status ${status}`);
  return { status, label: 'Rotation not available' };
});

// --------------------------------------------------------------- stairs in first person (walk yourself)
await check('G09_first_person_stairs_prompt_and_keys', async () => {
  const p = await open({ width: 1440, height: 900 });
  current = p;
  await building(p, FACTORY);
  await devRoute(p, 'Office 1.04', 'Rooms', 'CNC mill H-A1-A', 'Assets');
  await p.evaluate(() => window.__arinavGuidance.manualClock(true));
  await p.getByRole('button', { name: 'Start navigation' }).click();
  await p.evaluate(() => window.__arinavGuidance.advance(4)); // the walker stands in the staircase
  await p.getByRole('button', { name: 'First person' }).click();
  await p.getByRole('button', { name: /Walk myself/ }).click();
  await p.waitForTimeout(800);
  const a = await im(p);
  const prompt = p.locator('.pov-hud__ride');
  await prompt.waitFor({ timeout: 5000 });
  const text = await prompt.innerText();
  await p.screenshot({ path: join(OUT, 'G09-stairs-prompt-1440.png') });
  await p.mouse.move(900, 450);
  await p.keyboard.press('q');
  await p.waitForTimeout(600);
  const b = await im(p);
  await p.screenshot({ path: join(OUT, 'G09-after-stairs-1440.png') });
  await p.context().close();
  current = page;
  assert(/Stairs here/.test(text) && /Ground floor/.test(text), `prompt "${text}"`);
  assert(a.pose.floor_id !== b.pose.floor_id, `still on ${b.pose.floor_id}`);
  return { prompt: text.replace(/\s+/g, ' '), from: a.pose.floor_id, to: b.pose.floor_id, space: b.pose.space_id };
});

// --------------------------------------------------------------- mobile + reference viewport visuals
for (const [name, vp] of [
  ['M01-portrait-390x844', { width: 390, height: 844 }],
  ['M02-landscape-844x390', { width: 844, height: 390 }],
  ['Q02-reference-1000x860', { width: 1000, height: 860 }],
]) {
  await check(`${name}_navigation_view_usable`, async () => {
    const p = await open(vp, '?profile=immersive', vp.width < 900 ? { isMobile: true, hasTouch: true } : {});
    current = p;
    await building(p, CAMPUS);
    await devRoute(p, '3.01', 'Rooms', 'exit', 'Exits');
    await p.evaluate(() => window.__arinavGuidance.manualClock(true));
    await p.getByRole('button', { name: 'Start navigation' }).click();
    await p.evaluate(() => window.__arinavGuidance.advance(6));
    await p.getByRole('button', { name: 'First person' }).click();
    await p.waitForTimeout(1200);
    await p.screenshot({ path: join(OUT, `${name}-pov.png`) });
    // the 3D view must not be covered: sample the centre band's top element
    const blocked = await p.evaluate(() => {
      const w = innerWidth;
      const h = innerHeight;
      let covered = 0;
      let total = 0;
      for (let x = 0.3; x <= 0.7; x += 0.1)
        for (let y = 0.35; y <= 0.6; y += 0.05) {
          total++;
          const el = document.elementFromPoint(w * x, h * y);
          if (el && !el.closest('.viewer') && !el.classList.contains('look-layer')) covered++;
        }
      return covered / total;
    });
    const mic = await p.getByRole('button', { name: 'Hold to talk' }).count();
    await p.getByRole('button', { name: 'Overview' }).first().click();
    await p.waitForTimeout(800);
    await p.screenshot({ path: join(OUT, `${name}-overview.png`) });
    await p.context().close();
    current = page;
    assert(blocked <= 0.25, `centre of the view covered ${(blocked * 100).toFixed(0)}%`);
    return { centre_covered_pct: Math.round(blocked * 100), mic_buttons: mic };
  });
}

// --------------------------------------------------------------- core profile: new modules off
await check('A02_core_profile_switches_new_modules_off', async () => {
  const p = await open({ width: 1440, height: 900 }, '?profile=core');
  current = p;
  await building(p, CAMPUS);
  await devRoute(p, '3.01', 'Rooms', 'exit', 'Exits').catch(() => {});
  const bar = await p.locator('.immersive-bar').count();
  const card = await p.locator('.guidance').count();
  const facts = await p.locator('[aria-label="Route result"]').count();
  await p.screenshot({ path: join(OUT, 'A02-core-1440.png') });
  await p.goto(BASE + '?profile=immersive');
  await p.context().close();
  current = page;
  assert(bar === 0 && card === 0, `immersive UI visible in core (bar ${bar}, card ${card})`);
  assert(facts === 1, 'manual routing did not work in core');
  return { immersive_bar: bar, guidance_card: card, route_result: facts };
});

results.push({ name: 'no_page_errors_desktop', pass: desktopErrors.length === 0, detail: desktopErrors.slice(0, 5) });
const passed = results.filter((r) => r.pass).length;
writeFileSync(join(OUT, 'immersive_e2e.json'), JSON.stringify({ generated: new Date().toISOString(), passed, total: results.length, results }, null, 1));
console.log(`${passed}/${results.length} immersive checks passed -> ${OUT}`);
await browser.close();
process.exit(passed === results.length ? 0 : 1);
