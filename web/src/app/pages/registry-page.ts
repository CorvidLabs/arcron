import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { RegistryTable } from '../components/registry-table';
import { UpkeepBoard } from '../components/upkeep-board';

type View = 'registry' | 'board';

/**
 * The front door: the whole registry, read two ways.
 *
 * These stay two tabs rather than two routes. They are the same boxes seen by
 * a creator and by a keeper, and driving the console showed they are the only
 * two things on the page that genuinely compete for the same space. Splitting
 * them would make a keeper and a creator arrive somewhere different, which is
 * exactly the mode-picking the plan rejected.
 */
@Component({
  selector: 'arcron-registry-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RegistryTable, RouterLink, UpkeepBoard],
  template: `
    <div class="head">
      <nav class="views" aria-label="Registry view">
        @for (option of views; track option.key) {
          <button
            type="button"
            class="ghost small"
            [class.current]="view() === option.key"
            [attr.aria-current]="view() === option.key ? 'true' : null"
            (click)="show(option.key)"
          >
            {{ option.label }}
          </button>
        }
      </nav>

      <a class="primary" routerLink="/register">Register an upkeep</a>
    </div>

    @if (view() === 'registry') {
      <arcron-registry-table />
    } @else {
      <arcron-upkeep-board />
    }
  `,
  styles: `
    :host { display: grid; gap: 1.75rem; align-content: start; }
    .head {
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem 1rem;
      align-items: center;
      justify-content: space-between;
    }
    .views { display: flex; gap: 0.4rem; }
    .views .current { border-color: var(--sheen); color: var(--sheen); font-weight: 500; }
  `,
})
export class RegistryPage {
  protected readonly view = signal<View>('registry');
  protected readonly views = [
    { key: 'registry' as const, label: 'Registry' },
    { key: 'board' as const, label: 'Keeper board' },
  ];

  protected show(view: View): void {
    this.view.set(view);
  }
}
