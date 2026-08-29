/**
 * Live view of the Rain draw, plus the holder-facing writes.
 *
 * Reads the rain app, not the keeper app. Tickets persist across draws;
 * this service will not ask anyone to enter twice.
 */

import { computed, effect, Injectable, inject, signal, untracked } from '@angular/core';
import algosdk from 'algosdk';

import { ArcronService, describe } from './arcron.service';
import { WalletService } from './wallet.service';
import { algos } from '@corvidlabs/arcron/format';
import {
  TICKET_MBR,
  abandonOpen,
  decodeRainState,
  qualifies,
  rainFor,
  resolveOpen,
  type QualifyingAsset,
  type RainDeployment,
  type RainState,
} from '@corvidlabs/arcron/rain';
import * as txns from '@corvidlabs/arcron/rain-txns';
import { decodeUpkeep, type Upkeep, upkeepBoxName } from '@corvidlabs/arcron/upkeep';

export type RainOp = 'enter' | 'deposit' | 'claim' | 'resolve' | 'abandon';

export interface RainActivity {
  readonly operation: RainOp;
  readonly message: string;
  readonly txId: string;
  readonly round: bigint;
}

const POLL_MS = 5_000;

@Injectable({ providedIn: 'root' })
export class RainService {
  private readonly arcron = inject(ArcronService);
  private readonly wallet = inject(WalletService);
  private watching = 0;
  private timer: ReturnType<typeof setInterval> | null = null;

  readonly deployment = computed<RainDeployment | null>(() => rainFor(this.arcron.network()));
  readonly state = signal<RainState | null>(null);
  readonly upkeep = signal<Upkeep | null>(null);
  readonly holdings = signal<readonly QualifyingAsset[]>([]);
  readonly ticketCount = signal(0);
  readonly allocation = signal(0n);
  readonly error = signal<string | null>(null);
  readonly busy = signal<RainOp | null>(null);
  readonly activity = signal<readonly RainActivity[]>([]);
  readonly status = signal<'idle' | 'loading' | 'ready' | 'missing'>('idle');

  readonly available = computed(() => this.deployment() !== null);
  readonly qualifying = computed(() => this.holdings());
  readonly entered = computed(() => this.ticketCount() > 0);
  readonly canEnter = computed(
    () => this.wallet.connected() && this.qualifying().length > 0 && this.busy() === null,
  );
  readonly canClaim = computed(
    () => this.allocation() > 0n && this.qualifying().length > 0 && this.busy() === null,
  );
  readonly canResolve = computed(() => {
    const state = this.state();
    const round = this.arcron.round();
    return state !== null && resolveOpen(state, round) && this.wallet.connected();
  });
  readonly canAbandon = computed(() => {
    const state = this.state();
    const round = this.arcron.round();
    return state !== null && abandonOpen(state, round) && this.wallet.connected();
  });

  constructor() {
    effect(() => {
      this.wallet.activeAddress();
      this.arcron.network();
      if (untracked(() => this.watching) > 0) void this.refresh();
    });
  }

  /** RainPage holds a watch while it is on screen. */
  watch(): () => void {
    this.watching += 1;
    if (this.watching === 1) {
      void this.refresh();
      this.timer = setInterval(() => void this.refresh(), POLL_MS);
    }
    return () => {
      this.watching = Math.max(0, this.watching - 1);
      if (this.watching === 0 && this.timer !== null) {
        clearInterval(this.timer);
        this.timer = null;
      }
    };
  }

  async enter(): Promise<void> {
    const nft = this.holdings()[0];
    if (nft === undefined) {
      this.error.set('Hold a Corvid NFT from this collection first.');
      return;
    }
    await this.send('enter', (algod, appId, signing) =>
      txns.enter(algod, appId, signing, nft.id),
      'Entered. You stay in every draw after this; you do not enter again.',
    );
  }

  async depositAlgo(algo: number): Promise<void> {
    const microAlgo = Math.round(algo * 1e6);
    if (!Number.isFinite(microAlgo) || microAlgo <= 0) {
      this.error.set('Deposit a positive amount of ALGO.');
      return;
    }
    await this.send('deposit', (algod, appId, signing) =>
      txns.deposit(algod, appId, signing, microAlgo),
      `Deposited ${algos(BigInt(microAlgo))} into the pot.`,
    );
  }

  async claim(): Promise<void> {
    const nft = this.holdings()[0];
    if (nft === undefined) {
      this.error.set('You must still hold a Corvid NFT to collect.');
      return;
    }
    await this.send('claim', (algod, appId, signing) =>
      txns.claim(algod, appId, signing, nft.id),
      'Claimed your prize.',
    );
  }

  async resolve(): Promise<void> {
    const beacon = this.state()?.beaconApp;
    if (beacon === undefined || beacon === 0n) {
      this.error.set('This rain has no beacon configured.');
      return;
    }
    await this.send('resolve', (algod, appId, signing) =>
      txns.resolve(algod, appId, signing, Number(beacon)),
      'Asked the beacon who won.',
    );
  }

