// Phase B browser acceptance: virtual navigation in AriNav with a controlled simulation clock
// (dev build handle window.__arinavGuidance: snapshot / manualClock / advance). Needs the app on
// :4321 (frontend) and :8000 (backend), the dev buildings, playwright-core and installed Chrome.
//   PW_MODULE=<playwright-core path> node scripts/e2e/guidance_e2e.mjs [--out docs/reports/browser-b]
// Pipeline starts are blocked by the harness (no HQ generation, no cost). Every check records
// pass/fail with details; failures keep a screenshot. Exit code 1 if anything failed.
import { createRequire } from 'node:module';
import { mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE || 'playwright-core');
const args = process.argv.slice(2);
const OUT = args.includes('--out') ? args[args.indexOf('--out') + 1] : 'docs/reports/browser-b';
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const API = process.env.ARINAV_API || 'http://localhost:8000/api';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const CAMPUS = 'proc-campus-dev-001';
const FACTORY = 'proc-factory-dev-003';
mkdirSync(OUT, { recursive: true });
for (const f of readdirSync(OUT)) if (f.startsWith('fail-')) rmSync(join(OUT, f));

const results = [];
const browser = await chromium.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
});

async function newPage(options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.errors = [];
  page.pipelineStarts = 0;
  page.on('pageerror', (e) => page.errors.push(e.message));
  await page.route('**/api/pipeline-runs', (r) => {
    if (r.request().method() === 'POST') {
      page.pipelineStarts += 1;
      return r.fulfill({ status: 503, body: '{"detail":"blocked by test"}' });
    }
    return r.continue();
  });
  if (options.voice) {
    await page.route('**/api/assistant/status', async (r) => {
      const res = await r.fetch();
      const body = await res.json();
      return r.fulfill({ response: res, json: { ...body, voice: true } });
    });
    await page.route('**/api/assistant/speak', (r) => r.fulfill({ status: 502, body: '{"detail":"TTS down (test)"}' }));
  }
  await page.goto(BASE, { waitUntil: 'networkidle' });
  return page;
}

let current = null;
async function check(name, fn) {
  const began = Date.now();
  try {
    const detail = await fn();
    results.push({ name, pass: true, ms: Date.now() - began, detail: detail ?? null });
    console.log(`PASS ${name}${detail ? ' — ' + JSON.stringify(detail).slice(0, 180) : ''}`);
  } catch (error) {
    results.push({ name, pass: false, ms: Date.now() - began, error: String(error).slice(0, 800) });
    console.log(`FAIL ${name} — ${String(error).slice(0, 400)}`);
    if (current) await current.screenshot({ path: join(OUT, `fail-${name}.png`) }).catch(() => {});
  }
}
const assert = (cond, message) => {
  if (!cond) throw new Error(message);
};

