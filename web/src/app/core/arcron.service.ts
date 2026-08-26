/**
 * Live view of a keeper app: current round, app account, upkeep registry.
 *
 * Reads are permissionless. The registry is public box state, so the whole
 * dashboard works with no wallet connected. Everything is exposed as signals
 * and refreshed on a poll, because a keeper network is only interesting as it
 * moves: rounds tick, upkeeps come due, escrows drain.
 */

import { computed, effect, Injectable, signal } from '@angular/core';
import algosdk from 'algosdk';

import { NETWORKS, type NetworkKey } from '@corvidlabs/arcron/networks';

import { type Entry, entryFrom, entryLink, rememberedAppId } from './entry';
import { decodeUpkeep, type Upkeep, upkeepIdFromBoxName } from '@corvidlabs/arcron/upkeep';

const POLL_INTERVAL_MS = 2_500;
/** Round-rate samples kept; at the poll interval this is ~2 minutes of chain. */
const RATE_SAMPLES = 48;
/** Below this the sample window is too short to divide by. */
const MIN_RATE_WINDOW_MS = 8_000;
const NETWORK_STORAGE_KEY = 'arcron.network';
const APP_ID_STORAGE_KEY = (network: NetworkKey) => `arcron.appId.${network}`;

export interface AppAccount {
  readonly address: string;
  readonly amount: bigint;
  readonly minBalance: bigint;
  /** What the app could actually pay out: everything not locked as MBR. */
  readonly spendable: bigint;
}

export type ConnectionStatus = 'connecting' | 'ready' | 'error';

/**
 * Whether this app's creator has given up the power to replace its programs.
 *
 * Exported so the test can exercise the code the console actually runs. The
 * first version of this lived inline and its test declared a private copy, so
 * reverting the coercion below left every test green.
 *
 * A missing `frozen` key means an app deployed before governance existed,
 * which has no update path at all, so absent reads as frozen rather than
 * unknown.
 */
export function isFrozen(
  globalState: readonly { key: Uint8Array; value: { uint?: number | bigint } }[],
): boolean {
  const found = globalState.find((entry) => new TextDecoder().decode(entry.key) === 'frozen');
  if (!found) return true;
  // BigInt first: a strict compare between a number 0 and 0n is true, which
  // would report an unfrozen app as frozen and hide the warning entirely.
  return BigInt(found.value.uint ?? 0) !== 0n;
}

@Injectable({ providedIn: 'root' })
export class ArcronService {
  private timer: ReturnType<typeof setInterval> | null = null;

  /** Resolved once, before the signals below read it, so field order matters. */
  private readonly entry = readEntry();
  readonly network = signal<NetworkKey>(this.entry.network);
  readonly appId = signal<number | null>(this.entry.appId);

  readonly status = signal<ConnectionStatus>('connecting');
  readonly error = signal<string | null>(null);
  readonly round = signal<bigint>(0n);
  readonly genesisId = signal<string | null>(null);
  readonly upkeeps = signal<readonly Upkeep[]>([]);
  readonly appAccount = signal<AppAccount | null>(null);
  readonly nextUpkeepId = signal<bigint | null>(null);
  /**
   * Whether this app's creator can still replace its programs. Null while it
   * is unknown, which is not the same as safe: an app that does not carry the
   * flag at all predates governance and is immutable, so it reads as frozen.
   */
  readonly frozen = signal<boolean | null>(null);
  /**
   * Boxes this app holds that do not decode as upkeeps.
   *
   * Zero on any honest deployment. A non-zero count means the app is holding
   * data shaped like an upkeep box but is not one, which is either a
   * different contract wearing this one's box names or a deliberate attempt
   * to break the reader.
   */
  readonly undecodableBoxes = signal(0);

  /** Which refresh is allowed to write. See `refresh`. */
  private generation = 0;
  readonly lastRefreshed = signal<number | null>(null);
  /** Recent (wall clock, round) pairs, oldest first. */
  private readonly rateSamples = signal<readonly { at: number; round: bigint }[]>([]);

