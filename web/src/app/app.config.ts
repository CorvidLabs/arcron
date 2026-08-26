import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import {
  provideRouter,
  withComponentInputBinding,
  withInMemoryScrolling,
  withRouterConfig,
} from '@angular/router';

import { routerOptions, routes } from './routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(
      routes,
      // Keeps `?network=` and `?app=` on every link in the console. See
      // `routes.ts`, where the options are declared.
      withRouterConfig(routerOptions),
      // `/u/:id` reads its id as an `input()`, so the route parameter is the
      // component's input rather than a subscription it has to manage.
      withComponentInputBinding(),
      withInMemoryScrolling({ scrollPositionRestoration: 'enabled', anchorScrolling: 'enabled' }),
    ),
  ],
};
