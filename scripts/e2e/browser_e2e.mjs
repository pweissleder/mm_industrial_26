// Browser end-to-end checks of AriNav against the running app (frontend :4321, backend :8000).
// Uses playwright-core with the installed Chrome (no browser download):
//   set PW_MODULE=<path to node_modules/playwright-core>  (or have playwright-core resolvable)
//   node scripts/e2e/browser_e2e.mjs [--live] [--out docs/reports/browser]
// Offline mode (default) covers manual use without AI: building choice, search, developer
// routing (official + precision), exit route, nearest exit, scenario close/reset, nav-graph layer
// and the assistant's explicit offline state. --live adds real assistant turns (Claude) incl. an
// exit route and an ambiguous follow-up; it costs a few cents.
import { createRequire } from 'node:module';
import { mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { login, logout } from './login.mjs';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE || 'playwright-core');
const args = process.argv.slice(2);
const LIVE = args.includes('--live');
const OUT = args.includes('--out') ? args[args.indexOf('--out') + 1] : 'docs/reports/browser';
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
mkdirSync(OUT, { recursive: true });
// Failure screenshots belong to one run; drop the previous run's.
for (const f of readdirSync(OUT)) if (f.startsWith('fail-')) rmSync(join(OUT, f));

const results = [];
const consoleErrors = [];
async function step(name, fn) {
  const began = Date.now();
  try {
    const detail = await fn();
    results.push({ name, pass: true, ms: Date.now() - began, detail: detail ?? null });
    console.log(`PASS ${name}${detail ? ' — ' + JSON.stringify(detail).slice(0, 160) : ''}`);
  } catch (error) {
    results.push({ name, pass: false, ms: Date.now() - began, error: String(error).slice(0, 600) });
    console.log(`FAIL ${name} — ${String(error).slice(0, 300)}`);
    await page.screenshot({ path: join(OUT, `fail-${name}.png`) }).catch(() => {});
  }
}

// VOICE_WAV: a 16 kHz WAV that Chrome's fake microphone plays (scripts/e2e/make_voice_sample.py).
const VOICE_WAV = process.env.VOICE_WAV;
const chromeArgs = ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'];
if (VOICE_WAV) {
  chromeArgs.push('--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${VOICE_WAV}`);
}
const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: chromeArgs });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
await context.grantPermissions(['microphone'], { origin: BASE });
const page = await context.newPage();
page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()));
page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message));
const navResponses = [];
page.on('response', async (r) => {
  const url = r.url();
  if (url.includes('/api/nav/') && url.endsWith('/route')) {
    try { navResponses.push({ status: r.status(), body: await r.json() }); } catch { /* ignore */ }
  }
});
const shot = (name) => page.screenshot({ path: join(OUT, `${name}.png`) });

async function openBuilding(dataset) {
  if (await page.getByRole('tab', { name: 'Location' }).count()) await tab('Location');
  await page.selectOption('#building-select', dataset);
  await page.waitForFunction((d) => document.querySelector('.topbar__building') && document.querySelector('#building-select')?.value === d, dataset, { timeout: 60000 });
  await page.waitForTimeout(1500);
}
async function mode(name) {
  await page.getByRole('group', { name: 'Mode' }).getByRole('button', { name }).click();
  await page.waitForTimeout(300);
}
async function tab(name) {
  await page.getByRole('tab', { name }).click();
}
async function endpoint(query, which, kind) {
  const box = page.getByLabel('Search endpoints');
  await box.fill('');
  if (kind) await page.getByRole('group', { name: 'Search kind' }).getByRole('button', { name: kind, exact: true }).click();
  await box.fill(query);
  const row = page.locator('.dev-result').first();
  await row.waitFor({ timeout: 15000 });
  const label = (await row.locator('.list__title').innerText()).trim();
  await row.getByRole('button', { name: which, exact: true }).click();
  return label;
}
async function computeRoute() {
  const before = navResponses.length;
  await page.getByRole('button', { name: 'Compute route' }).click();
  await page.waitForFunction((n) => window.__navCount === undefined || true, before);
  for (let i = 0; i < 120 && navResponses.length === before; i++) await page.waitForTimeout(250);
  if (navResponses.length === before) throw new Error('no /route response');
  await page.locator('[aria-label="Route result"]').waitFor({ timeout: 15000 });
  return navResponses[navResponses.length - 1];
}

await step('app_loads', async () => {
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await login(page);
  await page.locator('#building-select').waitFor();
  const n = await page.locator('#building-select option').count();
  if (n < 2) throw new Error(`only ${n} buildings listed`);
  return { buildings: n };
});

