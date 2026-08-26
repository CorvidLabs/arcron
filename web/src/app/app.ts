import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { ActivityLog } from './components/activity-log';
import { NetworkBar } from './components/network-bar';
import { RegisterForm } from './components/register-form';
import { RegistryTable } from './components/registry-table';
import { SignerBar } from './components/signer-bar';
import { StatTiles } from './components/stat-tiles';
import { TrustBanner } from './components/trust-banner';
import { UpkeepBoard } from './components/upkeep-board';
import { ArcronService } from './core/arcron.service';
import { shortAddress } from '@corvidlabs/arcron/format';

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ActivityLog,
    NetworkBar,
    RegisterForm,
    RegistryTable,
    SignerBar,
    StatTiles,
    TrustBanner,
    UpkeepBoard,
  ],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  protected readonly arcron = inject(ArcronService);

  /** Two jobs, one state: watching your own upkeeps, or finding work. */
  protected readonly view = signal<'registry' | 'board'>('registry');
  protected readonly views = [
    { key: 'registry' as const, label: 'Registry' },
    { key: 'board' as const, label: 'Keeper board' },
  ];

  protected show(view: 'registry' | 'board'): void {
    this.view.set(view);
  }

  protected readonly appAddress = computed(() => {
    const account = this.arcron.appAccount();
    return account === null ? null : shortAddress(account.address);
  });

  protected readonly nodeError = computed(() => {
    if (this.arcron.genesisMatches() === false) {
      return `The node answering for ${this.arcron.config().label} reports genesis ${this.arcron.genesisId()}. Check the endpoint before trusting anything on this page.`;
    }
    return this.arcron.error();
  });
}
