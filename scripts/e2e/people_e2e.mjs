// Two people in two browsers (separate contexts = separate devices): they log in as different
// people, see each other live in "People here", one routes to the other, the route follows them
// when they move, and logging out shows them offline at their last place.
//   PW_MODULE=<playwright-core> CHROME_PATH=<chrome> ARINAV_URL=http://localhost:8080 \
//     node scripts/e2e/people_e2e.mjs [--out docs/reports/people]
// Needs two free people (the shipped profiles have three). The person A logs in as keeps the
// test's place afterwards (offline, in the factory's room S001).
import { createRequire } from 'node:module';
import { mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { closePage, login, logout, sessionToken } from './login.mjs';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE || 'playwright-core');
const args = process.argv.slice(2);
const OUT = args.includes('--out') ? args[args.indexOf('--out') + 1] : 'docs/reports/people';
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const FACTORY = 'proc-factory-dev-003';
const ROOM = `${FACTORY}-F0-S001`; // x in [-14.5, 0], y in [0, 17.1]
const SPOT = [-3, 4];
const MOVED = [-3, 9]; // 5 m on: well past the 1 m that triggers a new route
mkdirSync(OUT, { recursive: true });
for (const f of readdirSync(OUT)) if (f.startsWith('fail-')) rmSync(join(OUT, f));

const results = [];
const browser = await chromium.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
});

let current = null;
async function check(name, fn) {
  const began = Date.now();
  try {
    const detail = await fn();
    results.push({ name, pass: true, ms: Date.now() - began, detail: detail ?? null });
    console.log(`PASS ${name}${detail ? ' — ' + JSON.stringify(detail).slice(0, 200) : ''}`);
  } catch (error) {
    results.push({ name, pass: false, ms: Date.now() - began, error: String(error).slice(0, 800) });
    console.log(`FAIL ${name} — ${String(error).slice(0, 400)}`);
    if (current) await current.screenshot({ path: join(OUT, `fail-${name}.png`) }).catch(() => {});
  }
}
const assert = (cond, message) => {
  if (!cond) throw new Error(message);
};
const near = (p, q) => !!p && !!q && Math.hypot(p[0] - q[0], p[1] - q[1]) < 0.01;

