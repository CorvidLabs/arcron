import { ChangeDetectionStrategy, Component, inject } from '@angular/core';

import { KeeperService } from '../core/keeper.service';

@Component({
  selector: 'archon-activity-log',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="panel">
      <header>
        <h2>Activity</h2>
        <p class="subtitle">What this browser has sent, and what the contract returned.</p>
      </header>

      @if (keeper.error(); as error) {
        <div class="alert" role="alert">
          <p>{{ error }}</p>
          <button type="button" class="ghost small" (click)="dismiss()">Dismiss</button>
        </div>
      }

      @if (keeper.activity().length === 0) {
        <p class="empty">Nothing sent yet.</p>
      } @else {
        <ol>
          @for (entry of keeper.activity(); track entry.txId) {
            <li>
              <span class="op">{{ entry.operation }}</span>
              <span class="message">{{ entry.message }}</span>
              <span class="meta">round {{ entry.round }} · {{ entry.txId }}</span>
            </li>
          }
        </ol>
      }
    </section>
  `,
  styles: `
    .panel { display: grid; gap: 1rem; align-content: start; }
    header h2 { margin: 0; font-size: 1.1rem; }
    .subtitle { margin: 0.25rem 0 0; color: var(--text-faint); font-size: 0.85rem; }
    .empty { margin: 0; color: var(--text-faint); font-size: 0.85rem; }
    ol { list-style: none; margin: 0; padding: 0; display: grid; gap: 0.6rem; }
    li {
      display: grid;
      gap: 0.2rem;
      padding: 0.6rem 0.75rem;
      border: 1px solid var(--hairline);
      border-radius: 2px;
      background: var(--surface);
    }
    .op {
      font-family: var(--font-mono);
      font-size: 0.68rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--sheen-strong);
    }
    .message { font-size: 0.88rem; }
    .meta { font-family: var(--font-mono); font-size: 0.72rem; color: var(--text-faint); overflow-wrap: anywhere; }
    .alert {
      display: flex;
      gap: 0.75rem;
      align-items: start;
      justify-content: space-between;
      padding: 0.7rem 0.85rem;
      border: 1px solid var(--danger);
      border-radius: 2px;
      color: var(--danger);
    }
    .alert p { margin: 0; font-size: 0.85rem; overflow-wrap: anywhere; }
  `,
})
export class ActivityLog {
  protected readonly keeper = inject(KeeperService);

  protected dismiss(): void {
    this.keeper.dismissError();
  }
}
