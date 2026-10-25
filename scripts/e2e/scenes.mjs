// Night task: three full visual scenes against the LIVE app (real Claude turns, small cost),
// recorded as video (Playwright recordVideo, 1280x720) plus screenshots for the guide.
//   1 coffee -> route -> walk (overview + first person) -> arrival
//   2 wheelchair while the only lift is closed -> honest "no step-free route" -> alternative
//   3 walk yourself in first person -> "what's on my right?"
// Usage: PW_MODULE=<playwright-core> node scripts/e2e/scenes.mjs [--out docs/night/scenes] [--only 1,3]
// Never clicks Rebuild/Build HQ (that would start a paid pipeline run in live mode).
import { createRequire } from 'node:module';
import { mkdirSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PW_MODULE || 'playwright-core');
const args = process.argv.slice(2);
const OUT = args.includes('--out') ? args[args.indexOf('--out') + 1] : 'docs/night/scenes';
const VIDEO = args.includes('--video') ? args[args.indexOf('--video') + 1] : join(process.env.TEMP || '.', 'arinav-scenes-video');
const ONLY = args.includes('--only') ? args[args.indexOf('--only') + 1].split(',') : ['1', '2', '3'];
const BASE = process.env.ARINAV_URL || 'http://localhost:4321';
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const FACTORY = 'proc-factory-dev-003';
mkdirSync(OUT, { recursive: true });
mkdirSync(VIDEO, { recursive: true });

const browser = await chromium.launch({ executablePath: CHROME, headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const log = [];
const note = (scene, what, extra = {}) => {
  const e = { t: new Date().toISOString(), scene, what, ...extra };
  log.push(e);
  console.log(`[${scene}] ${what}${extra.reply ? ' — ' + extra.reply.slice(0, 160) : ''}`);
};

async function open(name) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, recordVideo: { dir: VIDEO, size: { width: 1280, height: 720 } } });
  const page = await context.newPage();
  page.sceneName = name;
  await page.route('**/api/pipeline-runs', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 503, body: '{}' }) : r.continue()));
  await page.goto(BASE + '/?profile=immersive', { waitUntil: 'networkidle' });
  // the Astro dev toolbar is a development overlay, not part of the app
  await page.addStyleTag({ content: 'astro-dev-toolbar{display:none!important}' });
  return page;
}
async function close(page) {
  const video = page.video();
  await page.context().close();
  const path = await video.path();
  const target = join(VIDEO, `${page.sceneName}.webm`);
  renameSync(path, target);
  return target;
}
const panel = async (p, openIt) => {
  const btn = p.getByRole('button', { name: openIt ? 'Show panel' : 'Hide panel' });
  if (await btn.count()) {
    await btn.click({ force: true });
    await p.waitForTimeout(400);
  }
};
async function building(p, id) {
  await panel(p, true);
  if (await p.getByRole('tab', { name: 'Location' }).count()) await p.getByRole('tab', { name: 'Location' }).click();
  await p.selectOption('#building-select', id);
  await p.waitForFunction((d) => document.querySelector('#building-select')?.value === d && document.querySelector('.topbar__building'), id);
  await p.waitForTimeout(2500);
  await panel(p, false);
  await p.waitForTimeout(800);
}
async function say(p, scene, text) {
  const input = p.getByPlaceholder(/Ask AriNav/);
  await input.click();
  await input.pressSequentially(text, { delay: 18 });
  await p.waitForTimeout(300);
  const before = await p.locator('.chat__msg--assistant, .chat__mini-reply').count();
  await input.press('Enter');
  await p.waitForTimeout(500);
  await p.waitForFunction(() => !document.querySelector('.chat__typing'), null, { timeout: 90000 });
  // the compact card shows the working phase ("Thinking…") instead of the typing row
  const last = () => p.locator('.chat__msg--assistant, .chat__mini-reply').last().innerText().catch(() => '');
  let reply = await last();
  for (let i = 0; i < 180 && /…\s*$/.test(reply.trim()); i++) {
    await p.waitForTimeout(500);
    reply = await last();
  }
  await p.waitForTimeout(600);
  note(scene, `said: ${text}`, { reply, replies_before: before });
  return reply;
}
const g = (p) => p.evaluate(() => window.__arinavGuidance?.snapshot());
async function shot(p, name) {
  await p.screenshot({ path: join(OUT, `${name}.png`) });
}