  async abandon(): Promise<void> {
    await this.send('abandon', (algod, appId, signing) =>
      txns.abandon(algod, appId, signing),
      'Put the prize back in the pot. The next draw will commit a fresh round.',
    );
  }

  ticketCost(): string {
    return algos(BigInt(TICKET_MBR));
  }

  async refresh(): Promise<void> {
    const deployment = this.deployment();
    if (deployment === null) {
      this.status.set('missing');
      this.state.set(null);
      return;
    }
    if (this.arcron.genesisMatches() === false) return;
    const algod = this.arcron.algod();
    try {
      if (this.status() === 'idle') this.status.set('loading');
      const application = await algod.getApplicationByID(deployment.appId).do();
      const entries = (application.params?.globalState ?? []).map((entry) => ({
        key: entry.key instanceof Uint8Array ? entry.key : new Uint8Array(),
        value: entry.value,
      }));
      const state = decodeRainState(deployment.appId, entries);
      this.state.set(state);

      const upkeep = await this.readUpkeep(algod, deployment);
      this.upkeep.set(upkeep);

      const address = this.wallet.activeAddress();
      if (address === null) {
        this.holdings.set([]);
        this.ticketCount.set(0);
        this.allocation.set(0n);
      } else {
        const [holdings, tickets, allocation] = await Promise.all([
          this.readHoldings(algod, address, state),
          this.countTickets(algod, deployment.appId, address, state.tickets),
          txns.readAllocation(algod, deployment.appId, address),
        ]);
        this.holdings.set(holdings);
        this.ticketCount.set(tickets);
        this.allocation.set(allocation);
      }
      this.status.set('ready');
      this.error.set(null);
    } catch (cause) {
      this.status.set('missing');
      this.error.set(describe(cause));
    }
  }

  private async readUpkeep(algod: algosdk.Algodv2, deployment: RainDeployment): Promise<Upkeep | null> {
    try {
      const box = await algod
        .getApplicationBoxByName(deployment.keeperAppId, upkeepBoxName(BigInt(deployment.upkeepId)))
        .do();
      const raw = box.value instanceof Uint8Array ? box.value : new Uint8Array();
      return decodeUpkeep(BigInt(deployment.upkeepId), raw);
    } catch {
      return null;
    }
  }

  private async readHoldings(
    algod: algosdk.Algodv2,
    address: string,
    state: RainState,
  ): Promise<QualifyingAsset[]> {
    const account = await algod.accountInformation(address).do();
    const held = (account.assets ?? []).filter((row) => BigInt(row.amount ?? 0) > 0n);
    const found: QualifyingAsset[] = [];
    for (const row of held.slice(0, 40)) {
      const id = Number(row.assetId);
      try {
        const info = await algod.getAssetByID(id).do();
        const params = info.params;
        const asset = {
          id,
          unitName: String(params?.unitName ?? ''),
          name: String(params?.name ?? ''),
          amount: BigInt(row.amount ?? 0),
          creator: String(params?.creator ?? ''),
        };
        if (qualifies(state, asset)) {
          found.push({
            id: asset.id,
            unitName: asset.unitName,
            name: asset.name,
            amount: asset.amount,
          });
        }
      } catch {
        continue;
      }
    }
    return found;
  }

  private async countTickets(
    algod: algosdk.Algodv2,
    appId: number,
    address: string,
    tickets: bigint,
  ): Promise<number> {
    const limit = Number(tickets > 64n ? 64n : tickets);
    let count = 0;
    const checks = Array.from({ length: limit }, (_, index) =>
      txns.readTicketHolder(algod, appId, BigInt(index)),
    );
    const holders = await Promise.all(checks);
    for (const holder of holders) {
      if (holder === address) count += 1;
    }
    return count;
  }

  private async send(
    operation: RainOp,
    call: (
      algod: ReturnType<ArcronService['algod']>,
      appId: number,
      signing: txns.Signing,
    ) => Promise<txns.CallResult>,
    message: string,
  ): Promise<void> {
    const deployment = this.deployment();
    const signing = this.wallet.signing();
    if (deployment === null) {
      this.error.set('Rain is not deployed on this network.');
      return;
    }
    if (signing === null) {
      this.error.set('Connect an account first.');
      return;
    }
    if (this.arcron.genesisMatches() === false || this.arcron.status() !== 'ready') {
      this.error.set('The last read of the chain failed. Nothing will be sent until it recovers.');
      return;
    }
    this.busy.set(operation);
    this.error.set(null);
    try {
      const result = await call(this.arcron.algod(), deployment.appId, signing);
      this.activity.update((entries) =>
        [{ operation, message, txId: result.txId, round: result.confirmedRound }, ...entries].slice(0, 8),
      );
      await this.refresh();
    } catch (cause) {
      this.error.set(describe(cause));
    } finally {
      this.busy.set(null);
    }
  }
}