async function api(page, method, path, body) {
  const r = await page.request.fetch(API + path, { method, data: body });
  return { status: r.status(), body: await r.json().catch(() => null) };
}
async function reset(page, b) {
  await api(page, 'POST', '/ingestions', { dataset: b });
  const s = (await api(page, 'GET', `/nav/${b}/state`)).body;
  if (s?.closed?.length) await api(page, 'POST', `/nav/${b}/scenario`, { action: 'reset', expected_revision: s.revision });
}
async function openBuilding(page, b) {
  if (await page.getByRole('tab', { name: 'Location' }).count()) await page.getByRole('tab', { name: 'Location' }).click();
  await page.selectOption('#building-select', b);
  await page.waitForFunction((d) => document.querySelector('#building-select')?.value === d && document.querySelector('.topbar__building'), b);
  await page.waitForTimeout(1200);
}
async function developer(page) {
  await page.getByRole('group', { name: 'Mode' }).getByRole('button', { name: 'Developer' }).click();
  await page.getByRole('tab', { name: 'Developer' }).click();
}
async function endpoint(page, query, which, kind) {
  const box = page.getByLabel('Search endpoints');
  await box.fill('');
  await page.getByRole('group', { name: 'Search kind' }).getByRole('button', { name: kind, exact: true }).click();
  await box.fill(query);
  const row = page.locator('.dev-result').first();
  await row.waitFor({ timeout: 15000 });
  await row.getByRole('button', { name: which, exact: true }).click();
}
async function route(page, startQ, startKind, targetQ, targetKind) {
  const req = page.waitForRequest((r) => r.url().endsWith('/route') && r.method() === 'POST');
  await endpoint(page, startQ, 'Start', startKind);
  await endpoint(page, targetQ, 'Target', targetKind);
  await page.getByRole('button', { name: 'Compute route' }).click();
  const request = JSON.parse((await req).postData());
  await page.locator('.guidance').waitFor({ timeout: 15000 });
  return request;
}
const snap = (page) =>
  page.evaluate(() => {
    const s = window.__arinavGuidance.snapshot();
    return {
      status: s.status,
      d: s.distance_m,
      total: s.total_m,
      rev: s.route_revision,
      floor: s.progress?.floor_id ?? null,
      pos: s.progress?.position ?? null,
      space: s.progress?.space_id ?? null,
      msg: s.message,
      mode: s.route?.mode,
      checked: s.route?.checked,
      target: s.route?.target ?? null,
      elements: s.route?.element_path ?? [],
      waypoints: s.route?.waypoints ?? [],
      path: s.route?.path ?? [],
      transitions: s.route?.transitions ?? [],
    };
  });
const manual = (page) => page.evaluate(() => window.__arinavGuidance.manualClock(true));
const advance = (page, s, step = 0.1) => page.evaluate(([x, st]) => window.__arinavGuidance.advance(x, st), [s, step]);
const close = (a, b) => Math.abs(a - b) < 1e-6;
const samePoint = (p, q) => p && q && close(p[0], q[0]) && close(p[1], q[1]);

// ------------------------------------------------------------------ campus: official exit route
const page = await newPage();
current = page;
await reset(page, CAMPUS);
await openBuilding(page, CAMPUS);
await developer(page);
const request = await route(page, '3.01', 'Rooms', 'exit', 'Exits');
await manual(page);

await check('01_preview_keeps_user_position', async () => {
  const before = await snap(page);
  await page.getByRole('button', { name: 'Preview route' }).click();
  await page.waitForTimeout(2500);
  const during = await snap(page);
  await page.screenshot({ path: join(OUT, 'e2e-01-preview.png') });
  await page.getByRole('button', { name: /Skip preview|Preview route/ }).first().click();
  const pose = await page.evaluate(() => window.__arinavGuidance.pose());
  assert(during.d === before.d && during.status === 'idle', `walker moved during preview: ${JSON.stringify(during)}`);
  assert(pose === null, 'a walker was shown before Start navigation');
  return { distance: during.d, status: during.status };
});

await check('02_route_and_endpoints_match_backend', async () => {
  const s = await snap(page);
  const backend = (await api(page, 'POST', `/nav/${CAMPUS}/route`, request)).body;
  assert(backend.status === 'ok', 'backend route not ok');
  assert(JSON.stringify(backend.waypoints) === JSON.stringify(s.waypoints), 'waypoints differ from the backend');
  assert(JSON.stringify(backend.element_path) === JSON.stringify(s.elements), 'element path differs');
  assert(samePoint(backend.start.position, s.waypoints[0].position), 'start differs');
  assert(samePoint(backend.target.position, s.waypoints.at(-1).position), 'target differs');
  return { waypoints: s.waypoints.length, length_m: backend.length_m };
});

