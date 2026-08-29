/**
 * Rain as a holder uses it: list, open, detail, empty, missing, and the one
 * link that used to dump people on Register.
 *
 * The rendered-console matrix still audits Rain at every width and theme.
 * This file is the click-through: a hash-only "Open a rain" resolved against
 * `<base href="/">` as `/#create`, which is the registry, whose primary
 * action is "Register an upkeep". Pinning the form on `/rain#create` kept
 * people on rain but left the list looking like two products. Opening one
 * is `/rain/new`, the way registering an upkeep is `/register`.
 */

import { expect, test, type Page } from '@playwright/test';

import { CANONICAL_APP_ID, RAIN_APP_ID, stubAlgod, stubWebFonts } from './chain';

async function settled(page: Page): Promise<void> {
  await expect(page.locator('.status .mono')).not.toHaveText(/connecting/, { timeout: 15_000 });
  await expect(page.locator('.status .mono')).toContainText('testnet-v1.0');
}

async function openHub(page: Page): Promise<void> {
  await stubWebFonts(page);
  await stubAlgod(page);
  await page.goto(`/rain?app=${CANONICAL_APP_ID}`);
  await settled(page);
  await expect(page.getByRole('heading', { name: 'Rains' })).toBeVisible();
  await expect(page.getByText('Corvid daily')).toBeVisible();
}

