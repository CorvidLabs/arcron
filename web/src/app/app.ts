import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { ActivityLog } from './components/activity-log';
import { NetworkBar } from './components/network-bar';
import { RegisterForm } from './components/register-form';
import { RegistryTable } from './components/registry-table';
import { SignerBar } from './components/signer-bar';
import { StatTiles } from './components/stat-tiles';
import { UpkeepBoard } from './components/upkeep-board';
import { ArchonService } from './core/archon.service';
import { shortAddress } from './core/format';

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ActivityLog, NetworkBar, RegisterForm, RegistryTable, SignerBar, StatTiles, UpkeepBoard],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  protected readonly archon = inject(ArchonService);

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
    const account = this.archon.appAccount();
    return account === null ? null : shortAddress(account.address);
  });

  protected readonly nodeError = computed(() => {
    if (this.archon.genesisMatches() === false) {
      return `The node answering for ${this.archon.config().label} reports genesis ${this.archon.genesisId()}. Check the endpoint before trusting anything on this page.`;
    }
    return this.archon.error();
  });
}