await step('open_campus_building', async () => {
  await openBuilding('proc-campus-dev-001');
  await page.locator('canvas').first().waitFor();
  await shot('01-campus-loaded');
  return { name: await page.locator('.topbar__building').innerText() };
});

await step('sidebar_search_room', async () => {
  await tab('Location');
  await page.getByLabel('Search locations').fill('Office');
  const hits = page.locator('.sidebar .list li');
  await hits.first().waitFor({ timeout: 10000 });
  return { hits: await hits.count() };
});

await step('developer_route_multifloor_to_exit_door', async () => {
  await mode('Developer');
  await tab('Developer');
  const start = await endpoint('3.', 'Start', 'Rooms');
  const target = await endpoint('exit', 'Target', 'Exits');
  const r = await computeRoute();
  const route = r.body;
  if (r.status !== 200 || route.status !== 'ok') throw new Error(`route ${r.status} ${route.status}`);
  const last = route.waypoints.at(-1);
  if (last.kind !== 'target' || last.element_id !== route.target.id) throw new Error('route does not end at the exit door');
  const floors = new Set(route.path.map((p) => p.floor_id));
  const lastSeg = route.path.at(-1);
  if (lastSeg.kind === 'connector') throw new Error('route geometry ends on a stair/lift, not at the door');
  await page.getByRole('button', { name: 'Fit to view' }).click().catch(() => {});
  await page.waitForTimeout(800);
  await shot('02-campus-route-to-exit');
  const facts = await page.locator('[aria-label="Route result"]').innerText();
  return { start, target, floors: floors.size, length_m: route.length_m, factsHasOk: facts.includes('ok') };
});

await step('nearest_exit_button', async () => {
  await page.getByRole('button', { name: 'Nearest exit' }).click();
  await page.getByText('Nearest exits').waitFor({ timeout: 20000 });
  return null;
});

await step('scenario_close_select_and_reset', async () => {
  await tab('Location');
  await page.getByLabel('Search locations').fill('Lift');
  const first = page.locator('.sidebar .list li button, .sidebar .list li').first();
  await first.click();
  await tab('Developer');
  const close = page.locator('.section', { hasText: 'Scenario' }).getByRole('button', { name: 'Close', exact: true });
  await close.click();
  await page.locator('.section', { hasText: 'Scenario' }).getByText(/Revision \d+ · 1 closed/).first().waitFor({ timeout: 10000 });
  await shot('03-scenario-closed');
  await page.locator('.section', { hasText: 'Scenario' }).getByRole('button', { name: 'Reset all' }).click();
  await page.locator('.section', { hasText: 'Scenario' }).getByText(/^Revision \d+ · 0 closed/).first().waitFor({ timeout: 10000 });
  return null;
});

await step('factory_precision_route_and_asset', async () => {
  await openBuilding('proc-factory-dev-003');
  await tab('Developer');
  await endpoint('Office 0.', 'Start', 'Rooms');
  const target = await endpoint('CNC mill', 'Target', 'Assets');
  const official = (await computeRoute()).body;
  if (official.status !== 'ok') throw new Error('official route to asset: ' + official.status);
  await page.getByRole('group', { name: 'Route mode' }).getByRole('button', { name: 'Precision' }).click();
  const r = await computeRoute();
  const ok = r.status === 200 ? r.body.status : `${r.status} ${r.body?.error?.code}`;
  await page.waitForTimeout(600);
  await shot('04-factory-precision');
  await page.getByRole('group', { name: 'Route mode' }).getByRole('button', { name: 'Official' }).click();
  return { target, official_m: official.length_m, precision: ok, precision_m: r.body?.length_m ?? null };
});

await step('nav_graph_layer_loads', async () => {
  await tab('View');
  const toggle = page.getByText('Nav graph', { exact: true });
  await toggle.click();
  await page.waitForTimeout(1500);
  await shot('05-nav-graph-layer');
  await toggle.click();
  return null;
});

await step('assistant_mode_state', async () => {
  await mode('Assistant');
  const status = await page.locator('[aria-label="Chat with AriNav"] [role="status"]').innerText();
  if (!LIVE && !/offline/i.test(status)) throw new Error('expected an explicit offline state, got ' + status);
  return { status };
});

