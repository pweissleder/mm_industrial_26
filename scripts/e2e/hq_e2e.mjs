// Night acceptance for the HQ showcase (matrix rows Q03-Q06): saved models are reused by building
// id, an explicit Rebuild really runs, a moved thing makes the HQ view honestly "outdated", and the
// frame time is measured (software GL in headless Chrome; no hardware claims).
// Runs against the real app (:4321/:8000) with an OFFLINE backend (no API key: deterministic pipeline).
//   PW_MODULE=<playwright-core> node scripts/e2e/hq_e2e.mjs [--out docs/night/e2e]
import { createRequire } from 'node:module';
import { mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { closePage, login } from './login.mjs';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE || 'playwright-core');
const args = process.argv.slice(2);
const OUT = args.includes('--out') ? args[args.indexOf('--out') + 1] : 'docs/night/e2e';
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const API = process.env.ARINAV_API || 'http://localhost:8000/api';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const SHOWCASE = ['proc-factory-dev-003', 'proc-campus-dev-001', 'swiss-1689'];
mkdirSync(OUT, { recursive: true });
for (const f of readdirSync(OUT)) if (f.startsWith('fail-hq')) rmSync(join(OUT, f));

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
    console.log(`PASS ${name}${detail ? ' — ' + JSON.stringify(detail).slice(0, 240) : ''}`);
  } catch (e) {
    results.push({ name, pass: false, ms: Date.now() - t, error: String(e).slice(0, 800) });
    console.log(`FAIL ${name} — ${String(e).slice(0, 300)}`);
    if (current) await current.screenshot({ path: join(OUT, `fail-hq-${name}.png`) }).catch(() => {});
  }
}
const get = async (path) => (await fetch(API + path)).json();

async function open(viewport, { blockRuns = true, query = '?profile=showcase', mutate = null, ...ctx } = {}) {
  const context = await browser.newContext({ viewport, ...ctx });
  const page = await context.newPage();
  page.log = { posts: 0, glb: 0 };
  page.on('request', (r) => {
    if (r.url().includes('/api/pipeline-runs') && r.method() === 'POST') page.log.posts++;
    if (r.url().endsWith('/model.glb') || r.url().includes('/model.glb?')) page.log.glb++;
  });
  if (blockRuns)
    await page.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
  if (mutate) await page.route(mutate.pattern, mutate.handler);
  await page.goto(BASE + query, { waitUntil: 'networkidle' });
  await login(page);
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
}
const chip = (p) => p.locator('.pipeline-chip').getAttribute('aria-label').then((t) => t ?? '').catch(() => '');
const badge = (p) => p.locator('.quality').textContent().then((t) => (t ?? '').trim()).catch(() => '');
async function waitChip(p, re, ms = 20000) {
  const t = Date.now();
  while (Date.now() - t < ms) {
    if (re.test(await chip(p))) return chip(p);
    await p.waitForTimeout(250);
  }
  throw new Error(`chip stayed "${await chip(p)}"`);
}
async function frameTimes(p, n = 40) {
  // Real render cost: draw the scene n times and wait for the GPU each time (readPixels sync).
  return p.evaluate((count) => {
    const s = window.__arinavScene;
    const r = s.renderer;
    const gl = r.getContext();
    const px = new Uint8Array(4);
    const pov = window.__arinavImmersive?.look()?.view === 'pov';
    const cam = pov ? s.povCamera : s.camera;
    const d = [];
    for (let i = 0; i < count; i++) {
      const t = performance.now();
      r.render(s.scene, cam);
      gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
      d.push(performance.now() - t);
    }
    d.sort((a, b) => a - b);
    const q = (x) => +d[Math.min(d.length - 1, Math.floor(d.length * x))].toFixed(1);
    const info = r.info.render;
    return { frames: count, camera: pov ? 'pov' : 'overview', median_ms: q(0.5), p95_ms: q(0.95), est_fps: +(1000 / q(0.5)).toFixed(0), draw_calls: info.calls, triangles: info.triangles };
  }, n);
}

// ------------------------------------------------------------- Q03: saved geometry reused by id
const runsBefore = Object.fromEntries(await Promise.all(SHOWCASE.map(async (d) => [d, (await get(`/pipeline-runs?dataset=${d}`)).length])));
const page = await open({ width: 1440, height: 900 });
current = page;
for (const id of SHOWCASE) {
  await check(`Q03_${id}_opens_saved_hq_without_a_run`, async () => {
    await building(page, id);
    const c = await waitChip(page, /HQ ready|HQ outdated/);
    await page.waitForTimeout(2500);
    await panel(page, false);
    await page.screenshot({ path: join(OUT, `HQ-${id}-1440.png`) });
    const b = await badge(page);
    assert(/HQ ready/.test(c), `chip "${c}"`);
    assert(b === 'HQ', `quality badge "${b}"`);
    return { chip: c, badge: b, name: await page.locator('.topbar__building').innerText() };
  });
}
await check('Q03_second_open_reuses_the_file_no_generation', async () => {
  const glbBefore = page.log.glb;
  await building(page, SHOWCASE[0]);
  await waitChip(page, /HQ ready/);
  await page.waitForTimeout(1500);
  const runsAfter = Object.fromEntries(await Promise.all(SHOWCASE.map(async (d) => [d, (await get(`/pipeline-runs?dataset=${d}`)).length])));
  assert(page.log.posts === 0, `${page.log.posts} pipeline starts requested`);
  assert(JSON.stringify(runsAfter) === JSON.stringify(runsBefore), `runs changed ${JSON.stringify(runsAfter)}`);
  return { pipeline_posts: page.log.posts, glb_fetches_total: page.log.glb, glb_fetches_second_open: page.log.glb - glbBefore, runs: runsAfter };
});

