/**
 * Live view of the Rain hub, plus the holder-facing writes.
 *
 * Reads the rain app, not the keeper app. The list is every rain box; a
 * detail page watches one. Tickets persist across drops, so this service
 * will not ask anyone to enter the same rain twice.
 */

import { computed, effect, Injectable, inject, signal, untracked } from '@angular/core';
import algosdk from 'algosdk';

import { ArcronService, describe } from './arcron.service';
import { WalletService } from './wallet.service';
import { algos, toBaseUnits } from '@corvidlabs/arcron/format';
import {
  CADENCES,
  CORVID_TESTNET_MINTER,
  CORVID_TESTNET_NFT,
  CORVID_TESTNET_NFT_NAME,
  ONE,
  RAIN_BOX_MBR,
  SPLIT,
  WAVE,
  ZERO_ADDRESS,
  allocationOf,
  decodeHubState,
  decodeRainRec,
  decodeTicket,
  encodeLabel,
  enterMbr,
  isCorvidRain,
  prizeAssetId,
  prizeLabel,
  qualifies,
  rainBoxName,
  rainFor,
  rainIdFromBoxName,
  rainStanding,
  rainsRemaining,
  roundsUntilRain,
  ticketBoxName,
  ticketRainIdForHolder,
  unitPrefixFor,
  waitingReason,
  type QualifyingAsset,
  type RainDeployment,
  type RainHubState,
  type RainRec,
  type Ticket,
} from '@corvidlabs/arcron/rain';
import * as txns from '@corvidlabs/arcron/rain-txns';
import { decodeUpkeep, type Upkeep, upkeepBoxName } from '@corvidlabs/arcron/upkeep';
import { knownNftImage, resolveNftImage } from './nft-media';

function httpMessage(cause: unknown): string {
  if (cause !== null && typeof cause === 'object' && 'response' in cause) {
    const text = (cause as { response?: { text?: string } }).response?.text;
    if (typeof text === 'string' && text.length > 0) {
      try {
        const body = JSON.parse(text) as { message?: string };
        if (typeof body.message === 'string') return body.message;
      } catch {
        return text;
      }
    }
  }
  return describe(cause);
}

export type RainOp =
  | 'enter' | 'gm' | 'deposit' | 'claim' | 'create' | 'resolve' | 'abandon' | 'optin';

export type YouStatus = 'connect' | 'open' | 'in' | 'yes' | 'no';

export interface HeldAsset {
  readonly id: number;
  readonly unitName: string;
  readonly name: string;
  readonly amount: bigint;
  readonly creator: string;
  readonly url: string;
  readonly reserve: string;
  readonly decimals: number;
}

export interface PrizeAsset {
  readonly id: number;
  readonly name: string;
  readonly unitName: string;
  readonly decimals: number;
}

export interface RainActivity {
  readonly operation: RainOp;
  readonly message: string;
  readonly txId: string;
  readonly round: bigint;
}

export interface CreateRainInput {
  readonly label: string;
  readonly gate: 'open' | 'corvid' | 'custom';
  readonly gateCreator: string;
  readonly prize: 'algo' | 'asa';
  readonly prizeAsset: number;
  readonly drip: number;
  readonly cadence: 'hourly' | 'daily' | 'weekly' | 'monthly';
  readonly mode: 'split' | 'one' | 'wave';
  readonly waveCap: number;
}

const POLL_MS = 5_000;

@Injectable({ providedIn: 'root' })
export class RainService {
  private readonly arcron = inject(ArcronService);
  private readonly wallet = inject(WalletService);
  private watching = 0;
  private watchingId: bigint | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;

  readonly deployment = computed<RainDeployment | null>(() => rainFor(this.arcron.network()));
  readonly hub = signal<RainHubState | null>(null);
  readonly rains = signal<readonly RainRec[]>([]);
  readonly current = signal<RainRec | null>(null);
  readonly upkeep = signal<Upkeep | null>(null);
  readonly holdings = signal<readonly QualifyingAsset[]>([]);
  readonly accountAssets = signal<readonly HeldAsset[]>([]);
  readonly nftImages = signal<Readonly<Record<number, string>>>({});
  readonly enteredIds = signal<ReadonlySet<string>>(new Set());
  readonly prizeAssets = signal<Readonly<Record<number, PrizeAsset>>>({});
  readonly optedInIds = signal<ReadonlySet<number>>(new Set());
  readonly ticket = signal<Ticket | null>(null);
  readonly allocation = signal(0n);
  private readonly assetCache = new Map<number, Omit<HeldAsset, 'amount'>>();
  private readonly resolving = new Set<number>();
  readonly error = signal<string | null>(null);
  readonly writeError = signal<string | null>(null);
  readonly busy = signal<RainOp | null>(null);
  readonly activity = signal<readonly RainActivity[]>([]);
  readonly status = signal<'idle' | 'loading' | 'ready' | 'missing'>('idle');

