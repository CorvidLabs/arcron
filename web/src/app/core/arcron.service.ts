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

import { devModeFrom, type DevModeState } from './dev-mode';
import { appIdStorageKey, type Entry, entryFrom, rememberedAppId, storeAppId } from './entry';
import { canonicalAppId, isQuarantined, standingOf } from './quarantine';
import { decodeUpkeep, type Upkeep, upkeepIdFromBoxName } from '@corvidlabs/arcron/upkeep';

const POLL_INTERVAL_MS = 2_500;
/**
 * How many polls pass between full reads of the registry.
 *
 * A poll used to read every box every time: status, the app, its account, the
 * box list, and then one request per box. At twelve upkeeps that is sixteen
 * requests every 2.5 seconds, or 6.4 a second from a single open tab, growing
 * by 0.4 a second for every upkeep anyone ever registers. Public nodes answer
 * that with 403, and the console then reports itself unable to read the chain.
 *
 * The round has to be live, because it decides which Execute buttons are lit
 * and what they claim to pay. Escrow balances do not need 2.5 second
 * resolution: they change when an upkeep runs, and the fastest cadence anyone
 * can register is ten rounds, about 28 seconds.
 *
 * So the cheap half runs every poll and the expensive half every eighth,
 * which is 6.4 requests a second down to about 0.9. A write refreshes
 * immediately regardless, so anything you do yourself still appears at once.
 */
const FULL_READ_EVERY = 8;
/**
 * How many box names one listing request asks for.
 *
 * The listing used to be a single unbounded request, which is whatever the
 * node's default page happens to be, and a registry past that size would have
 * been read as complete while the tail of it was silently missing. algosdk
 * 3.7.0 exposes `limit` and `next`, and algod answers with `next-token` while
 * there is more, so the listing follows the token until there is not.
 */
export const BOX_PAGE_SIZE = 100;
/**
 * How many box reads are in flight at once.
 *
 * A full read used to fire one request per box in a single burst, which is
 * exactly the shape a public node rate-limits: at thirty-six boxes that is
 * thirty-six simultaneous requests, and the node's answer to a burst is 403
 * for every one of them, which then read as thirty-six unreadable boxes. Eight
 * at a time keeps the read short without looking like an attack.
 */
export const BOX_READ_CONCURRENCY = 8;
/** Round-rate samples kept; at the poll interval this is ~2 minutes of chain. */
const RATE_SAMPLES = 48;
/** Below this the sample window is too short to divide by. */
const MIN_RATE_WINDOW_MS = 8_000;
const NETWORK_STORAGE_KEY = 'arcron.network';

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
/**
 * Whether it is safe to put money on screen: the read succeeded, the node is
 * the chain it claims to be, there is an app to talk to, and that app is not
 * one the console has quarantined.
 *
 * Exported so its test binds to the predicate the console actually runs. A
 * test that redeclares this passes with the guard deleted, which is how the
 * unguarded state got shipped the first time.
 */
export function canCommitMoney(state: {
  status: string;
  genesisMatches: boolean | null;
  appId: number | null;
  /**
   * A link named an app that is not the published deployment and the visitor
   * has not accepted it. See `quarantine.ts`: this is the single gate every
   * money button and `KeeperService.send` already key on, which is why the
   * quarantine is enforced here rather than button by button.
   */
  quarantined: boolean;
}): boolean {
  return (
    state.status === 'ready' &&
    state.genesisMatches !== false &&
    state.appId !== null &&
    !state.quarantined
  );
}

export function isFrozen(
  globalState: readonly { key: Uint8Array; value: { uint?: number | bigint } }[],
): boolean {
  const found = globalState.find((entry) => new TextDecoder().decode(entry.key) === 'frozen');
  if (!found) return true;
  // BigInt first: a strict compare between a number 0 and 0n is true, which
  // would report an unfrozen app as frozen and hide the warning entirely.
  return BigInt(found.value.uint ?? 0) !== 0n;
}

/** One page of a box listing, as algod returns it. */
export interface BoxPage {
  readonly boxes: readonly { readonly name: Uint8Array }[];
  /** Present while there is another page; algod's `next-token`. */
  readonly nextToken?: string;
}

/**
 * The three reads a registry snapshot is made of, narrowed to what
 * `readRegistry` uses so a test can stand a fake node in for algod and drive
 * the code the console actually runs through a failed box, a torn read and a
 * recovery. `algodRegistryReader` is the real one.
 */
