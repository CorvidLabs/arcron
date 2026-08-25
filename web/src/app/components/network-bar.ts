import { afterNextRender, ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';

import { ArcronService } from '../core/arcron.service';
import { duration } from '@corvidlabs/arcron/format';
import type { NetworkKey } from '@corvidlabs/arcron/networks';

@Component({
  selector: 'arcron-network-bar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="bar">
      <div class="brand">
        <svg class="mark" viewBox="0 0 64 64" role="img" aria-label="CorvidLabs">
          <circle cx="24" cy="32" r="18" fill="currentColor" />
          <path d="M33 21.5 L58.5 29.5 L33 39.5 Z" fill="currentColor" />
          <circle cx="27.5" cy="26" r="3" fill="var(--paper)" />
        </svg>
        <h1 class="wordmark">Arcron</h1>
        <span class="tagline">keeper network console</span>
      </div>

      <div class="controls">
        <fieldset class="networks">
          <legend class="sr-only">Network</legend>
          @for (option of networks; track option.key) {
            <label class="network" [class.active]="arcron.network() === option.key">
              <input
                type="radio"
                name="network"
                class="sr-only"
                [value]="option.key"
                [checked]="arcron.network() === option.key"
                (change)="selectNetwork(option.key)"
              />
              {{ option.label }}
            </label>
          }
        </fieldset>

        <label class="app-id">
          <span class="eyebrow">App</span>
          <input
            type="number"
            inputmode="numeric"
            [value]="arcron.appId() ?? ''"
            placeholder="app id"
            aria-label="Keeper app id"
            (change)="setAppId($event)"
          />
        </label>

        <p class="status" [class]="statusClass()" role="status">
          <span class="dot" aria-hidden="true"></span>
          <span class="mono">{{ statusLabel() }}</span>
        </p>

        <button
          type="button"
          class="corvid-theme-toggle"
          data-corvid-theme-toggle
          aria-pressed="false"
          aria-label="Switch to dark theme"
          title="Switch theme"
        >
          <svg
            class="sun"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="4.2" />
            <path
              d="M12 2.6v2.4M12 19v2.4M4.2 4.2l1.7 1.7M18.1 18.1l1.7 1.7M2.6 12h2.4M19 12h2.4M4.2 19.8l1.7-1.7M18.1 5.9l1.7-1.7"
            />
          </svg>
          <svg
            class="moon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M21 12.8A8.6 8.6 0 1 1 11.2 3a6.7 6.7 0 0 0 9.8 9.8z" />
          </svg>
        </button>
      </div>
    </div>
  `,
  styles: `
    .bar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.85rem 1.5rem;
      align-items: center;
      justify-content: space-between;
    }
    .brand { display: flex; align-items: center; gap: 0.6rem; }
    .mark { width: 26px; height: 26px; color: var(--ink); }
    .wordmark { margin: 0; font-size: 1.3rem; font-weight: 900; letter-spacing: -0.02em; }
    .tagline {
      color: var(--text-faint);
      font-size: 0.78rem;
      font-family: var(--font-mono);
      padding-left: 0.6rem;
      border-left: 1px solid var(--hairline);
    }
    .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 0.7rem; }
    .networks {
      display: flex;
      margin: 0;
      padding: 2px;
      border: 1px solid var(--hairline);
      border-radius: 2px;
      gap: 2px;
    }
    .network {
      padding: 0.25rem 0.8rem;
      font-size: 0.82rem;
      font-family: var(--font-mono);
      color: var(--text-faint);
      cursor: pointer;
    }
    .network.active { background: var(--ink); color: var(--paper); font-weight: 500; }
    .network:focus-within { outline: 2px solid var(--sheen); outline-offset: 2px; }
    .app-id { display: flex; align-items: center; gap: 0.45rem; }
    .app-id input { width: 7.5rem; }
    .status { display: flex; align-items: center; gap: 0.45rem; margin: 0; font-size: 0.78rem; }
    .status .dot { width: 0.45rem; height: 0.45rem; border-radius: 50%; background: currentColor; }
    .status.ready { color: var(--success); }
    .status.warn { color: var(--warning); }
    .status.bad { color: var(--danger); }
    .status .mono { color: var(--text-faint); }
    .status.bad .mono { color: var(--danger); }
  `,
})
export class NetworkBar {
  protected readonly arcron = inject(ArcronService);

  constructor() {
    // brand/theme.js wires every [data-corvid-theme-toggle] once, when it
    // loads. Angular renders this header after that would have happened, so
    // the script is loaded here instead — once the button it looks for exists.
    afterNextRender(() => {
      const script = document.createElement('script');
      script.src = 'brand/theme.js';
      document.head.appendChild(script);
    });
  }

  protected readonly networks = [
    { key: 'localnet' as const, label: 'LocalNet' },
    { key: 'testnet' as const, label: 'TestNet' },
  ];

  protected readonly statusClass = computed(() => {
    if (this.arcron.status() === 'error') return 'bad';
    if (this.arcron.genesisMatches() === false) return 'bad';
    if (this.arcron.status() === 'connecting') return 'warn';
    return 'ready';
  });

  protected readonly statusLabel = computed(() => {
    const genesis = this.arcron.genesisId();
    if (this.arcron.status() === 'error') return 'node unreachable';
    if (this.arcron.genesisMatches() === false) return `wrong chain: ${genesis}`;
    if (this.arcron.status() === 'connecting') return 'connecting…';
    const seconds = this.arcron.secondsPerRound();
    const basis = this.arcron.paceSource() === 'measured' ? '' : ' nominal';
    return `${genesis} · round ${this.arcron.round()} · ${seconds.toFixed(1)} s/round${basis}`;
  });

  protected selectNetwork(network: NetworkKey): void {
    this.arcron.setNetwork(network);
  }

  protected setAppId(event: Event): void {
    const value = (event.target as HTMLInputElement).value.trim();
    this.arcron.setAppId(value === '' ? null : Number(value));
  }
}