  readonly available = computed(() => this.deployment() !== null);
  readonly qualifying = computed(() => this.holdings());
  readonly entered = computed(() => this.ticket() !== null);
  readonly canEnter = computed(
    () =>
      this.wallet.connected() &&
      this.qualifying().length > 0 &&
      !this.entered() &&
      this.busy() === null,
  );
  readonly canGm = computed(
    () =>
      this.wallet.connected() &&
      this.entered() &&
      this.current()?.mode === WAVE &&
      this.qualifying().length > 0 &&
      this.busy() === null,
  );
  readonly canClaim = computed(
    () => this.allocation() > 0n && this.qualifying().length > 0 && this.busy() === null,
  );
  readonly rainsLeft = computed(() => {
    const rain = this.current();
    return rain === null ? 0n : rainsRemaining(rain);
  });
  readonly roundsToRain = computed(() => {
    const rain = this.current();
    if (rain === null) return 0n;
    return roundsUntilRain(rain, this.arcron.round());
  });
  readonly hubTickets = computed(() =>
    this.rains().reduce((total, rain) => total + rain.tickets, 0n),
  );
  readonly hubAlgoPot = computed(() =>
    this.rains()
      .filter((rain) => rain.prizeAsset === 0n)
      .reduce((total, rain) => total + rain.pot, 0n),
  );
  readonly hubAsaRains = computed(
    () => this.rains().filter((rain) => rain.prizeAsset !== 0n).length,
  );
  readonly nextHubRain = computed(() => {
    const round = this.arcron.round();
    let soonest: bigint | null = null;
    for (const rain of this.rains()) {
      const until = roundsUntilRain(rain, round);
      if (soonest === null || until < soonest) soonest = until;
    }
    return soonest;
  });
  readonly hubStanding = computed(() => {
    const round = this.arcron.round();
    const rains = this.rains();
    if (rains.length === 0) return null;
    if (rains.some((rain) => rainStanding(rain, round) === 'due')) return 'due' as const;
    if (rains.some((rain) => rainStanding(rain, round) === 'scheduled')) return 'scheduled' as const;
    return 'waiting' as const;
  });

  constructor() {
    effect(() => {
      this.wallet.activeAddress();
      this.arcron.network();
      if (untracked(() => this.watching) > 0) void this.refresh();
    });
  }

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

  /** Which rain the detail page is looking at. Null on the hub list. */
  focus(id: bigint | null): void {
    this.watchingId = id;
    if (id === null) {
      this.current.set(null);
      this.ticket.set(null);
      this.allocation.set(0n);
    } else if (this.watching > 0) {
      void this.refresh();
    }
  }

  ticketCost(mode: bigint = this.current()?.mode ?? SPLIT): string {
    return algos(BigInt(enterMbr(mode)));
  }

  gateLabel(rain: RainRec): string {
    if (!rain.gated) return 'Open';
    return isCorvidRain(rain) ? 'Corvid NFT' : 'NFT';
  }

  /** On-chain name of the collection sample, or of the token this wallet holds. */
  gateName(rain: RainRec): string {
    if (!rain.gated) return 'Open';
    const held = this.heldFor(rain);
    if (held !== null) return held.name || held.unitName || this.gateLabel(rain);
    if (isCorvidRain(rain)) {
      const cached = this.assetCache.get(CORVID_TESTNET_NFT);
      return cached?.name || CORVID_TESTNET_NFT_NAME;
    }
    return this.gateLabel(rain);
  }