const floorsSeen = [];
await check('05_pause_resume_no_jump', async () => {
  await page.getByRole('button', { name: 'Start navigation' }).click();
  await advance(page, 6);
  const a = await snap(page);
  floorsSeen.push(a.floor);
  await page.getByRole('button', { name: 'Pause' }).click();
  await advance(page, 30);
  const b = await snap(page);
  await page.getByRole('button', { name: 'Resume' }).click();
  await advance(page, 0.1);
  const c = await snap(page);
  assert(b.status === 'paused' && b.d === a.d, 'walker moved while paused');
  const jump = Math.hypot(c.pos[0] - a.pos[0], c.pos[1] - a.pos[1]);
  assert(c.status === 'active' && jump < 0.2, `jump after resume: ${jump}`);
  return { paused_at_m: +a.d.toFixed(2), jump_m: +jump.toFixed(3) };
});

await check('13_user_and_developer_view_same_location', async () => {
  await page.waitForTimeout(400); // let the panel render the latest snapshot
  const dev = await snap(page);
  const devText = await page.locator('.guidance__next').innerText();
  await page.getByRole('group', { name: 'Mode' }).getByRole('button', { name: 'Assistant' }).click();
  await page.waitForTimeout(400);
  const user = await snap(page);
  const userText = await page.locator('.guidance__next').innerText();
  await page.screenshot({ path: join(OUT, 'e2e-13-user-view.png') });
  const debugInUser = await page.locator('.guidance__debug').count();
  await developer(page);
  assert(samePoint(dev.pos, user.pos) && dev.d === user.d, 'views show different positions');
  assert(devText === userText, `instruction differs between views: ${devText} | ${userText}`);
  assert(debugInUser === 0, 'debug details visible in the user view');
  return { position: user.pos, instruction: userText.split('\n')[0] };
});

await check('08_floor_changes_in_order_and_03_exit_route_ends_at_exit_door', async () => {
  for (let i = 0; i < 200; i++) {
    const s = await snap(page);
    if (s.floor && floorsSeen.at(-1) !== s.floor) floorsSeen.push(s.floor);
    if (s.status !== 'active') break;
    await advance(page, 0.5, 0.05);
  }
  const end = await snap(page);
  await page.screenshot({ path: join(OUT, 'e2e-03-arrived.png') });
  const model = (await api(page, 'GET', `/buildings/${CAMPUS}/model`)).body;
  const door = model.openings.find((o) => o.id === end.target.id);
  assert(end.status === 'arrived', `not arrived: ${end.status}`);
  assert(door && door.space_ids.includes('outside'), 'target is not an exit door');
  assert(samePoint(end.pos, door.position), `arrived at ${end.pos}, exit door at ${door?.position}`);
  const levels = floorsSeen.map((f) => model.floors.find((x) => x.id === f).level);
  assert(JSON.stringify(levels) === JSON.stringify([3, 2, 1, 0]), `floor order ${levels}`);
  return { floors: levels, arrived_at: end.pos };
});

await check('06_closure_invalidates_guidance_and_reroutes_from_position', async () => {
  await page.getByRole('button', { name: 'Done' }).click();
  await page.getByRole('button', { name: /Start navigation|Restart navigation/ }).click();
  await advance(page, 5);
  const before = await snap(page);
  const stairs = before.elements.find((e) => /-V\d+$/.test(e));
  const st = (await api(page, 'GET', `/nav/${CAMPUS}/state`)).body;
  await api(page, 'POST', `/nav/${CAMPUS}/scenario`, { action: 'close', element_id: stairs, expected_revision: st.revision });
  await page.waitForFunction((r) => window.__arinavGuidance.snapshot().route_revision > r, before.rev, { timeout: 15000 });
  await page.waitForFunction(() => window.__arinavGuidance.snapshot().status === 'active', null, { timeout: 15000 });
  const after = await snap(page);
  await page.screenshot({ path: join(OUT, 'e2e-06-rerouted.png') });
  assert(!after.elements.includes(stairs), 'new route still uses the closed stairs');
  assert(samePoint(after.pos, before.pos), `walker jumped from ${before.pos} to ${after.pos}`);
  return { closed: stairs, new_elements: after.elements.length, position_kept: true };
});