async function open() {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  page.errors = [];
  page.on('pageerror', (e) => page.errors.push(e.message));
  await page.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.addStyleTag({ content: 'astro-dev-toolbar{display:none!important}' });
  return page;
}
async function openBuilding(page, b) {
  if (await page.getByRole('tab', { name: 'Location' }).count()) await page.getByRole('tab', { name: 'Location' }).click();
  await page.selectOption('#building-select', b);
  await page.waitForFunction((d) => document.querySelector('#building-select')?.value === d && document.querySelector('.topbar__building'), b, { timeout: 60000 });
  await page.waitForTimeout(1200);
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
/** Put the page's person somewhere, as their device would. */
async function place(page, position, extra = {}) {
  const r = await page.request.put(BASE + '/api/user-sessions/current/position', {
    headers: { 'X-AriNav-Session': await sessionToken(page) },
    data: { building_id: FACTORY, endpoint: { kind: 'point', space_id: ROOM, position }, ...extra },
  });
  assert(r.ok(), `position refused: ${r.status()} ${await r.text()}`);
}
const peopleText = (page) => page.locator('.people-list').innerText().catch(() => '');
/** Wait until the page's "People here" row for `name` matches. */
async function rowFor(page, name, pattern, timeout = 10000) {
  await page.waitForFunction(
    ([n, src]) =>
      [...document.querySelectorAll('.people-list li')].some((li) => li.innerText.includes(n) && new RegExp(src).test(li.innerText)),
    [name, pattern.source],
    { timeout },
  );
}
const isRoute = (r) => r.url().endsWith('/route') && r.request().method() === 'POST';

const a = await open();
const b = await open();
const who = { a: null, b: null, aId: null };

await check('01_two_people_log_in', async () => {
  current = a;
  who.a = await login(a);
  current = b;
  who.b = await login(b);
  assert(who.a && who.b, `login failed: ${who.a} / ${who.b} (are people configured?)`);
  assert(who.a !== who.b, `both pages are ${who.a}`);
  const directory = await (await a.request.get(BASE + '/api/users')).json();
  who.aId = directory.profiles.find((p) => p.name === who.a)?.id;
  assert(who.aId, `${who.a} is not in the directory`);
  const occupied = directory.profiles.filter((p) => p.occupied).map((p) => p.name);
  assert(occupied.includes(who.a) && occupied.includes(who.b), `not both taken: ${occupied}`);
  return { a: who.a, b: who.b };
});

await check('02_both_enter_the_factory', async () => {
  for (const p of [a, b]) {
    current = p;
    await openBuilding(p, FACTORY);
  }
  const users = (await (await a.request.get(BASE + `/api/buildings/${FACTORY}/users`)).json()).users;
  const here = users.map((u) => u.profile.name);
  assert(here.includes(who.a) && here.includes(who.b), `not both in the factory: ${here}`);
  return { here };
});

await check('03_a_place_appears_live_for_b', async () => {
  current = b;
  await place(a, SPOT, { label: 'E2E spot' });
  await rowFor(b, who.a, /E2E spot/);
  current = a;
  await rowFor(a, 'You', /E2E spot/);
  const first = (await a.locator('.people-list li').first().innerText()).split('\n')[0].trim();
  assert(first === 'You', `A's list does not start with "You": ${first}`);
  await b.screenshot({ path: join(OUT, 'people-01-b-sees-a.png') });
  return { b_list: (await peopleText(b)).replace(/\n/g, ' · ') };
});

await check('04_b_routes_to_a', async () => {
  current = b;
  await b.getByRole('group', { name: 'Mode' }).getByRole('button', { name: 'Developer' }).click();
  await b.getByRole('tab', { name: 'Developer' }).click();
  await endpoint(b, '0.23', 'Start', 'Rooms');
  await endpoint(b, who.a.split(' ')[0], 'Target', 'People');
  const response = b.waitForResponse(isRoute, { timeout: 20000 });
  await b.getByRole('button', { name: 'Compute route' }).click();
  const route = await (await response).json();
  assert(route.status === 'ok', `route ${route.status}: ${JSON.stringify(route.error ?? null)}`);
  assert(route.target_person?.id === who.aId, `route is not to ${who.a}: ${JSON.stringify(route.target_person)}`);
  assert(near(route.target.position, SPOT), `route ends at ${route.target.position}, not ${SPOT}`);
  await b.screenshot({ path: join(OUT, 'people-02-route-to-a.png') });
  return { length_m: route.length_m, target: route.target.position };
});

await check('05_route_follows_a', async () => {
  current = b;
  const rerouted = b.waitForResponse(isRoute, { timeout: 20000 });
  await place(a, MOVED, { moving: true });
  const route = await (await rerouted).json();
  assert(route.status === 'ok', `reroute ${route.status}`);
  assert(near(route.target.position, MOVED), `new route ends at ${route.target.position}, not ${MOVED}`);
  await b.waitForTimeout(500);
  await b.screenshot({ path: join(OUT, 'people-03-route-follows.png') });
  return { length_m: route.length_m, target: route.target.position };
});

await check('06_logging_out_shows_offline', async () => {
  current = b;
  await logout(a);
  await rowFor(b, who.a, /offline/, 15000);
  const users = (await (await b.request.get(BASE + `/api/buildings/${FACTORY}/users`)).json()).users;
  const left = users.find((u) => u.profile.id === who.aId);
  assert(left?.status === 'offline' && near(left.position?.position, MOVED), `A is ${JSON.stringify(left)}`);
  return { a: left.status, at: left.position.position };
});

const pageErrors = [...a.errors, ...b.errors];
results.push({ name: 'no_page_errors', pass: pageErrors.length === 0, detail: pageErrors.slice(0, 5) });
await closePage(a);
await closePage(b);
const passed = results.filter((r) => r.pass).length;
writeFileSync(join(OUT, 'people_e2e.json'), JSON.stringify({ generated: new Date().toISOString(), base: BASE, passed, total: results.length, results }, null, 1));
console.log(`${passed}/${results.length} people checks passed -> ${OUT}`);
await browser.close();
process.exit(passed === results.length ? 0 : 1);