  /**
   * Where the round rate came from: a chain we watched move, or the nominal
   * block time we assume until then.
   */
  readonly paceSource = computed<'measured' | 'nominal'>(() =>
    this.measuredRoundSeconds() === null ? 'nominal' : 'measured',
  );

  /**
   * Seconds per round, for turning round counts into human time.
   *
   * On a dev-mode chain the measurement is meaningless, because a block
   * appears when a transaction does, so watching the clock would report
   * whatever the gap between your own transactions happened to be. There we keep the nominal
   * rate, which is what the same schedule would mean on a real chain.
   */
  readonly secondsPerRound = computed<number>(
    () => this.measuredRoundSeconds() ?? this.config().nominalRoundSeconds,
  );

  private readonly measuredRoundSeconds = computed<number | null>(() => {
    if (this.config().devMode === true) return null;
    const samples = this.rateSamples();
    const first = samples.at(0);
    const last = samples.at(-1);
    if (first === undefined || last === undefined) return null;
    const elapsed = last.at - first.at;
    const advanced = last.round - first.round;
    if (elapsed < MIN_RATE_WINDOW_MS || advanced <= 0n) return null;
    return elapsed / 1_000 / Number(advanced);
  });

  readonly config = computed(() => NETWORKS[this.network()]);
  readonly algod = computed(() => {
    const { algod } = this.config();
    return new algosdk.Algodv2(algod.token, algod.server, algod.port);
  });
  /** True once the node we reached is the chain we asked for. */
  readonly genesisMatches = computed(() => {
    const genesis = this.genesisId();
    return genesis === null ? null : this.config().genesisIds.includes(genesis);
  });
  readonly totalEscrowed = computed(() =>
    this.upkeeps().reduce((total, upkeep) => total + upkeep.balance, 0n),
  );
  /** The app must be able to pay out every µALGO it holds in escrow. */
  readonly solvent = computed(() => {
    const account = this.appAccount();
    return account === null ? null : account.spendable >= this.totalEscrowed();
  });

  constructor() {
    effect(() => {
      const network = this.network();
      localStorage.setItem(NETWORK_STORAGE_KEY, network);
    });
    effect(() => {
      const appId = this.appId();
      const key = APP_ID_STORAGE_KEY(this.network());
      if (appId === null) localStorage.removeItem(key);
      else localStorage.setItem(key, String(appId));
    });
    // Keep the address bar describing what is on screen, so the URL is always
    // the shareable link, with no copy button to find, and a reload comes back
    // to the same registry rather than to whatever was last remembered.
    effect(() => {
      const link = entryLink(location.pathname, this.network(), this.appId());
      history.replaceState(history.state, '', link);
    });
    this.start();
  }

  setNetwork(network: NetworkKey): void {
    if (network === this.network()) return;
    this.network.set(network);
    this.appId.set(readAppId(network));
    this.reset();
    void this.refresh();
  }

  setAppId(appId: number | null): void {
    if (appId === this.appId()) return;
    this.appId.set(appId);
    this.reset();
    void this.refresh();
  }

  start(): void {
    if (this.timer !== null) return;
    void this.refresh();
    this.timer = setInterval(() => void this.refresh(), POLL_INTERVAL_MS);
  }

  stop(): void {
    if (this.timer === null) return;
    clearInterval(this.timer);
    this.timer = null;
  }

  async refresh(): Promise<void> {
    const algod = this.algod();
    const appId = this.appId();
    // Every write below is guarded by this. A victim who suspects the app id
    // they were linked and types the canonical one gets a reset and a new
    // refresh, and the attacker's slower in-flight read would otherwise land
    // afterwards and repaint their registry under the canonical id, where no
    // warning is shown. Whichever refresh started last is the only one
    // allowed to finish.
    const generation = ++this.generation;
    const current = () => generation === this.generation;
    try {
      const params = await algod.getTransactionParams().do();
      const status = await algod.status().do();
      // Guarded like everything else below. `algod` was captured from the
      // config as it was when this refresh started, so a slow read from the
      // previous network or the previous app id used to land here and write
      // both fields anyway. `round` decides which Execute buttons are live
      // and what they claim to pay, and `genesisId` drives the wrong-chain
      // banner, so these two were the worst pair to leave outside the guard.
      if (!current()) return;
      this.genesisId.set(params.genesisID ?? null);
      this.round.set(status.lastRound);
      if (this.config().devMode !== true) this.sampleRate(status.lastRound);

      if (appId === null) {
        this.upkeeps.set([]);
        this.appAccount.set(null);
        this.nextUpkeepId.set(null);
        this.frozen.set(null);
      } else {
        await this.refreshApp(algod, appId, current);
      }
      if (!current()) return;
      this.status.set('ready');
      this.error.set(null);
      this.lastRefreshed.set(Date.now());
    } catch (cause) {
      if (!current()) return;
      this.status.set('error');
      this.error.set(describe(cause));
    }
  }