async function stillRain(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/rain(?:\/new|\/\d+)?(?:\?|$|#)/);
  await expect(page.getByRole('heading', { name: 'Register an upkeep' })).toHaveCount(0);
}

test.describe('rain hub', () => {
  test('Open a rain stays on rain and never goes to register', async ({ page }) => {
    await openHub(page);

    await page.getByRole('link', { name: 'Open a rain' }).click();

    await expect(page).toHaveURL(/\/rain\/new(?:\?|$)/);
    const url = new URL(page.url());
    expect(url.pathname, 'Open a rain dropped /rain and landed on the registry').toBe('/rain/new');
    expect(url.pathname).not.toBe('/');
    expect(url.pathname).not.toBe('/register');
    expect(url.pathname).not.toBe('/rain');
    expect(url.hash).not.toBe('#create');

    await expect(page.getByRole('heading', { name: 'Open a rain' })).toBeVisible();
    await stillRain(page);
    await expect(page.getByRole('heading', { name: 'Rains' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Back to rains' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Register an upkeep', exact: true })).toHaveCount(0);
    await expect(page.getByText('Who it falls on')).toBeVisible();
    await expect(page.getByText('Connect an account above to open one.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Open this rain' })).toBeDisabled();
  });

  test('the same bug does not come back at phone width', async ({ page }) => {
    await stubWebFonts(page);
    await stubAlgod(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/rain?app=${CANONICAL_APP_ID}`);
    await settled(page);

    await page.getByRole('link', { name: 'Open a rain' }).click();

    await expect(page).toHaveURL(/\/rain\/new(?:\?|$)/);
    await stillRain(page);
    await expect(page.getByRole('heading', { name: 'Open a rain' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Register an upkeep' })).toHaveCount(0);
  });

  test('empty-hub Open one stays on rain, not the registry', async ({ page }) => {
    await stubWebFonts(page);
    await stubAlgod(page, { emptyRains: true });
    await page.goto(`/rain?app=${CANONICAL_APP_ID}`);
    await settled(page);

    await expect(page.getByText('No rains on this hub yet.')).toBeVisible();
    await page.getByRole('link', { name: 'Open one' }).click();

    await expect(page).toHaveURL(/\/rain\/new(?:\?|$)/);
    await stillRain(page);
    await expect(page.getByRole('heading', { name: 'Open a rain' })).toBeVisible();
  });

  test('the table lists every stub rain and a row opens its detail', async ({ page }) => {
    await openHub(page);

    const daily = page.getByRole('row', { name: /Corvid daily/ });
    const gm = page.getByRole('row', { name: /Corvid GM/ });
    const lottery = page.getByRole('row', { name: /Corvid lottery/ });
    const asa = page.getByRole('row', { name: /live ASA split/ });
    await expect(daily).toBeVisible();
    await expect(gm).toBeVisible();
    await expect(lottery).toBeVisible();
    await expect(asa).toBeVisible();
    await expect(daily).toContainText('Everyone');
    await expect(gm).toContainText('Who shows up');
    await expect(lottery).toContainText('One person');
    await expect(daily).toContainText('Corvid NFT');
    await expect(daily).toContainText('connect to check');
    await expect(daily).toContainText('746557513');
    await expect(asa).toContainText('770131837');
    await expect(asa).toContainText('Rain Drops');

    await daily.getByRole('link', { name: 'Corvid daily' }).click();

    await expect(page).toHaveURL(/\/rain\/1(?:\?|$)/);
    await expect(page.getByRole('heading', { name: 'Corvid daily' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Back to rains' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'The pot' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'What anyone can do here' })).toBeVisible();
    await expect(page.getByText('Split across everyone who entered')).toBeVisible();
    await expect(page.getByText('Connect an account above to enter, check in, or claim.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Enter this rain' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'I am here' })).toHaveCount(0);
    await stillRain(page);

    await page.getByRole('link', { name: 'Back to rains' }).click();
    await expect(page).toHaveURL(/\/rain(?:\?|$)/);
    await expect(page.getByRole('heading', { name: 'Rains' })).toBeVisible();
  });

  test('the GM rain has WAVE facts, not a lottery or a register form', async ({ page }) => {
    await openHub(page);
    await page.getByRole('row', { name: /Corvid GM/ }).getByRole('link', { name: 'Corvid GM' }).click();
    await expect(page).toHaveURL(/\/rain\/2(?:\?|$)/);
    await expect(page.getByRole('heading', { name: 'Corvid GM' })).toBeVisible();
    await expect(page.getByText('Who shows up', { exact: true })).toBeVisible();
    await expect(page.getByText('The first 10 to check in this drop')).toBeVisible();
    await expect(page.getByText('This drop', { exact: true })).toBeVisible();
    await expect(page.getByText('0 / 10')).toBeVisible();
    await expect(page.getByRole('button', { name: 'I am here' })).toHaveCount(0);
    await stillRain(page);
  });

  test('the lottery rain is one person, and missing ids say so', async ({ page }) => {
    await openHub(page);
    await page.getByRole('row', { name: /Corvid lottery/ }).getByRole('link', { name: 'Corvid lottery' }).click();
    await expect(page).toHaveURL(/\/rain\/3(?:\?|$)/);
    await expect(page.getByRole('heading', { name: 'Corvid lottery' })).toBeVisible();
    await expect(page.getByText('One person', { exact: true })).toBeVisible();
    await expect(page.getByText('One random ticket each fire')).toBeVisible();
    await expect(page.getByText('This drop', { exact: true })).toHaveCount(0);
    await stillRain(page);

    await page.goto(`/rain/99?app=${CANONICAL_APP_ID}`);
    await settled(page);
    await expect(page.getByRole('heading', { name: 'No rain 99 on this hub.' })).toBeVisible();
    await page.getByRole('link', { name: 'See what is open' }).click();
    await expect(page).toHaveURL(/\/rain(?:\?|$|#)/);
    await expect(page.getByRole('heading', { name: 'Rains' })).toBeVisible();
  });

  test('the hub chrome is the console chrome, not a keeper-tile lie', async ({ page }) => {
    await openHub(page);

    await expect(page.getByRole('heading', { name: /This deployment is not frozen/ })).toBeVisible();
    await expect(page.getByText('4 boxes on this hub')).toBeVisible();
    await expect(page.getByText('across every rain', { exact: true })).toBeVisible();
    await expect(page.getByText('a rain can fire on the next draw')).toBeVisible();
    await expect(page.getByText('ALGO, plus 1 ASA rain', { exact: true })).toBeVisible();
    await expect(page.getByText('across 5 upkeeps')).toHaveCount(0);
    await expect(page.getByText('executable by anyone, right now')).toHaveCount(0);
    await expect(page.getByRole('link', { name: String(RAIN_APP_ID) })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Activity' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Open a rain' })).toHaveCount(0);
  });

  test('Rain and Register in the nav are different destinations', async ({ page }) => {
    await openHub(page);

    const nav = page.getByRole('navigation', { name: 'Arcron' });
    await nav.getByRole('link', { name: 'Register', exact: true }).click();
    await expect(page).toHaveURL(/\/register/);
    await expect(page.getByRole('heading', { name: 'Register an upkeep' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Open a rain' })).toHaveCount(0);

    await nav.getByRole('link', { name: 'Rain', exact: true }).click();
    await expect(page).toHaveURL(/\/rain(?:\?|$)/);
    await expect(page.getByRole('heading', { name: 'Rains' })).toBeVisible();

    await page.getByRole('link', { name: 'Open a rain' }).click();
    await expect(page).toHaveURL(/\/rain\/new(?:\?|$)/);
    await stillRain(page);
    await expect(page.getByRole('heading', { name: 'Open a rain' })).toBeVisible();
  });

  test('the registry Register an upkeep is not the rain CTA', async ({ page }) => {
    await stubWebFonts(page);
    await stubAlgod(page);
    await page.goto(`/?app=${CANONICAL_APP_ID}`);
    await settled(page);

    await page.getByRole('link', { name: 'Register an upkeep' }).click();
    await expect(page).toHaveURL(/\/register/);
    await expect(page.getByRole('heading', { name: 'Register an upkeep' })).toBeVisible();

    await page.getByRole('navigation', { name: 'Arcron' }).getByRole('link', { name: 'Rain', exact: true }).click();
    await expect(page).toHaveURL(/\/rain(?:\?|$)/);
    await expect(page.getByRole('heading', { name: 'Rains' })).toBeVisible();
    await page.getByRole('link', { name: 'Open a rain' }).click();
    await expect(page).toHaveURL(/\/rain\/new(?:\?|$)/);
    await stillRain(page);
  });

  test('phone drawer Register an upkeep is not Open a rain', async ({ page }) => {
    await stubWebFonts(page);
    await stubAlgod(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/rain?app=${CANONICAL_APP_ID}`);
    await settled(page);

    await page.getByRole('button', { name: 'Open menu' }).click();
    const drawer = page.getByRole('navigation', { name: 'Console' });
    await expect(drawer.getByRole('link', { name: 'Rain', exact: true })).toBeVisible();
    await drawer.getByRole('link', { name: 'Register an upkeep' }).click();
    await expect(page).toHaveURL(/\/register/);
    await expect(page.getByRole('heading', { name: 'Register an upkeep' })).toBeVisible();

    await page.getByRole('button', { name: 'Open menu' }).click();
    await page.getByRole('navigation', { name: 'Console' }).getByRole('link', { name: 'Rain', exact: true }).click();
    await expect(page).toHaveURL(/\/rain(?:\?|$)/);
    await expect(page.getByRole('heading', { name: 'Rains' })).toBeVisible();

    await page.getByRole('link', { name: 'Open a rain' }).click();
    await expect(page).toHaveURL(/\/rain\/new(?:\?|$)/);
    await stillRain(page);
  });

  test('a rain at phone width still opens its own detail', async ({ page }) => {
    await stubWebFonts(page);
    await stubAlgod(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/rain?app=${CANONICAL_APP_ID}`);
    await settled(page);
    await expect(page.getByText('Corvid daily')).toBeVisible();

    await page.getByRole('link', { name: 'Corvid daily' }).click();
    await expect(page).toHaveURL(/\/rain\/1(?:\?|$)/);
    await expect(page.getByRole('heading', { name: 'Corvid daily' })).toBeVisible();
    await stillRain(page);

    await page.getByRole('link', { name: 'Back to rains' }).click();
    await expect(page).toHaveURL(/\/rain(?:\?|$)/);
  });

  test('clicking a middle cell opens that rain, not the last row', async ({ page }) => {
    await openHub(page);

    await page.getByRole('row', { name: /Corvid daily/ }).click();
    await expect(page).toHaveURL(/\/rain\/1(?:\?|$)/);
    await page.getByRole('link', { name: 'Back to rains' }).click();

    await page.getByRole('row', { name: /Corvid GM/ }).click();
    await expect(page).toHaveURL(/\/rain\/2(?:\?|$)/);
    await page.getByRole('link', { name: 'Back to rains' }).click();

    await page.getByRole('row', { name: /Corvid lottery/ }).click();
    await expect(page).toHaveURL(/\/rain\/3(?:\?|$)/);
    await stillRain(page);
  });

  test('a rain row is one link covering the row, not the id and the name separately', async ({ page }) => {
    await openHub(page);
    const daily = page.getByRole('row', { name: /Corvid daily/ });
    await expect(daily.getByRole('link')).toHaveCount(1);
    await expect(daily.locator('img.thumb')).toHaveAttribute('src', /corvid-0001\.png/);
    await expect(page.locator('img[src*="mascot"]')).toHaveCount(0);
    await daily.click();
    await expect(page).toHaveURL(/\/rain\/1(?:\?|$)/);
    await expect(page.getByRole('heading', { name: 'Corvid daily' })).toBeVisible();
    await stillRain(page);
  });

  test('an ASA rain shows the asset id and the opt-in on its page', async ({ page }) => {
    await openHub(page);
    const asa = page.getByRole('row', { name: /live ASA split/ });
    await expect(asa).toContainText('770131837');
    await expect(asa.getByText('waiting', { exact: true })).toBeVisible();
    await asa.click();
    await expect(page).toHaveURL(/\/rain\/4(?:\?|$)/);
    await expect(page.getByRole('heading', { name: 'live ASA split' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Prize' })).toBeVisible();
    await expect(page.getByRole('link', { name: /Asset 770131837/ }).first()).toBeVisible();
    await expect(page.getByText(/Connect to opt in to ASA 770131837/)).toBeVisible();
    await expect(page.getByText(/Entering still costs/)).toBeVisible();
    await stillRain(page);
  });

  test('Registry, Rain and Register land at the top, not a jump down', async ({ page }) => {
    await stubWebFonts(page);
    await stubAlgod(page);
    await page.goto(`/?app=${CANONICAL_APP_ID}`);
    await settled(page);

    const nav = page.getByRole('navigation', { name: 'Arcron' });
    await nav.getByRole('link', { name: 'Rain', exact: true }).click();
    await settled(page);
    await expect(page.getByRole('heading', { name: 'Rains' })).toBeVisible();
    expect(await page.evaluate(() => window.scrollY)).toBeLessThan(8);

    await nav.getByRole('link', { name: 'Register', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Register an upkeep' })).toBeVisible();
    expect(await page.evaluate(() => window.scrollY)).toBeLessThan(8);

    await nav.getByRole('link', { name: 'Registry', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Upkeep registry' })).toBeVisible();
    expect(await page.evaluate(() => window.scrollY)).toBeLessThan(8);
  });

  test('Back to rains from the create page returns to the hub', async ({ page }) => {
    await openHub(page);
    await page.getByRole('link', { name: 'Open a rain' }).click();
    await expect(page).toHaveURL(/\/rain\/new(?:\?|$)/);
    await page.getByRole('link', { name: 'Back to rains' }).click();
    await expect(page).toHaveURL(/\/rain(?:\?|$)/);
    await expect(page.getByRole('heading', { name: 'Rains' })).toBeVisible();
    await stillRain(page);
  });
});
