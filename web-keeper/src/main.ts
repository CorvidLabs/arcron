import { bootstrapApplication } from '@angular/platform-browser';
import { provideZonelessChangeDetection } from '@angular/core';

import { Dashboard } from './app/dashboard';

void bootstrapApplication(Dashboard, {
    providers: [provideZonelessChangeDetection()],
});
