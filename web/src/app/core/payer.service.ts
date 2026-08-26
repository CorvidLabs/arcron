/**
 * The connected account, read as money.
 *
 * The console called `accountInformation` exactly once and it was for the
 * *app* account, so nothing anywhere read the balance of the person about to
 * pay. A registration they could not afford opened the wallet and came back as
 * whatever algod said, rendered raw. `docs/journeys.md:74` asks for the
 * opposite: they cannot start a registration they cannot afford, and they are
 * told which it is before the wallet opens.
 *
 * Deliberately its own service rather than a field on `ArcronService`. That
 * one is the *chain's* state, refreshed on its own poll and reset when the app
 * id moves; this one follows the wallet, which changes for entirely different
 * reasons. It also carries the node's current minimum fee, because the two
 * numbers are only ever wanted together: what registering costs, against what
 * the payer holds.
 *
 * Both are re-read on a poll and on demand, so an account funded from
 * elsewhere unblocks the form without a reload.
 */

import { computed, effect, inject, Injectable, signal, untracked } from '@angular/core';
import algosdk from 'algosdk';

import { ArcronService, describe } from './arcron.service';
import { WalletService } from './wallet.service';

const POLL_INTERVAL_MS = 6_000;
/** What algod charges per transaction when nothing on the node says otherwise. */
const ASSUMED_MIN_FEE = 1_000n;

export interface PayerBalance {
  readonly address: string;
  readonly amount: bigint;
  /** What this account must keep, given the assets and apps it already holds. */
  readonly minBalance: bigint;
  /** What it can actually part with: everything above its own minimum balance. */
  readonly spendable: bigint;
}

@Injectable({ providedIn: 'root' })
export class PayerService {
  private readonly arcron = inject(ArcronService);
  private readonly wallet = inject(WalletService);

  private timer: ReturnType<typeof setInterval> | null = null;

  readonly balance = signal<PayerBalance | null>(null);
  readonly error = signal<string | null>(null);
  readonly reading = signal(false);

  /**
   * The node's own minimum fee, not a constant.
   *
   * `docs/ac/j2.md` asks for the fee component of the quote to come from the
   * node's suggested parameters, so that a chain charging something other than
   * 1,000 microALGO moves the figure on screen instead of being quietly wrong.
   */
  readonly minFee = signal<bigint>(ASSUMED_MIN_FEE);

  /** Null until a balance has been read, which is not the same as zero. */
  readonly spendable = computed(() => this.balance()?.spendable ?? null);

  constructor() {
    // The address is what this follows. Switching accounts inside one wallet,
    // connecting, and disconnecting all arrive here, and a stale balance from
    // the previous account is worse than none: it is a number about somebody
    // else's money sitting next to a submit button.
    effect(() => {
      const address = this.wallet.activeAddress();
      // Read so a network switch restarts the poll too: the balance is an
      // answer about one chain, and the algod behind it has just changed.
      this.arcron.network();
      untracked(() => this.follow(address));
    });
  }

  /** Re-read now, without a page reload. Wired to the form's own control. */
  async refresh(): Promise<void> {
    const address = this.wallet.activeAddress();
    const algod = this.arcron.algod();
    this.reading.set(true);
    try {
      const params = await algod.getTransactionParams().do();
      this.minFee.set(BigInt(params.minFee));
      if (address === null) {
        this.balance.set(null);
      } else {
        this.balance.set(await readBalance(algod, address));
      }
      this.error.set(null);
    } catch (cause) {
      // The balance is cleared rather than left standing. A figure that cannot
      // be re-read is not evidence of anything, and the form treats "unknown"
      // as "do not commit money", which is the same answer the stale-read
      // guard gives everywhere else in the console.
      this.balance.set(null);
      this.error.set(describe(cause));
    } finally {
      this.reading.set(false);
    }
  }

  private follow(address: string | null): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.balance.set(null);
    this.error.set(null);
    void this.refresh();
    if (address === null) return;
    this.timer = setInterval(() => void this.refresh(), POLL_INTERVAL_MS);
  }
}

async function readBalance(algod: algosdk.Algodv2, address: string): Promise<PayerBalance> {
  const account = await algod.accountInformation(address).do();
  const amount = BigInt(account.amount);
  const minBalance = BigInt(account.minBalance);
  return {
    address,
    amount,
    minBalance,
    // An account cannot spend below its own minimum balance, so a balance that
    // clears the total on paper and not this is still short. The AVM enforces
    // it and the console did not even read it.
    spendable: amount > minBalance ? amount - minBalance : 0n,
  };
}