export interface RegistryReader {
  /** The app account's balance and its locked minimum. */
  account(): Promise<{ amount: bigint; minBalance: bigint }>;
  /** One page of box names, continuing from `next` when it is given. */
  boxPage(next: string | undefined): Promise<BoxPage>;
  /** One box's value. Rejects when the node will not hand it over. */
  box(name: Uint8Array): Promise<Uint8Array>;
}

/**
 * Everything one full read of the registry learned, set on the service in one
 * go so the account and the boxes on screen always come from the same read.
 */
export interface RegistrySnapshot {
  readonly account: AppAccount;
  readonly upkeeps: readonly Upkeep[];
  /** Box names the listing returned, readable or not. */
  readonly listedBoxes: number;
  readonly undecodableBoxes: number;
  readonly unreadableBoxes: number;
  /**
   * Whether the app's balance stood still from before the listing to after
   * the last box read. Every escrow mutation moves it: register and top-up
   * pay in, execute and cancel pay out. A balance that moved means some box
   * was read before or after the account it is being compared against, and
   * the two cannot be summed against each other.
   */
  readonly consistent: boolean;
}

/**
 * What the console knows about the escrow it is displaying.
 *
 * - `unread`: no account has been read yet, or there is no app.
 * - `incomplete`: the node refused at least one box, so the escrow total is a
 *   lower bound and nothing can be said about solvency.
 * - `torn`: every box was read, but the balance moved while they were being
 *   read, so the total and the balance are from different moments.
 * - `complete`: the balance and every box come from one still moment, and
 *   the comparison between them means something.
 */
export type EscrowRead = 'unread' | 'incomplete' | 'torn' | 'complete';

export function escrowReadOf(state: {
  account: AppAccount | null;
  unreadableBoxes: number;
  consistent: boolean | null;
}): EscrowRead {
  if (state.account === null) return 'unread';
  if (state.unreadableBoxes > 0) return 'incomplete';
  if (state.consistent !== true) return 'torn';
  return 'complete';
}

/**
 * Whether the app can pay out every µALGO it holds in escrow, or `null` when
 * the read does not support an answer either way.
 *
 * The first version compared the balance against whatever boxes had been
 * read, so a node that refused half the boxes produced a smaller total, the
 * comparison passed, and the tile said the balance "covers every escrow" of a
 * registry it had only half seen. `null` here is not a verdict: it says the
 * console does not know, which is a different thing from the app being short.
 */
export function solvencyOf(state: {
  account: AppAccount | null;
  totalEscrowed: bigint;
  unreadableBoxes: number;
  consistent: boolean | null;
}): boolean | null {
  if (state.account === null || escrowReadOf(state) !== 'complete') return null;
  return state.account.spendable >= state.totalEscrowed;
}

/**
 * `Promise.all` with at most `limit` calls in flight.
 *
 * Results keep the order of `items`, and a rejection rejects the whole map,
 * exactly like `Promise.all`; callers that want per-item failures catch inside
 * `fn`, which is what `readRegistry` does.
 */