  /**
   * An ASA id a person can look up. Corvid rains show the live sample token;
   * a custom gate shows the token this wallet holds, if any.
   */
  gateAssetId(rain: RainRec): string | null {
    if (!rain.gated) return null;
    const held = this.heldFor(rain);
    if (held !== null) return String(held.id);
    if (isCorvidRain(rain)) return String(CORVID_TESTNET_NFT);
    return null;
  }

  waitingHint(rain: RainRec): string | null {
    return waitingReason(rain);
  }

  youStatus(rain: RainRec): YouStatus {
    if (!this.wallet.connected()) return rain.gated ? 'connect' : 'open';
    if (this.enteredIds().has(rain.id.toString())) return 'in';
    if (!rain.gated) return 'open';
    return this.heldFor(rain) !== null ? 'yes' : 'no';
  }

  youLabel(rain: RainRec): string {
    switch (this.youStatus(rain)) {
      case 'open':
        return 'anyone can enter';
      case 'in':
        return "you're in";
      case 'yes':
        return 'you qualify';
      case 'no':
        return "you don't hold this NFT";
      case 'connect':
        return 'connect to check';
    }
  }

  heldFor(rain: RainRec): HeldAsset | null {
    if (!rain.gated) return null;
    const prefix = unitPrefixFor(rain, this.deployment());
    return this.accountAssets().find((asset) => qualifies(rain, asset, prefix)) ?? null;
  }

  thumbnail(rain: RainRec): string | null {
    const images = this.nftImages();
    const held = this.heldFor(rain);
    if (held !== null && images[held.id] !== undefined) return images[held.id];
    if (held !== null) {
      const known = knownNftImage(held.id);
      if (known !== null) return known;
    }
    if (isCorvidRain(rain)) return knownNftImage(CORVID_TESTNET_NFT);
    return null;
  }

  prizeOf(rain: RainRec): PrizeAsset | null {
    if (rain.prizeAsset === 0n) return null;
    return this.prizeAssets()[Number(rain.prizeAsset)] ?? null;
  }

  prizeText(amount: bigint, rain: RainRec): string {
    const info = this.prizeOf(rain);
    return prizeLabel(amount, rain.prizeAsset, info?.unitName ?? '', info?.decimals ?? null);
  }

  prizeName(rain: RainRec): string | null {
    if (rain.prizeAsset === 0n) return null;
    const info = this.prizeOf(rain);
    if (info === null) return 'ASA';
    return info.name || info.unitName || 'ASA';
  }

  prizeId(rain: RainRec): string | null {
    return prizeAssetId(rain);
  }

  isOptedIn(assetId: number): boolean {
    return this.optedInIds().has(assetId);
  }

  async optIn(assetId: number): Promise<void> {
    if (assetId <= 0) return;
    if (this.isOptedIn(assetId)) return;
    await this.send('optin', (algod, _appId, signing) => txns.optInHolderAsset(algod, signing, assetId), `Opted in to ${assetId}.`);
  }

  createCost(): string {
    return algos(BigInt(RAIN_BOX_MBR));
  }

  async enter(): Promise<void> {
    const rain = this.current();
    const nft = this.holdings()[0];
    if (rain === null) return;
    if (nft === undefined && rain.gated) {
      this.writeError.set('Hold a token from this collection first.');
      return;
    }
    await this.send('enter', (algod, appId, signing) =>
      txns.enter(algod, appId, signing, rain.id, nft?.id ?? 0, rain.mode),
      'Entered. You stay in every drop after this.',
    );
  }

  async gm(): Promise<void> {
    const rain = this.current();
    const nft = this.holdings()[0];
    if (rain === null) return;
    await this.send('gm', (algod, appId, signing) =>
      txns.gm(algod, appId, signing, rain.id, nft?.id ?? 0),
      'Checked in for this drop.',
    );
  }

  async depositAlgo(algo: number): Promise<void> {
    const rain = this.current();
    if (rain === null) return;
    const microAlgo = Math.round(algo * 1e6);
    if (!Number.isFinite(microAlgo) || microAlgo <= 0) {
      this.writeError.set('Deposit a positive amount of ALGO.');
      return;
    }
    await this.send('deposit', (algod, appId, signing) =>
      txns.deposit(algod, appId, signing, rain.id, microAlgo),
      `Deposited ${algos(BigInt(microAlgo))} into the pot.`,
    );
  }