const videos = {};

// ---------------------------------------------------------------- scene 1
if (ONLY.includes('1')) {
  const p = await open('scene1_coffee_route_arrival');
  await building(p, FACTORY);
  await shot(p, 'S1-0-building');
  await say(p, 1, "I'm in Office 0.38. Where can I get a coffee?");
  await p.waitForTimeout(2500);
  await shot(p, 'S1-1-coffee-answer');
  await say(p, 1, 'And how do I get there?');
  await p.locator('.guidance').waitFor({ timeout: 20000 });
  await p.waitForTimeout(3000);
  await shot(p, 'S1-2-route');
  await p.getByRole('button', { name: 'Start navigation' }).click();
  await p.getByRole('button', { name: '2×', exact: true }).click().catch(() => {});
  await p.waitForTimeout(6000);
  await shot(p, 'S1-3-walking-overview');
  await p.getByRole('button', { name: 'First person' }).click();
  await p.waitForTimeout(9000);
  await shot(p, 'S1-4-walking-first-person');
  await p.getByRole('button', { name: 'Overview' }).first().click();
  await p.getByRole('button', { name: '8×', exact: true }).click().catch(() => {});
  const t0 = Date.now();
  while (Date.now() - t0 < 90000 && (await g(p))?.status !== 'arrived') await p.waitForTimeout(500);
  await p.waitForTimeout(2500);
  const s = await g(p);
  note(1, 'final status', { status: s?.status, distance: s?.distance_m, total: s?.total_m });
  await shot(p, 'S1-5-arrived');
  videos.scene1 = await close(p);
}

// ---------------------------------------------------------------- scene 2
if (ONLY.includes('2')) {
  const p = await open('scene2_wheelchair_lift_closed');
  await building(p, FACTORY);
  await say(p, 2, 'The office lift is out of order. Please close it.');
  await p.waitForTimeout(1500);
  const confirm = p.getByRole('button', { name: 'Confirm' }).last();
  if (await confirm.count()) {
    await confirm.click();
    await p.waitForTimeout(2500);
    note(2, 'confirmed closure');
  } else note(2, 'no proposal shown');
  await shot(p, 'S2-1-lift-closed');
  await say(p, 2, "I use a wheelchair. I'm at hall access 0.19 and need to get to Office 1.04 upstairs.");
  await p.waitForTimeout(3000);
  await shot(p, 'S2-2-no-step-free-route');
  await say(p, 2, 'Okay. Then take me to kitchen 0.28 instead.');
  await p.waitForTimeout(3500);
  await shot(p, 'S2-3-step-free-alternative');
  const s = await g(p);
  note(2, 'route', { status: s?.status, path: s?.route?.element_path?.length, constraints: s?.route?.constraints });
  videos.scene2 = await close(p);
}

// ---------------------------------------------------------------- scene 3
if (ONLY.includes('3')) {
  const p = await open('scene3_walk_whats_on_my_right');
  await building(p, FACTORY);
  await say(p, 3, "I'm in Office 0.38.");
  await p.waitForTimeout(1500);
  await p.getByRole('button', { name: 'First person' }).click();
  await p.waitForTimeout(1200);
  // First person starts walking from the stated location; otherwise switch it on explicitly
  const walk = p.getByRole('button', { name: /Walk myself/ });
  if (await walk.count()) await walk.click();
  await p.waitForTimeout(1500);
  await shot(p, 'S3-1-first-person-start');
  await p.mouse.move(800, 380);
  for (const [key, ms] of [['ArrowLeft', 900], ['w', 1200], ['ArrowRight', 1800]]) {
    await p.keyboard.down(key);
    await p.waitForTimeout(ms);
    await p.keyboard.up(key);
    await p.waitForTimeout(500);
  }
  await shot(p, 'S3-2-walked');
  const pose = await p.evaluate(() => window.__arinavImmersive?.pose());
  note(3, 'pose before question', { pose });
  await say(p, 3, "What's on my right?");
  await p.waitForTimeout(3500);
  await shot(p, 'S3-3-whats-on-my-right');
  videos.scene3 = await close(p);
}

await browser.close();
writeFileSync(join(OUT, 'scenes_log.json'), JSON.stringify({ when: new Date().toISOString(), videos, log }, null, 2));
console.log('videos', videos);