export async function mapPooled<T, R>(
  items: readonly T[],
  limit: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (!Number.isInteger(limit) || limit < 1) throw new Error(`pool limit must be >= 1, got ${limit}`);
  const results: R[] = new Array(items.length);
  let next = 0;
  const worker = async (): Promise<void> => {
    while (next < items.length) {
      const index = next;
      next += 1;
      results[index] = await fn(items[index], index);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

/**
 * Every box name the node lists, following `next-token` until it stops.
 *
 * A token that repeats would loop forever against a misbehaving node, so it
 * is a failure rather than a retry.
 */
export async function listBoxes(
  page: (next: string | undefined) => Promise<BoxPage>,
): Promise<{ readonly name: Uint8Array }[]> {
  const names: { readonly name: Uint8Array }[] = [];
  let next: string | undefined;
  do {
    const result = await page(next);
    names.push(...result.boxes);
    if (result.nextToken !== undefined && result.nextToken === next) {
      throw new Error('box listing repeated a next-token; refusing to loop');
    }
    next = result.nextToken;
  } while (next !== undefined);
  return names;
}

/** `RegistryReader` over a real node. */
export function algodRegistryReader(algod: algosdk.Algodv2, appId: number): RegistryReader {
  const address = algosdk.getApplicationAddress(appId);
  return {
    async account() {
      const account = await algod.accountInformation(address).do();
      return { amount: account.amount, minBalance: account.minBalance };
    },
    async boxPage(next) {
      let request = algod.getApplicationBoxes(appId).limit(BOX_PAGE_SIZE);
      // Not `.next(undefined)`: the builder writes whatever it is given into
      // the query string, and a literal "next=undefined" is a token algod
      // rejects.
      if (next !== undefined) request = request.next(next);
      const result = await request.do();
      return { boxes: result.boxes, nextToken: result.nextToken };
    },
    async box(name) {
      return (await algod.getApplicationBoxByName(appId, name).do()).value;
    },
  };
}

/**
 * One full read of the registry: balance, every box, balance again.
 *
 * The two balance reads bracket the box reads, which is how `consistent` is
 * decided. The account is reported from the second read, because when the two
 * disagree neither matches the boxes and the later one is at least current.
 */
export async function readRegistry(
  reader: RegistryReader,
  address: string,
  concurrency = BOX_READ_CONCURRENCY,
): Promise<RegistrySnapshot> {
  const before = await reader.account();
  const listed = await listBoxes((next) => reader.boxPage(next));
  let undecodable = 0;
  let unreadable = 0;
  const read = await mapPooled(listed, concurrency, async (box) => {
    const id = upkeepIdFromBoxName(box.name);
    if (id === null) return null;

    // Fetching and decoding fail for entirely different reasons and must
    // not share a catch. An earlier version wrapped both, so a 403 from a
    // rate-limited node, a timeout, or a box deleted between the listing
    // and the read all counted as "does not decode", and the banner then
    // told the visitor this app "is a different contract wearing these box
    // names". Cancelling an upkeep reliably produced that accusation
    // against an honest deployment, because the delete raced the read.
    let value: Uint8Array;
    try {
      value = await reader.box(box.name);
    } catch {
      // A box that cannot be fetched says nothing about the app. It is
      // either gone, which is what cancel does, or the node did not
      // answer. It does say something about this read: the escrow total is
      // now a lower bound, and `solvencyOf` refuses to compare it.
      unreadable += 1;
      return null;
    }

    try {
      return decodeUpkeep(id, value);
    } catch {
      // This one IS the signal. Box contents belong to whoever owns the
      // app, and a decoder throw inside a bare Promise.all rejects the
      // whole read. That pinned the connection at 'error' for one
      // malformed box, which cost an attacker about 0.058 ALGO and
      // switched off every warning on the page while the register button
      // stayed live. One bad box now drops one row.
      undecodable += 1;
      return null;
    }
  });
  const after = await reader.account();
  return {
    account: {
      address,
      amount: after.amount,
      minBalance: after.minBalance,
      spendable: after.amount - after.minBalance,
    },
    upkeeps: read
      .filter((upkeep): upkeep is Upkeep => upkeep !== null)
      .sort((left, right) => (left.id < right.id ? -1 : 1)),
    listedBoxes: listed.length,
    undecodableBoxes: undecodable,
    unreadableBoxes: unreadable,
    consistent: before.amount === after.amount && before.minBalance === after.minBalance,
  };
}

@Injectable({ providedIn: 'root' })
export class ArcronService {
  private timer: ReturnType<typeof setInterval> | null = null;
  /** Polls since the registry was last read in full. */
  private pollsSinceFullRead = FULL_READ_EVERY;

  /** Resolved once, before the signals below read it, so field order matters. */
  private readonly entry = readEntry();
  readonly network = signal<NetworkKey>(this.entry.network);
  readonly appId = signal<number | null>(this.entry.appId);

  /**
   * The app id this visitor said to continue to anyway, if any.
   *
   * Held per app id and only in memory. Accepting a foreign app is a decision
   * about this page, not a preference, so it dies with the tab and a reload
   * asks again.
   */
  private readonly acceptedAppId = signal<number | null>(null);

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
  /**
   * Boxes the node would not hand over. Not a claim about the app: a
   * cancelled upkeep's box is deleted, so a read racing a cancel finds
   * nothing, and a rate-limited node answers nothing for anything. It is a
   * claim about the read: while this is non-zero the escrow total is a lower
   * bound and `solvent` is unknown.
   */
  readonly unreadableBoxes = signal(0);
  /** Box names the last full read listed, so "N of M unreadable" has an M. */
  readonly listedBoxes = signal(0);
  /**
   * Whether the last full read's balance and boxes come from one still
   * moment. Null until a read has completed. See `RegistrySnapshot`.
   */
  readonly snapshotConsistent = signal<boolean | null>(null);

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
  /**
   * Whether it is safe to commit money right now.
   *
   * Every write guard used to key on `status() === 'ready'` alone, and
   * `refresh()` sets that on any read it completed without throwing. A node
   * answering for the wrong chain answers perfectly well, so the console
   * showed "wrong chain" in the header, raised a red banner, and left every
   * money button live underneath it. An app id of null did the same, which is
   * the default state of the front door.
   *
   * Two independent reviewers found this in the same pass, which is usually
   * what it takes to notice that a red page and a working button are not
   * contradictory to the code.
   */
  readonly canWrite = computed(() =>
    canCommitMoney({
      status: this.status(),
      genesisMatches: this.genesisMatches(),
      appId: this.appId(),
      quarantined: this.quarantined(),
    }),
  );

  /** The app id this console ships pointing at on the current network. */
  readonly canonicalAppId = computed(() => canonicalAppId(this.network()));

  /** What the console can say about the app id it is pointed at. */
  readonly standing = computed(() =>
    standingOf({ appId: this.appId(), network: this.network() }),
  );

  /** Whether this visitor has said to continue to the app currently selected. */
  readonly accepted = computed(() => this.acceptedAppId() === this.appId());

  /**
   * Whether every money button must stay dead.
   *
   * Deliberately independent of `status`: the comparison needs no chain data,
   * and gating it on a successful read would let a hostile app switch its own
   * quarantine off by serving one box that will not decode.
   */
  readonly quarantined = computed(() =>
    isQuarantined({ standing: this.standing(), accepted: this.accepted() }),
  );
  readonly totalEscrowed = computed(() =>
    this.upkeeps().reduce((total, upkeep) => total + upkeep.balance, 0n),
  );
  /** How much of the escrow the console has actually seen. */
  readonly escrowRead = computed<EscrowRead>(() =>
    escrowReadOf({
      account: this.appAccount(),
      unreadableBoxes: this.unreadableBoxes(),
      consistent: this.snapshotConsistent(),
    }),
  );
  /**
   * The app must be able to pay out every µALGO it holds in escrow. Null
   * while there is no app, and also while the read that would decide it is
   * incomplete or torn: a total summed over the boxes the node agreed to
   * serve is not the total, and comparing the balance against it proves
   * nothing in either direction.
   */
  readonly solvent = computed(() =>
    solvencyOf({
      account: this.appAccount(),
      totalEscrowed: this.totalEscrowed(),
      unreadableBoxes: this.unreadableBoxes(),
      consistent: this.snapshotConsistent(),
    }),
  );

  constructor() {
    effect(() => {
      const network = this.network();
      localStorage.setItem(NETWORK_STORAGE_KEY, network);
    });
    // `storeAppId` is the only writer, and it refuses a foreign app id. That
    // refusal is the reason a poisoned link cannot outlive the visit: the id
    // arrived in the URL and the URL is the only thing that can bring it back.
    effect(() => {
      storeAppId(localStorage, this.network(), this.appId(), this.standing());
    });
    // Keeping the address bar in step with this state is the shell's job, not
    // this service's: it goes through the router (`app.ts`), because writing
    // the URL behind the router's back leaves its own copy stale and the next
    // routerLink rebuilds the address from that stale copy.
    this.start();
  }

  setNetwork(network: NetworkKey): void {
    if (network === this.network()) return;
    this.network.set(network);
    this.appId.set(readAppId(network));
    this.forgetAcceptance();
    this.reset();
    void this.refresh();
  }

  setAppId(appId: number | null): void {
    if (appId === this.appId()) return;
    this.appId.set(appId);
    this.forgetAcceptance();
    this.reset();
    void this.refresh();
  }

  /** Leave a quarantined app for the one this console ships pointing at. */
  useCanonicalApp(): void {
    const canonical = this.canonicalAppId();
    if (canonical !== null) this.setAppId(canonical);
  }

  /**
   * The visitor has read the warning and wants to continue to this app.
   *
   * Unlocks the money buttons for this app id, in this tab, until the app id
   * changes or the page is reloaded. Nothing is written down.
   *
   * The comment used to say that and the code did not do it: `acceptedAppId`
   * was never cleared, so accepting app A, switching to B and switching back to
   * A unlocked money again with no second prompt. A review found it. Clearing
   * on every change makes the sentence true — returning to an app you once
   * accepted asks again.
   */
  acceptCurrentApp(): void {
    this.acceptedAppId.set(this.appId());
  }

  /**
   * Forget an acceptance because the app id moved.
   *
   * Called wherever the app id changes. Accepting is per-visit-to-an-app, not
   * per-app-forever.
   */
  private forgetAcceptance(): void {
    this.acceptedAppId.set(null);
  }

  start(): void {
    if (this.timer !== null) return;
    void this.refresh();
    this.timer = setInterval(() => {
      const full = this.pollsSinceFullRead >= FULL_READ_EVERY;
      void this.refresh(full);
    }, POLL_INTERVAL_MS);
  }

  stop(): void {
    if (this.timer === null) return;
    clearInterval(this.timer);
    this.timer = null;
  }

  /**
   * Read the chain.
   *
   * `full` forces the registry to be re-read rather than waiting for the next
   * scheduled full read. Callers outside the poll pass it, so anything you do
   * yourself appears at once and the saving only ever applies to idle time.
   */
  async refresh(full = true): Promise<void> {
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
        this.listedBoxes.set(0);
        this.undecodableBoxes.set(0);
        this.unreadableBoxes.set(0);
        this.snapshotConsistent.set(null);
      } else if (full || this.upkeeps().length === 0) {
        // The second condition matters on first load and after a network
        // switch, where waiting up to eight polls to show anything would read
        // as a broken page rather than as a cheap one.
        this.pollsSinceFullRead = 0;
        await this.refreshApp(algod, appId, current);
      } else {
        this.pollsSinceFullRead += 1;
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

    const reader = algodRegistryReader(algod, appId);
    const address = algosdk.getApplicationAddress(appId).toString();
    let snapshot = await readRegistry(reader, address);
    if (!snapshot.consistent && current()) {
      // Something moved the balance mid-read: an execution landing on a busy
      // registry, most often. One more pass costs the same as the first and
      // usually lands in a quiet window; if it does not, the tile says so
      // and the next scheduled full read tries again.
      snapshot = await readRegistry(reader, address);
    }
    if (!current()) return;
    // Set together: the account and the boxes on screen are always from the
    // same read, so the tile never compares one read's balance to another's
    // escrow.
    this.appAccount.set(snapshot.account);
    this.upkeeps.set(snapshot.upkeeps);
    this.listedBoxes.set(snapshot.listedBoxes);
    this.undecodableBoxes.set(snapshot.undecodableBoxes);
    this.unreadableBoxes.set(snapshot.unreadableBoxes);
    this.snapshotConsistent.set(snapshot.consistent);
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
    this.unreadableBoxes.set(0);
    this.listedBoxes.set(0);
    this.snapshotConsistent.set(null);
    this.genesisId.set(null);
    this.rateSamples.set([]);
  }
}

/**
 * Whether the developer controls are on.
 *
 * Read once at module load rather than per call, so it cannot change under a
 * page that has already decided which deployment it is showing.
 */
const DEV_STATE = readDevMode();

/** Whether developer controls are shown at all. */
export const DEV_MODE = DEV_STATE.enabled;

/**
 * Whether dev mode was on *before* this navigation.
 *
 * `?app=` and `?network=` require this rather than `DEV_MODE`, so that one link
 * cannot both turn dev mode on and point the console at a look-alike app.
 */
export const DEV_ESTABLISHED = DEV_STATE.established;

function readDevMode(): DevModeState {
  try {
    return devModeFrom(location.search, localStorage);
  } catch {
    // A browser blocking site data throws on access. Dev mode off is the safe
    // answer: one deployment, nothing configurable.
    return { enabled: false, established: false };
  }
}

/**
 * Where the console opens.
 *
 * Outside dev mode this is the canonical deployment and nothing else, so the
 * link and the memory are not consulted at all.
 */
function readEntry(): Entry {
  return entryFrom(
    location.search,
    localStorage.getItem(NETWORK_STORAGE_KEY),
    (network) => localStorage.getItem(appIdStorageKey(network)),
    DEV_ESTABLISHED,
  );
}

/** The app id for a network the *user* switched to, from memory only and never the link. */
function readAppId(network: NetworkKey): number | null {
  return rememberedAppId(network, localStorage.getItem(appIdStorageKey(network)));
}

export function describe(cause: unknown): string {
  if (cause instanceof Error) return cause.message;
  return String(cause);
}
