// The app asks "Who are you?" on a tab's first visit (AccountDialog): e2e pages log in through it.
//   await login(page)            // the first free person
//   await login(page, 'Sam')     // a free person whose tile names "Sam"
//   await closePage(page)        // log out (frees the person at once), then close the context
// A person stays taken for the lease time (30 s) when a context closes without logging out, and
// only a few people are configured: close pages with closePage. Logging in makes the person's
// location the building the page opens next (their saved place there is kept).

const DIALOG = 'dialog.account-dialog';

/** Log the page in as a free person; their name, or null when no people are configured. */
export async function login(page, name = null, { timeout = 15000 } = {}) {
  const dialog = page.locator(DIALOG);
  try {
    await page.waitForFunction((sel) => document.querySelector(sel)?.open, DIALOG, { timeout });
  } catch {
    // No dialog: nobody to log in as (no profiles), or the page resumed its session.
    if (await page.evaluate(() => sessionStorage.getItem('arinav:user-session'))) return 'resumed';
    return null;
  }
  let tiles = dialog.locator('.accounts__tile:not([disabled])');
  if (name) tiles = tiles.filter({ hasText: name });
  try {
    await tiles.first().waitFor({ timeout });
  } catch {
    const taken = await dialog.locator('.accounts__tile').allInnerTexts();
    throw new Error(`no free person${name ? ` named ${name}` : ''} to log in as: ${taken.join(' | ').replace(/\n/g, ' ')}`);
  }
  const tile = tiles.first();
  const who = (await tile.locator('.accounts__name').innerText()).trim();
  await tile.click();
  await page.waitForFunction((sel) => !document.querySelector(sel)?.open, DIALOG, { timeout });
  return who;
}

/** The page's session token (for API calls as that person), or null. */
export async function sessionToken(page) {
  return page.evaluate(() => {
    const raw = sessionStorage.getItem('arinav:user-session');
    return raw ? JSON.parse(raw).token : null;
  });
}

/** Log out: the person is free again at once (instead of after the lease). */
export async function logout(page) {
  const token = await sessionToken(page).catch(() => null);
  if (!token) return;
  await page.request
    .fetch(new URL('/api/user-sessions/current', page.url()).toString(), {
      method: 'DELETE',
      headers: { 'X-AriNav-Session': token },
    })
    .catch(() => {});
}

export async function closePage(page) {
  await logout(page);
  await page.context().close();
}
