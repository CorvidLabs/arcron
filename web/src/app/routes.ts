/**
 * Three destinations, and no more.
 *
 * The console had no router at all: one scrolling page, two tabs, a register
 * form pinned under both, and no way to link to a single upkeep. An earlier
 * plan answered that by copying five destinations from NFDomains onto a
 * registry of five rows. Driving the console settled it the other way: the
 * page reads as one continuous surface and only Registry and Keeper board
 * genuinely compete, and they compete as two readings of the same boxes
 * rather than as two places. So they stay two tabs on `/`.
 *
 * What was actually missing was a destination per upkeep. Registering ended
 * nowhere, and nothing on the network could be linked to. See
 * `docs/console-plan.md`, "Decisions taken 2026-08-26", and
 * `docs/ac/j3-j4.md` section 3.1.
 *
 * Network and app id stay in the query string across all three, which
 * `withRouterConfig({ defaultQueryParamsHandling: 'preserve' })` in
 * `app.config.ts` is what makes true for every link on the page.
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
  // A mistyped path is not a reason to lose the network and app the visitor
  // arrived with, and Angular carries the query string through a redirect.
  { path: '**', redirectTo: '' },
];