  /** `amount` is whole tokens as typed; the asset's decimals scale it to base units here. */
  async depositAsset(amount: number): Promise<void> {
    const rain = this.current();
    if (rain === null) return;
    if (rain.prizeAsset === 0n) {
      this.writeError.set('This rain pays ALGO.');
      return;
    }
    const info = this.prizeOf(rain);
    if (info === null) {
      this.writeError.set('Still reading the prize asset. Try again in a moment.');
      return;
    }
    if (!Number.isFinite(amount) || amount <= 0) {
      this.writeError.set('Deposit a positive amount.');
      return;
    }
    const baseUnits = toBaseUnits(amount, info.decimals);
    if (baseUnits <= 0n) {
      this.writeError.set('Deposit a positive amount.');
      return;
    }
    await this.send('deposit', (algod, appId, signing) =>
      txns.depositAsset(algod, appId, signing, rain.id, Number(rain.prizeAsset), baseUnits),
      `Deposited ${prizeLabel(baseUnits, rain.prizeAsset, info.unitName, info.decimals)} into the pot.`,
    );
  }

  async claim(): Promise<void> {
    const rain = this.current();
    const nft = this.holdings()[0];
    if (rain === null) return;
    if (nft === undefined && rain.gated) {
      this.writeError.set('You must still hold a collection token to collect.');
      return;
    }
    await this.send('claim', (algod, appId, signing) =>
      txns.claim(algod, appId, signing, rain.id, nft?.id ?? 0),
      'Claimed your rain.',
    );
  }

  async resolve(): Promise<void> {
    const rain = this.current();
    if (rain === null) return;
    await this.send('resolve', (algod, appId, signing) =>
      txns.resolve(algod, appId, signing, rain.id),
      'Resolved the drop. The winner can claim.',
    );
  }

  /**
   * Return an unresolved prize to the pot after its seed window has closed.
   *
   * The only exit from the one state a rain cannot leave on its own.
   * `_fire_one` will not fire while a prize is locked, and once the committed
   * round's seed is too old to read, `resolve` refuses. Without this the rain
   * is finished -- the hub is immutable, so nobody can patch around it.
   */
  async abandon(): Promise<void> {
    const rain = this.current();
    if (rain === null) return;
    await this.send('abandon', (algod, appId, signing) =>
      txns.abandon(algod, appId, signing, rain.id),
      'Returned the locked prize to the pot. The rain can fire again.',
    );
  }

  async create(input: CreateRainInput): Promise<bigint | null> {
    const mode = input.mode === 'one' ? ONE : input.mode === 'wave' ? WAVE : SPLIT;
    const waveCap = mode === WAVE ? Math.max(1, Math.floor(input.waveCap)) : 0;
    const cadence = CADENCES.find((item) => item.label === input.cadence) ?? CADENCES[1];
    const prizeAsset = input.prize === 'asa' ? Math.floor(input.prizeAsset) : 0;
    let drip = input.drip;
    if (prizeAsset === 0) drip = Math.round(input.drip * 1e6);
    drip = Math.floor(drip);
    if (drip <= 0) {
      this.writeError.set('Drip must be positive.');
      return null;
    }
    let gateCreator = ZERO_ADDRESS;
    if (input.gate === 'corvid') gateCreator = CORVID_TESTNET_MINTER;
    if (input.gate === 'custom') gateCreator = input.gateCreator.trim();
    if (input.gate === 'custom' && (gateCreator.length === 0 || !algosdk.isValidAddress(gateCreator))) {
      this.writeError.set('A gated rain needs a minting account address.');
      return null;
    }

    const deployment = this.deployment();
    const signing = this.wallet.signing();
    if (deployment === null || signing === null) {
      this.writeError.set('Connect an account on TestNet first.');
      return null;
    }
    this.busy.set('create');
    this.writeError.set(null);
    try {
      const algod = this.arcron.algod();
      if (prizeAsset > 0) {
        try {
          await txns.optInPrizeAsset(algod, deployment.appId, signing, prizeAsset);
        } catch (cause) {
          const message = httpMessage(cause);
          if (!message.includes('Already opted in')) throw cause;
        }
      }
      const result = await txns.createRain(algod, deployment.appId, signing, {
        label: encodeLabel(input.label.trim() || 'Rain'),
        gateCreator,
        prizeAsset,
        drip,
        intervalRounds: cadence.rounds,
        mode,
        waveCap,
      });
      this.activity.update((entries) =>
        [
          {
            operation: 'create' as const,
            message: `Opened rain ${result.returnValue?.toString() ?? ''}.`,
            txId: result.txId,
            round: result.confirmedRound,
          },
          ...entries,
        ].slice(0, 8),
      );
      await this.refresh();
      return result.returnValue ?? null;
    } catch (cause) {
      this.writeError.set(httpMessage(cause));
      return null;
    } finally {
      this.busy.set(null);
    }
  }