  private async refreshApp(
    algod: algosdk.Algodv2,
    appId: number,
    current: () => boolean,
  ): Promise<void> {
    const application = await algod.getApplicationByID(appId).do();
    const counter = application.params?.globalState?.find(
      (entry) => new TextDecoder().decode(entry.key) === 'next_upkeep_id',
    );
    if (!current()) return;
    this.nextUpkeepId.set(counter ? BigInt(counter.value.uint ?? 0) : null);

    this.frozen.set(isFrozen(application.params?.globalState ?? []));

    const address = algosdk.getApplicationAddress(appId);
    const account = await algod.accountInformation(address).do();
    if (!current()) return;
    this.appAccount.set({
      address: address.toString(),
      amount: account.amount,
      minBalance: account.minBalance,
      spendable: account.amount - account.minBalance,
    });

    const upkeeps = await this.readUpkeeps(algod, appId, current);
    if (!current()) return;
    this.upkeeps.set(upkeeps);
  }

  private async readUpkeeps(
    algod: algosdk.Algodv2,
    appId: number,
    current: () => boolean,
  ): Promise<Upkeep[]> {
    const { boxes } = await algod.getApplicationBoxes(appId).do();
    let undecodable = 0;
    const upkeeps = await Promise.all(
      boxes.map(async (box) => {
        const id = upkeepIdFromBoxName(box.name);
        if (id === null) return null;
        try {
          const value = await algod.getApplicationBoxByName(appId, box.name).do();
          return decodeUpkeep(id, value.value);
        } catch {
          // Box contents belong to whoever owns the app, and a decoder throw
          // inside a bare Promise.all rejects the whole read. That pinned the
          // connection at 'error' for one malformed box, which cost an
          // attacker about 0.058 ALGO and switched off every warning on the
          // page while the register button stayed live. One bad box now
          // drops one row.
          undecodable += 1;
          return null;
        }
      }),
    );
    if (current()) this.undecodableBoxes.set(undecodable);
    return upkeeps
      .filter((upkeep): upkeep is Upkeep => upkeep !== null)
      .sort((left, right) => (left.id < right.id ? -1 : 1));
  }

  /** Keep a rolling window of (time, round) pairs to derive the round rate. */
  private sampleRate(round: bigint): void {
    this.rateSamples.update((samples) =>
      [...samples, { at: Date.now(), round }].slice(-RATE_SAMPLES),
    );
  }

  private reset(): void {
    this.status.set('connecting');
    this.error.set(null);
    this.upkeeps.set([]);
    this.appAccount.set(null);
    this.nextUpkeepId.set(null);
    this.frozen.set(null);
    this.undecodableBoxes.set(0);
    this.genesisId.set(null);
    this.rateSamples.set([]);
  }
}

/** Where the console opens: the entry link first, then what it remembers. */
function readEntry(): Entry {
  return entryFrom(
    location.search,
    localStorage.getItem(NETWORK_STORAGE_KEY),
    (network) => localStorage.getItem(APP_ID_STORAGE_KEY(network)),
  );
}

/** The app id for a network the *user* switched to, from memory only and never the link. */
function readAppId(network: NetworkKey): number | null {
  return rememberedAppId(network, localStorage.getItem(APP_ID_STORAGE_KEY(network)));
}

export function describe(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  return String(cause);
}
