/**
 * Six destinations: the keeper registry, one upkeep, register, Rain, open a
 * rain, and one rain.
 *
 * Registry and Keeper board stay two tabs on `/` — they are two readings of
 * the same boxes. Rain is a different contract (the scheduled draw) with a
 * holder-facing surface, so it is its own route rather than a tab on the
 * registry. Opening a rain is a page, the way registering an upkeep is, not a
 * form pinned under the list. `rain/new` is declared before `rain/:id` so
 * "new" is not read as an id. Network and app id stay in the query string;
 * Rain reads its own app id from the network config and does not steal `?app=`.
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
    title: 'Rain · Arcron',
    loadComponent: () => import('./pages/rain-page').then((module) => module.RainPage),
  },
  {
    path: 'rain/new',
    title: 'Open a rain · Arcron',
    loadComponent: () => import('./pages/rain-create-page').then((module) => module.RainCreatePage),
  },
  {
    path: 'rain/:id',
    title: 'A rain · Arcron',
    loadComponent: () => import('./pages/rain-detail-page').then((module) => module.RainDetailPage),
  },
  // A mistyped path is not a reason to lose the network and app the visitor
  // arrived with, and Angular carries the query string through a redirect.
  { path: '**', redirectTo: '' },
];
