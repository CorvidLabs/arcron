import { bootstrapApplication } from '@angular/platform-browser';
import { provideZonelessChangeDetection } from '@angular/core';

import { GovernPage } from './app/govern-page';

void bootstrapApplication(GovernPage, {
    providers: [provideZonelessChangeDetection()],
});
