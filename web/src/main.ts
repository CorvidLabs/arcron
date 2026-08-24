import { Buffer } from 'buffer';

// Wallet SDKs (WalletConnect, Pera, Defly) expect Node's globals in the
// browser; provide them before anything else loads.
const globals = globalThis as typeof globalThis & { Buffer?: typeof Buffer; global?: typeof globalThis };
globals.Buffer ??= Buffer;
globals.global ??= globalThis;

import { bootstrapApplication } from '@angular/platform-browser';
import { appConfig } from './app/app.config';
import { App } from './app/app';

bootstrapApplication(App, appConfig)
  .catch((err) => console.error(err));
