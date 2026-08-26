import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { RegisterForm } from '../components/register-form';

/**
 * Registering an upkeep, and then going to see it.
 *
 * The form used to be pinned below both tabs and to end on itself: the only
 * evidence anything had happened was a line in the activity log naming an id
 * you then had to find. It now ends at `/u/:id`, which is the only version
 * where "see your upkeep and watch it execute without hunting for it" is
 * true, and it is the reason the router exists at all.
 */
@Component({
  selector: 'arcron-register-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RegisterForm, RouterLink],
  template: `
    <nav class="back" aria-label="Breadcrumb">
      <a routerLink="/">Back to the registry</a>
    </nav>

    <arcron-register-form (registered)="show($event)" />
  `,
  styles: `
    :host { display: grid; gap: 1.25rem; align-content: start; }
    .back { font-size: 0.85rem; }
  `,
})
export class RegisterPage {
  private readonly router = inject(Router);

  protected show(upkeepId: bigint): void {
    void this.router.navigate(['/u', String(upkeepId)]);
  }
}