// ------------------------------------------------------------- Q06: measured frame time
await check('Q06_frame_time_desktop_overview_hq', async () => {
  await building(page, SHOWCASE[0]);
  await waitChip(page, /HQ ready/);
  await page.waitForTimeout(1500);
  const ft = await frameTimes(page);
  return { ...ft, context: 'headless Chrome, SwiftShader software GL, 1440x900, this laptop' };
});
await closePage(page);

await check('Q06_frame_time_mobile_emulation_overview', async () => {
  const p = await open({ width: 390, height: 844 }, { isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  current = p;
  const cdp = await p.context().newCDPSession(p);
  await building(p, SHOWCASE[1]);
  await waitChip(p, /HQ ready/);
  await panel(p, false);
  await p.waitForTimeout(1500);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 });
  const ft = await frameTimes(p);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 });
  await p.screenshot({ path: join(OUT, 'HQ-campus-overview-390.png') });
  await closePage(p);
  current = null;
  return { ...ft, context: '390x844 DPR2 emulation, CPU throttled 4x, SwiftShader; not a real phone' };
});

// ------------------------------------------------------------- Q05: a moved thing outdates HQ
await check('Q05_moved_thing_shows_live_view_and_outdated_hq', async () => {
  const id = SHOWCASE[0];
  let moved = null;
  const p = await open({ width: 1440, height: 900 }, {
    mutate: {
      pattern: `**/api/buildings/${id}/model`,
      handler: async (route) => {
        // the router's building after an agent's move_asset (same as scenario_sync writes)
        const res = await route.fetch();
        const data = await res.json();
        const a = data.assets.find((x) => /chair/i.test(x.type)) ?? data.assets[0];
        a.position = [a.position[0] + 1.5, a.position[1]];
        moved = a.name;
        await route.fulfill({ response: res, json: data });
      },
    },
  });
  current = p;
  await building(p, id);
  const c = await waitChip(p, /HQ outdated|HQ ready/);
  await p.waitForTimeout(2000);
  await panel(p, false);
  const b = await badge(p);
  await p.screenshot({ path: join(OUT, 'HQ-Q05-outdated-1440.png') });
  await p.locator('.pipeline-chip').click();
  await p.waitForTimeout(500);
  const note = await p.locator('dialog[open] [role="note"]').innerText().catch(() => '');
  await p.screenshot({ path: join(OUT, 'HQ-Q05-dialog-1440.png') });
  await closePage(p);
  current = null;
  assert(/HQ outdated/.test(c), `chip "${c}"`);
  assert(b === 'Low', `badge "${b}" (the baked HQ furniture must not be shown)`);
  assert(note.includes(moved), `dialog note "${note.slice(0, 120)}"`);
  return { moved, chip: c, badge: b, note: note.slice(0, 160) };
});

// ------------------------------------------------------------- Q04: explicit Rebuild executes
await check('Q04_explicit_rebuild_runs_with_unchanged_input', async () => {
  const id = SHOWCASE[1];
  const before = await get(`/locations/${id}`);
  const p = await open({ width: 1440, height: 900 }, { blockRuns: false });
  current = p;
  await building(p, id);
  await waitChip(p, /HQ ready/);
  await p.locator('.pipeline-chip').click();
  await p.getByRole('button', { name: 'Rebuild' }).click();
  await waitChip(p, /HQ ready/, 120000);
  const [run] = await get(`/pipeline-runs?dataset=${id}`);
  const after = await get(`/locations/${id}`);
  await p.keyboard.press('Escape');
  await p.waitForTimeout(1500);
  await p.screenshot({ path: join(OUT, 'HQ-Q04-after-rebuild-1440.png') });
  await closePage(p);
  current = null;
  const stages = Object.fromEntries(Object.entries(run.stages).map(([k, v]) => [k, v.status]));
  assert(run.status === 'completed', `run ${run.status}`);
  assert(stages.floorplan === 'passed' && stages.equipment === 'passed', `stages ${JSON.stringify(stages)}`);
  assert(after.updated_at !== before.updated_at, 'model was not rewritten');
  return { run: run.run_id, offline: run.offline, stages, updated_before: before.updated_at, updated_after: after.updated_at };
});

await browser.close();
const passed = results.filter((r) => r.pass).length;
writeFileSync(join(OUT, 'hq_e2e.json'), JSON.stringify({ when: new Date().toISOString(), passed, total: results.length, results }, null, 2));
console.log(`${passed}/${results.length} HQ checks passed -> ${OUT}`);
process.exit(passed === results.length ? 0 : 1);