if (LIVE) {
  const turns = [];
  page.on('response', async (r) => {
    if (r.url().endsWith('/api/assistant/turn')) {
      try { turns.push({ status: r.status(), body: await r.json() }); } catch { /* ignore */ }
    }
  });
  async function say(text) {
    const n = turns.length;
    const input = page.locator('[aria-label="Chat with AriNav"] input[type="text"], [aria-label="Chat with AriNav"] input').first();
    await input.fill(text);
    await page.getByRole('button', { name: 'Send' }).click();
    for (let i = 0; i < 360 && turns.length === n; i++) await page.waitForTimeout(250);
    if (turns.length === n) throw new Error('no assistant turn response');
    await page.waitForTimeout(800);
    return turns.at(-1);
  }
  await step('live_exit_route_from_first_floor', async () => {
    const t = await say("I'm in Office 1.04. How do I get out of the building?");
    if (t.status !== 200) throw new Error('turn HTTP ' + t.status);
    const route = t.body.ui?.route;
    await shot('06-live-exit-route');
    if (!route || route.status !== 'ok') throw new Error('no route shown: ' + t.body.reply);
    if (route.waypoints.at(-1).element_id !== route.target.id || route.target.kind !== 'opening') throw new Error('route does not end at an exit door');
    return { reply: t.body.reply, usd: t.body.usage?.estimated_usd, total_ms: t.body.timing_ms?.total_ms };
  });
  await step('live_ambiguous_follow_up', async () => {
    const t = await say('And the connection from there, the bigger one?');
    if (t.status !== 200) throw new Error('turn HTTP ' + t.status);
    const reply = t.body.reply;
    if (/budget|limit|error|tool/i.test(reply)) throw new Error('raw limit/error text: ' + reply);
    await shot('07-live-ambiguous');
    return { reply, status: t.body.status, rounds: t.body.usage?.rounds, usd: t.body.usage?.estimated_usd };
  });
  await step('live_closure_needs_confirmation', async () => {
    const t = await say('Please close the office lift.');
    const proposal = t.body.ui?.proposal;
    if (!proposal) throw new Error('no proposal: ' + t.body.reply);
    await page.getByRole('button', { name: 'Confirm' }).click();
    await page.waitForTimeout(1500);
    const state = await (await page.request.get(BASE + '/api/nav/proc-factory-dev-003/state')).json();
    if (!state.closed.includes(proposal.element_id)) throw new Error('confirmed closure not in scenario');
    await shot('08-live-confirmed-closure');
    await page.request.post(BASE + '/api/nav/proc-factory-dev-003/scenario', { data: { action: 'reset', expected_revision: state.revision } });
    return { element: proposal.element_name, revision: state.revision };
  });

  if (VOICE_WAV) {
    await step('live_voice_push_to_talk', async () => {
      const n = turns.length;
      const transcripts = [];
      const onResp = async (r) => {
        if (r.url().endsWith('/api/assistant/transcribe')) {
          try { transcripts.push({ status: r.status(), body: await r.json() }); } catch { /* ignore */ }
        }
      };
      page.on('response', onResp);
      const talk = page.getByRole('button', { name: 'Hold to talk' });
      const box = await talk.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.down();
      await page.waitForTimeout(5200);
      await page.mouse.up();
      for (let i = 0; i < 360 && turns.length === n; i++) await page.waitForTimeout(250);
      page.off('response', onResp);
      const stt = transcripts.at(-1);
      if (!stt || stt.status !== 200) throw new Error('transcription failed: ' + JSON.stringify(stt));
      const t = turns.at(-1);
      if (!t || turns.length === n) throw new Error('no assistant turn after speech');
      await page.waitForTimeout(1500);
      const shown = await page.locator('.chat__msg--user').last().innerText();
      await shot('09-live-voice');
      if (!/office/i.test(stt.body.text) || !/exit/i.test(stt.body.text)) throw new Error('unexpected transcript: ' + stt.body.text);
      if (!shown.includes(stt.body.text.slice(0, 12))) throw new Error('transcript not shown as the user message');
      if (!t.body.audio_pcm16) throw new Error('no spoken reply: ' + (t.body.voice_error || 'missing audio'));
      return { transcript: stt.body.text, stt_ms: stt.body.stt_ms, reply: t.body.reply, tts_ms: t.body.timing_ms?.tts_ms, total_ms: t.body.timing_ms?.total_ms, route: t.body.ui?.route?.status };
    });
  }
}

const relevantErrors = consoleErrors.filter((e) => !/favicon|DevTools|WebGL|GPU stall|GroupMarkerNotSet/i.test(e));
results.push({ name: 'no_console_errors', pass: relevantErrors.length === 0, detail: relevantErrors.slice(0, 10) });
const passed = results.filter((r) => r.pass).length;
const report = { generated: new Date().toISOString(), base: BASE, live: LIVE, passed, total: results.length, results };
writeFileSync(join(OUT, LIVE ? 'browser_e2e_live.json' : 'browser_e2e.json'), JSON.stringify(report, null, 1));
console.log(`${passed}/${results.length} browser checks passed -> ${OUT}`);
await logout(page);
await browser.close();
process.exit(passed === results.length ? 0 : 1);