await check('07_missing_route_stops_the_simulation', async () => {
  const model = (await api(page, 'GET', `/buildings/${CAMPUS}/model`)).body;
  let st = (await api(page, 'GET', `/nav/${CAMPUS}/state`)).body;
  for (const c of model.vertical_connectors) {
    if (st.closed.includes(c.id)) continue;
    st = (await api(page, 'POST', `/nav/${CAMPUS}/scenario`, { action: 'close', element_id: c.id, expected_revision: st.revision })).body;
  }
  await page.waitForFunction(() => window.__arinavGuidance.snapshot().status === 'blocked', null, { timeout: 15000 });
  const a = await snap(page);
  await advance(page, 20);
  const b = await snap(page);
  const text = await page.locator('.guidance').innerText();
  await page.screenshot({ path: join(OUT, 'e2e-07-blocked.png') });
  await api(page, 'POST', `/nav/${CAMPUS}/scenario`, { action: 'reset', expected_revision: st.revision });
  assert(b.status === 'blocked' && samePoint(a.pos, b.pos), 'walker moved without a route');
  assert(/No route/.test(text), 'no explanation shown');
  assert(!/proc-|-V\d|-O\d{4}|-S\d{3}/.test(text), `technical ids in the user message: ${text}`);
  return { message: a.msg };
});

await check('15_U1_clearing_the_start_removes_the_route', async () => {
  await page.getByRole('button', { name: 'Stop' }).click().catch(() => {});
  await page.getByRole('button', { name: 'Clear start' }).click();
  await page.waitForTimeout(300);
  const facts = await page.locator('[aria-label="Route result"]').count();
  const s = await snap(page);
  assert(facts === 0 || !(await page.locator('[aria-label="Route result"]').innerText()).includes('ok'), 'stale route still shown');
  return { guidance_status: s.status };
});

// ------------------------------------------------------------------ factory: precision + HQ cache
await check('16_cached_hq_model_loads_without_regeneration', async () => {
  await reset(page, FACTORY);
  const startsBefore = page.pipelineStarts;
  await openBuilding(page, FACTORY);
  await page.getByRole('button', { name: /HQ ready/ }).waitFor({ timeout: 30000 });
  await openBuilding(page, CAMPUS);
  await openBuilding(page, FACTORY);
  await page.getByRole('button', { name: /HQ ready/ }).waitFor({ timeout: 30000 });
  assert(page.pipelineStarts === startsBefore, `pipeline started ${page.pipelineStarts - startsBefore}x`);
  return { pipeline_starts: page.pipelineStarts - startsBefore };
});

await check('04_09_precision_walker_stays_on_checked_route_and_modes_differ', async () => {
  await developer(page);
  await route(page, 'Office 0.', 'Rooms', 'CNC mill', 'Assets');
  const official = await snap(page);
  const officialBadge = await page.locator('.guidance .route-badge').innerText().catch(() => '');
  await page.getByRole('group', { name: 'Route mode' }).getByRole('button', { name: 'Precision' }).click();
  await page.getByRole('button', { name: 'Compute route' }).click();
  await page.waitForFunction(() => window.__arinavGuidance.snapshot().route?.mode === 'precision', null, { timeout: 20000 });
  const pre = await snap(page);
  const preBadge = await page.locator('.guidance .route-badge').innerText().catch(() => '');
  await page.getByRole('button', { name: 'Start navigation' }).click();
  const segs = [...pre.path, ...pre.transitions.filter((t) => t.kind !== 'door').map((t) => ({ floor_id: t.to_floor_id, points: [t.position, t.to_position ?? t.position] }))];
  let worst = 0;
  for (let i = 0; i < 400; i++) {
    const s = await snap(page);
    const dist = Math.min(
      ...segs.filter((g) => g.floor_id === s.floor).flatMap((g) =>
        g.points.slice(1).map((b, k) => {
          const a = g.points[k];
          const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
          const len2 = dx * dx + dy * dy || 1;
          const t = Math.max(0, Math.min(1, ((s.pos[0] - a[0]) * dx + (s.pos[1] - a[1]) * dy) / len2));
          return Math.hypot(s.pos[0] - (a[0] + t * dx), s.pos[1] - (a[1] + t * dy));
        }),
      ),
    );
    worst = Math.max(worst, dist);
    if (s.status !== 'active') break;
    await advance(page, 0.3, 0.05);
  }
  const end = await snap(page);
  await page.screenshot({ path: join(OUT, 'e2e-04-precision.png') });
  await page.getByRole('group', { name: 'Route mode' }).getByRole('button', { name: 'Official' }).click();
  assert(official.mode === 'official' && pre.mode === 'precision', 'modes not distinguishable');
  assert(officialBadge !== preBadge, `same badge ${officialBadge}`);
  assert(['obstacle_checked', 'schematic_vertical'].includes(pre.checked), `precision not checked: ${pre.checked}`);
  assert(worst < 1e-6, `walker left the checked polyline by ${worst} m`);
  assert(end.status === 'arrived' && samePoint(end.pos, pre.waypoints.at(-1).position), 'precision walker did not arrive at the target point');
  return { official: officialBadge, precision: preBadge, max_offset_m: worst };
});