  async refresh(): Promise<void> {
    const deployment = this.deployment();
    if (deployment === null) {
      this.status.set('missing');
      this.hub.set(null);
      this.rains.set([]);
      this.current.set(null);
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
      const hub = decodeHubState(deployment.appId, entries);
      this.hub.set(hub);

      const upkeep = await this.readUpkeep(algod, deployment);
      this.upkeep.set(upkeep);

      const { rains, boxes } = await this.readRains(algod, deployment.appId);
      this.rains.set(rains);
      await this.loadPrizeAssets(algod, rains);
      await this.loadCollectionArt(algod);

      const watchingId = this.watchingId;
      const current = watchingId === null ? null : (rains.find((rain) => rain.id === watchingId) ?? null);
      this.current.set(current);

      const address = this.wallet.activeAddress();
      if (address === null) {
        this.accountAssets.set([]);
        this.holdings.set([]);
        this.ticket.set(null);
        this.allocation.set(0n);
        this.enteredIds.set(new Set());
        this.optedInIds.set(new Set());
      } else {
        const { held, optedIn } = await this.readAccountAssets(algod, address);
        this.accountAssets.set(held);
        this.optedInIds.set(optedIn);
        this.resolveImages(held);
        this.enteredIds.set(this.enteredFromBoxes(boxes, address));
        if (current === null) {
          this.holdings.set([]);
          this.ticket.set(null);
          this.allocation.set(0n);
        } else {
          const holdings = this.holdingsFor(current, held, deployment);
          const ticket = await this.readTicket(algod, deployment.appId, current.id, address);
          this.holdings.set(holdings);
          this.ticket.set(ticket);
          this.allocation.set(allocationOf(current, ticket));
        }
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

  private async readRains(
    algod: algosdk.Algodv2,
    appId: number,
  ): Promise<{ rains: RainRec[]; boxes: Uint8Array[] }> {
    const listed = await algod.getApplicationBoxes(appId).do();
    const boxes: Uint8Array[] = [];
    const found: RainRec[] = [];
    for (const box of listed.boxes ?? []) {
      const name = box.name instanceof Uint8Array ? box.name : new Uint8Array();
      boxes.push(name);
      const id = rainIdFromBoxName(name);
      if (id === null) continue;
      try {
        const body = await algod.getApplicationBoxByName(appId, rainBoxName(id)).do();
        const raw = body.value instanceof Uint8Array ? body.value : new Uint8Array();
        found.push(decodeRainRec(id, raw));
      } catch {
        continue;
      }
    }
    found.sort((left, right) => (left.id < right.id ? -1 : left.id > right.id ? 1 : 0));
    return { rains: found, boxes };
  }

  private holdingsFor(
    rain: RainRec,
    assets: readonly HeldAsset[],
    deployment: RainDeployment,
  ): QualifyingAsset[] {
    if (!rain.gated) return [{ id: 0, unitName: '', name: 'open', amount: 1n }];
    const prefix = unitPrefixFor(rain, deployment);
    return assets
      .filter((asset) => qualifies(rain, asset, prefix))
      .map((asset) => ({
        id: asset.id,
        unitName: asset.unitName,
        name: asset.name,
        amount: asset.amount,
      }));
  }

  private enteredFromBoxes(boxes: readonly Uint8Array[], address: string): Set<string> {
    const found = new Set<string>();
    for (const name of boxes) {
      const id = ticketRainIdForHolder(name, address);
      if (id !== null) found.add(id.toString());
    }
    return found;
  }

  private async loadCollectionArt(algod: algosdk.Algodv2): Promise<void> {
    const info = await this.ensureAsset(algod, CORVID_TESTNET_NFT);
    if (info === null) return;
    this.resolveImages([{ ...info, amount: 1n }]);
  }

  private async loadPrizeAssets(algod: algosdk.Algodv2, rains: readonly RainRec[]): Promise<void> {
    const next: Record<number, PrizeAsset> = { ...this.prizeAssets() };
    const ids = new Set<number>();
    for (const rain of rains) {
      const id = Number(rain.prizeAsset);
      if (id > 0) ids.add(id);
    }
    for (const id of ids) {
      const info = await this.ensureAsset(algod, id);
      if (info === null) continue;
      next[id] = {
        id,
        name: info.name,
        unitName: info.unitName || 'ASA',
        decimals: info.decimals,
      };
    }
    this.prizeAssets.set(next);
  }

  private async ensureAsset(
    algod: algosdk.Algodv2,
    id: number,
  ): Promise<Omit<HeldAsset, 'amount'> | null> {
    const cached = this.assetCache.get(id);
    if (cached !== undefined) return cached;
    try {
      const info = await algod.getAssetByID(id).do();
      const params = info.params;
      const record: Omit<HeldAsset, 'amount'> = {
        id,
        unitName: String(params?.unitName ?? ''),
        name: String(params?.name ?? ''),
        creator: String(params?.creator ?? ''),
        url: String(params?.url ?? ''),
        reserve: String(params?.reserve ?? ''),
        decimals: Number(params?.decimals ?? 0),
      };
      this.assetCache.set(id, record);
      return record;
    } catch {
      return null;
    }
  }

  private async readAccountAssets(
    algod: algosdk.Algodv2,
    address: string,
  ): Promise<{ held: HeldAsset[]; optedIn: Set<number> }> {
    const account = await algod.accountInformation(address).do();
    const rows = account.assets ?? [];
    const optedIn = new Set<number>();
    const found: HeldAsset[] = [];
    for (const row of rows.slice(0, 80)) {
      const id = Number(row.assetId);
      optedIn.add(id);
      const amount = BigInt(row.amount ?? 0);
      if (amount <= 0n) continue;
      const cached = await this.ensureAsset(algod, id);
      if (cached === null) continue;
      found.push({ ...cached, amount });
    }
    return { held: found, optedIn };
  }

  private resolveImages(assets: readonly HeldAsset[]): void {
    for (const asset of assets) {
      if (this.nftImages()[asset.id] !== undefined) continue;
      if (this.resolving.has(asset.id)) continue;
      this.resolving.add(asset.id);
      void resolveNftImage(asset).then((url) => {
        this.resolving.delete(asset.id);
        if (url === null) return;
        this.nftImages.update((current) => ({ ...current, [asset.id]: url }));
      });
    }
  }

  private async readTicket(
    algod: algosdk.Algodv2,
    appId: number,
    rainId: bigint,
    address: string,
  ): Promise<Ticket | null> {
    const listed = await algod.getApplicationBoxes(appId).do();
    const wanted = ticketBoxName(rainId, address);
    const present = (listed.boxes ?? []).some((box) => {
      const name = box.name instanceof Uint8Array ? box.name : new Uint8Array();
      if (name.length !== wanted.length) return false;
      for (let index = 0; index < name.length; index += 1) {
        if (name[index] !== wanted[index]) return false;
      }
      return true;
    });
    if (!present) return null;
    const raw = await txns.readTicket(algod, appId, rainId, address);
    if (raw === null) return null;
    return decodeTicket(raw);
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
      this.writeError.set('Rain is not deployed on this network.');
      return;
    }
    if (signing === null) {
      this.writeError.set('Connect an account first.');
      return;
    }
    if (this.arcron.genesisMatches() === false || this.arcron.status() !== 'ready') {
      this.writeError.set('The last read of the chain failed. Nothing will be sent until it recovers.');
      return;
    }
    this.busy.set(operation);
    this.writeError.set(null);
    try {
      const result = await call(this.arcron.algod(), deployment.appId, signing);
      this.activity.update((entries) =>
        [{ operation, message, txId: result.txId, round: result.confirmedRound }, ...entries].slice(0, 8),
      );
      await this.refresh();
    } catch (cause) {
      this.writeError.set(httpMessage(cause));
    } finally {
      this.busy.set(null);
    }
  }
}
