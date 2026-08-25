/**
 * The write half of the console: the four keeper calls, as UI state.
 *
 * Each call reports the round it landed in and what the method returned,
 * because on a keeper network the return values *are* the feedback: the next
 * due round, the new balance, the refund.
 */

import { computed, Injectable, inject, signal } from '@angular/core';

import { ArcronService, describe } from './arcron.service';
import { algos } from '@corvidlabs/arcron/format';
import { WalletService } from './wallet.service';
import * as txns from '@corvidlabs/arcron/keeper-txns';
import type { Upkeep } from '@corvidlabs/arcron/upkeep';

export type Operation = 'register' | 'top_up' | 'cancel' | 'execute' | 'opt_in_asset' | 'top_up_asset';

export interface Activity {
  readonly operation: Operation;
  readonly upkeepId: bigint | null;
  readonly message: string;
  readonly txId: string;
  readonly round: bigint;
}

@Injectable({ providedIn: 'root' })
export class KeeperService {
  private readonly arcron = inject(ArcronService);
  private readonly wallet = inject(WalletService);

  readonly busy = signal<Operation | null>(null);
  readonly error = signal<string | null>(null);
  readonly activity = signal<readonly Activity[]>([]);
  readonly canSign = computed(() => this.wallet.connected());

  async register(params: txns.RegisterParams): Promise<void> {
    await this.send('register', null, async (algod, appId, signing) => {
      const result = await txns.register(algod, appId, signing, params);
      return { result, message: `Registered upkeep ${result.returnValue}` };
    });
  }

  async topUp(upkeep: Upkeep, amount: number): Promise<void> {
    await this.send('top_up', upkeep.id, async (algod, appId, signing) => {
      const result = await txns.topUp(algod, appId, signing, upkeep.id, amount);
      return { result, message: `Escrow now ${algos(result.returnValue ?? 0n)}` };
    });
  }

  /** Let the app hold an upkeep's bonus asset. Permanent, and costs 0.1 ALGO. */
  async optInAsset(upkeep: Upkeep): Promise<void> {
    await this.send('opt_in_asset', upkeep.id, async (algod, appId, signing) => {
      const result = await txns.optInAsset(
        algod, appId, signing, upkeep.id, Number(upkeep.feeAsset),
      );
      return { result, message: `App can now hold asset ${upkeep.feeAsset}` };
    });
  }

  /** Add to an upkeep's bonus escrow, in the asset's base units. */
  async topUpAsset(upkeep: Upkeep, amount: number): Promise<void> {
    await this.send('top_up_asset', upkeep.id, async (algod, appId, signing) => {
      const result = await txns.topUpAsset(
        algod, appId, signing, upkeep.id, Number(upkeep.feeAsset), amount,
      );
      return { result, message: `Bonus escrow now ${result.returnValue ?? 0n} base units` };
    });
  }

  async cancel(upkeep: Upkeep): Promise<void> {
    await this.send('cancel', upkeep.id, async (algod, appId, signing) => {
      const result = await txns.cancel(algod, appId, signing, upkeep.id, upkeep.feeAsset);
      return {
        result,
        message: `Refunded ${algos(result.returnValue ?? 0n)} (escrow plus box MBR)`,
      };
    });
  }

  async execute(upkeep: Upkeep): Promise<void> {
    await this.send('execute', upkeep.id, async (algod, appId, signing) => {
      const result = await txns.execute(algod, appId, signing, upkeep);
      return {
        result,
        message: `Executed for ${algos(upkeep.feePerExecution)}; next due at round ${result.returnValue}`,
      };
    });
  }

  dismissError(): void {
    this.error.set(null);
  }

  private async send(
    operation: Operation,
    upkeepId: bigint | null,
    call: (
      algod: ReturnType<ArcronService['algod']>,
      appId: number,
      signing: txns.Signing,
    ) => Promise<{ result: txns.CallResult; message: string }>,
  ): Promise<void> {
    const appId = this.arcron.appId();
    const signing = this.wallet.signing();
    if (appId === null) {
      this.error.set('Set a keeper app id first');
      return;
    }
    if (signing === null) {
      this.error.set('Connect an account before sending transactions');
      return;
    }
    this.busy.set(operation);
    this.error.set(null);
    try {
      const { result, message } = await call(this.arcron.algod(), appId, signing);
      this.activity.update((entries) =>
        [{ operation, upkeepId, message, txId: result.txId, round: result.confirmedRound }, ...entries].slice(0, 8),
      );
      await this.arcron.refresh();
    } catch (cause) {
      this.error.set(describe(cause));
    } finally {
      this.busy.set(null);
    }
  }
}