await check('12_missing_hq_does_not_block_map_use', async () => {
  await openBuilding(page, CAMPUS);
  await page.getByRole('button', { name: /LQ only/ }).waitFor({ timeout: 20000 });
  await developer(page);
  await route(page, '3.01', 'Rooms', 'exit', 'Exits');
  const s = await snap(page);
  assert(s.route !== null || s.waypoints.length > 0, 'no route in LQ mode');
  return { quality: await page.locator('.quality').innerText(), waypoints: s.waypoints.length };
});

const pageErrors = [...page.errors];

// ------------------------------------------------------------------ voice failure + sessions
await check('11_audio_errors_do_not_block_navigation', async () => {
  const vp = await newPage({ voice: true });
  current = vp;
  await openBuilding(vp, CAMPUS);
  await developer(vp);
  await route(vp, '3.01', 'Rooms', 'exit', 'Exits');
  await manual(vp);
  const voiceButton = vp.getByRole('button', { name: /Voice (on|off)/ });
  if ((await voiceButton.innerText()).includes('off')) await voiceButton.click(); // hints on
  await vp.getByRole('button', { name: 'Start navigation' }).click();
  await advance(vp, 200);
  await vp.waitForTimeout(800);
  const s = await snap(vp);
  const hint = await vp.locator('.guidance__hint').innerText().catch(() => '');
  await vp.screenshot({ path: join(OUT, 'e2e-11-voice-error.png') });
  await vp.context().close();
  current = page;
  assert(s.status === 'arrived', `navigation blocked by audio: ${s.status}`);
  assert(/Spoken hints paused/.test(hint), 'the audio failure was not shown');
  return { status: s.status, hint };
});

await check('14_sessions_do_not_share_positions', async () => {
  const a = await newPage();
  const b = await newPage();
  for (const p of [a, b]) {
    await openBuilding(p, CAMPUS);
    await developer(p);
    await route(p, '3.01', 'Rooms', 'exit', 'Exits');
    await manual(p);
    await p.getByRole('button', { name: 'Start navigation' }).click();
  }
  await advance(a, 20);
  await advance(b, 3);
  const [sa, sb] = [await snap(a), await snap(b)];
  await a.context().close();
  await b.context().close();
  assert(Math.abs(sa.d - sb.d) > 10, `positions coupled: ${sa.d} vs ${sb.d}`);
  return { session_a_m: +sa.d.toFixed(1), session_b_m: +sb.d.toFixed(1) };
});

results.push({ name: 'no_page_errors', pass: pageErrors.length === 0, detail: pageErrors.slice(0, 5) });
const passed = results.filter((r) => r.pass).length;
writeFileSync(join(OUT, 'guidance_e2e.json'), JSON.stringify({ generated: new Date().toISOString(), passed, total: results.length, results }, null, 1));
console.log(`${passed}/${results.length} guidance checks passed -> ${OUT}`);
await browser.close();
process.exit(passed === results.length ? 0 : 1);
