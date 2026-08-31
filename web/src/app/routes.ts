/**
 * Three destinations — the keeper registry, one upkeep, register — plus three
 * temporary stubs where Rain used to be.
 *
 * Registry and Keeper board stay two tabs on `/` — they are two readings of
 * the same boxes. Rain was three routes here on the reasoning that a different
 * contract with a holder-facing surface deserved routes rather than a tab; it
 * turned out to deserve its own address, because its reader holds an NFT and
 * this console's reader is a developer reading selectors and escrow runway.
 * Rain now lives at https://corvidlabs.xyz/rain/.
 *
 * The three paths stay declared, and stay ordered `rain/new` before
 * `rain/:id` so "new" is still not read as an id, because a console that
 * spreads by shared links cannot let old links rot: with an index.html
 * fallback a deleted `/rain/3` renders the registry rather than 404ing, which
 * silently shows the wrong page. Each forwards to its counterpart at the new
 * address instead — see `pages/rain-moved.ts` — and all three are retired
 * together once the announced 30-day window closes.
 *
 * Network and app id stay in the query string.
 */

import type { RouterConfigOptions, Routes } from '@angular/router';

/**
 * `?network=` and `?app=` say which chain and which registry the page is
 * showing, so they belong to every destination rather than to one of them.
 * Preserving them by default means no link in the console has to remember,
 * and the one place that changes them (`app.ts`) asks for 'merge' explicitly.
 *
 * Declared here rather than inline in `app.config.ts` so it can be asserted
 * without booting Angular; `app.config.ts` passes exactly this object to
 * `withRouterConfig`.
 */
export const routerOptions: RouterConfigOptions = {
  defaultQueryParamsHandling: 'preserve',
};

export const routes: Routes = [
  {
    path: '',
    pathMatch: 'full',
    title: 'Arcron keeper network console',
    loadComponent: () => import('./pages/registry-page').then((module) => module.RegistryPage),
  },
  {
    path: 'u/:id',
    title: 'Upkeep · Arcron',
    loadComponent: () => import('./pages/upkeep-page').then((module) => module.UpkeepPage),
  },
  {
    path: 'register',
    title: 'Register an upkeep · Arcron',
    loadComponent: () => import('./pages/register-page').then((module) => module.RegisterPage),
  },
  {
    path: 'rain',
    title: 'Rain has moved',
    loadComponent: () => import('./pages/rain-moved').then((module) => module.RainMoved),
  },
  {
    path: 'rain/new',
    title: 'Rain has moved',
    loadComponent: () => import('./pages/rain-moved').then((module) => module.RainCreateMoved),
  },
  {
    path: 'rain/:id',
    title: 'Rain has moved',
    loadComponent: () => import('./pages/rain-moved').then((module) => module.RainDetailMoved),
  },
  // A mistyped path is not a reason to lose the network and app the visitor
  // arrived with, and Angular carries the query string through a redirect.
  { path: '**', redirectTo: '' },
];
