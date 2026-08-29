/**
 * A keeper network that never moves.
 *
 * The console is a live view of a chain, which is the worst possible thing to
 * point a rendering suite at: the round ticks every 2.5 seconds, an upkeep
 * that was due becomes scheduled, the registry changes shape when somebody
 * registers, and a TestNet outage turns every check red for reasons that have
 * nothing to do with the page. A suite that fails when a round number changes
 * is one people delete.
 *
 * So the chain is stubbed at the HTTP boundary rather than mocked inside the
 * app: `page.route` answers algod, and everything above it - algosdk, the poll
 * in `ArcronService`, the ARC-4 box decoder, the escalation arithmetic - is the
 * real code running against fixed bytes. Nothing here needs LocalNet running,
 * a funded account, or a network connection.
 *
 * The registry is built to put every state on screen at once, because a state
 * that never renders is a state the suite never audits: one upkeep due, one
 * scheduled, one starved, one escalating past its base fee, and one with an
 * ASA bonus and two call arguments to make the widest row the table can draw.
 */

import algosdk from 'algosdk';
import type { Page } from '@playwright/test';

import { encodeCallArgs, upkeepBoxName } from '@corvidlabs/arcron/upkeep';

/** The deployment the console ships pointing at; anything else is quarantined. */
export const CANONICAL_APP_ID = 769891898;
/**
 * A look-alike, to drive the quarantine. It answers with the same registry,
 * because that is the attack: identical ABI, identical boxes, different escrow.
 */
export const FOREIGN_APP_ID = 771234567;
/** The demo target, `pulse`. */
export const TARGET_APP_ID = 769891902;
/** Live TestNet Rain. Stubbed so `/rain` does not 404 the draw. */
export const RAIN_APP_ID = 770029154;

/** Frozen. Every "due in N rounds" on the page is derived from this one number. */
export const ROUND = 55_400_000n;

export const GENESIS_ID = 'testnet-v1.0';
const GENESIS_HASH = 'SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=';
const MIN_FEE = 1_000n;

const ALGOD_HOST = 'testnet-api.algonode.cloud';

interface Fixture {
  readonly id: bigint;
  readonly creator: Uint8Array;
  readonly targetApp: bigint;
  readonly callArgs: readonly Uint8Array[];
  readonly intervalRounds: bigint;
  readonly nextExecutionRound: bigint;
  readonly feePerExecution: bigint;
  readonly balance: bigint;
  readonly timesExecuted: bigint;
  readonly policy: bigint;
  readonly feeCap: bigint;
  readonly lastServicedRound: bigint;
  readonly feeAsset: bigint;
  readonly assetFee: bigint;
  readonly assetBalance: bigint;
}

/** A deterministic 32-byte public key, so every address is stable across runs. */
function creator(seed: number): Uint8Array {
  const key = new Uint8Array(32);
  for (let index = 0; index < 32; index += 1) key[index] = (seed * 31 + index * 7) % 251;
  return key;
}

/** Four bytes of method selector, the way `keeper-abi.ts` writes one. */
function selector(bytes: readonly number[]): Uint8Array {
  return new Uint8Array(bytes);
}

const CATCH_UP = 0n;
const SKIP_AHEAD = 1n;

/**
 * Five upkeeps, chosen for what they make the page draw rather than for
 * realism. Ids are spread out because consecutive ids hide an alignment bug in
 * the box-name encoding.
 */
export const UPKEEPS: readonly Fixture[] = [
  // Due now, healthy escrow: a live Execute button and a highlighted row.
  {
    id: 7n,
    creator: creator(1),
    targetApp: BigInt(TARGET_APP_ID),
    callArgs: [selector([0x8a, 0x1f, 0x2b, 0x44])],
    intervalRounds: 1_286n,
    nextExecutionRound: ROUND - 12n,
    feePerExecution: 4_000n,
    balance: 604_000n,
    timesExecuted: 151n,
    policy: SKIP_AHEAD,
    feeCap: 0n,
    lastServicedRound: ROUND - 1_298n,
    feeAsset: 0n,
    assetFee: 0n,
    assetBalance: 0n,
  },
  // On schedule and a long way out: the widest "next run" cell.
  {
    id: 12n,
    creator: creator(2),
    targetApp: 770_112_004n,
    callArgs: [selector([0x11, 0xc0, 0xde, 0x01])],
    intervalRounds: 30_857n,
    nextExecutionRound: ROUND + 28_431n,
    feePerExecution: 120_000n,
    balance: 3_600_000n,
    timesExecuted: 4n,
    policy: CATCH_UP,
    feeCap: 0n,
    lastServicedRound: ROUND - 2_426n,
    feeAsset: 0n,
    assetFee: 0n,
    assetBalance: 0n,
  },
  // Starved: escrow below one fee, so nothing can run it and the row greys out.
  {
    id: 21n,
    creator: creator(3),
    targetApp: 769_954_311n,
    callArgs: [selector([0x63, 0x7a, 0x9d, 0x02])],
    intervalRounds: 240n,
    nextExecutionRound: ROUND - 900n,
    feePerExecution: 9_500n,
    balance: 3_100n,
    timesExecuted: 88n,
    policy: SKIP_AHEAD,
    feeCap: 0n,
    lastServicedRound: ROUND - 1_140n,
    feeAsset: 0n,
    assetFee: 0n,
    assetBalance: 0n,
  },
  // Late, with a ceiling: the escalated fee renders in --warning.
  {
    id: 34n,
    creator: creator(4),
    targetApp: 770_400_918n,
    callArgs: [selector([0xde, 0xad, 0xbe, 0xef])],
    intervalRounds: 500n,
    nextExecutionRound: ROUND - 780n,
    feePerExecution: 5_000n,
    balance: 250_000n,
    timesExecuted: 12n,
    policy: CATCH_UP,
    feeCap: 45_000n,
    lastServicedRound: ROUND - 1_280n,
    feeAsset: 0n,
    assetFee: 0n,
    assetBalance: 0n,
  },
  // An ASA bonus and two arguments: the longest cell the upkeep page can draw.
  {
    id: 58n,
    creator: creator(5),
    targetApp: 770_998_143n,
    callArgs: [
      selector([0x2c, 0x4e, 0x00, 0x7f]),
      new Uint8Array([0, 0, 0, 0, 0, 0, 0x27, 0x10]),
      new TextEncoder().encode('quarterly-settlement'),
    ],
    intervalRounds: 925_714n,
    nextExecutionRound: ROUND + 411_002n,
    feePerExecution: 250_000n,
    balance: 7_500_000n,
    timesExecuted: 2n,
    policy: CATCH_UP,
    feeCap: 900_000n,
    lastServicedRound: ROUND - 514_712n,
    feeAsset: 3_225_439_167n,
    assetFee: 50_000n,
    assetBalance: 1_200_000n,
  },
];

/**
 * The 130-byte ARC-4 head, written out by offset.
 *
 * Deliberately not shared with `decodeUpkeep`'s constants. If the fixture were
 * generated by the same table the decoder reads, a wrong offset would cancel
 * out and the suite would happily render nonsense.
 */
function encodeUpkeep(upkeep: Fixture): Uint8Array {
  const head = new Uint8Array(130);
  const view = new DataView(head.buffer);
  head.set(upkeep.creator, 0);
  view.setBigUint64(32, upkeep.targetApp);
  view.setUint16(40, 130);
  view.setBigUint64(42, upkeep.intervalRounds);
  view.setBigUint64(50, upkeep.nextExecutionRound);
  view.setBigUint64(58, upkeep.feePerExecution);
  view.setBigUint64(66, upkeep.balance);
  view.setBigUint64(74, upkeep.timesExecuted);
  view.setBigUint64(82, upkeep.policy);
  view.setBigUint64(90, upkeep.feeCap);
  view.setBigUint64(98, upkeep.lastServicedRound);
  view.setBigUint64(106, upkeep.feeAsset);
  view.setBigUint64(114, upkeep.assetFee);
  view.setBigUint64(122, upkeep.assetBalance);

  const tail = encodeCallArgs([...upkeep.callArgs]);
  const box = new Uint8Array(head.length + tail.length);
  box.set(head, 0);
  box.set(tail, head.length);
  return box;
}

function base64(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString('base64');
}

const BOXES = UPKEEPS.map((upkeep) => ({
  name: base64(upkeepBoxName(upkeep.id)),
  value: base64(encodeUpkeep(upkeep)),
}));

/** Every app id the stub answers for. Anything else is a genuine 404. */
const KNOWN_APPS = new Set([CANONICAL_APP_ID, FOREIGN_APP_ID, RAIN_APP_ID]);

function statusBody(): unknown {
  return {
    'catchup-time': 0,
    'last-round': Number(ROUND),
    'last-version': 'https://github.com/algorandfoundation/specs/tree/arcron-e2e',
    'next-version': 'https://github.com/algorandfoundation/specs/tree/arcron-e2e',
    'next-version-round': Number(ROUND) + 1,
    'next-version-supported': true,
    'stopped-at-unsupported-round': false,
    'time-since-last-round': 2_800_000_000,
  };
}

function paramsBody(): unknown {
  return {
    'consensus-version': 'https://github.com/algorandfoundation/specs/tree/arcron-e2e',
    fee: 0,
    'genesis-hash': GENESIS_HASH,
    'genesis-id': GENESIS_ID,
    'last-round': Number(ROUND),
    'min-fee': Number(MIN_FEE),
  };
}

function rainState(): unknown[] {
  const gate = creator(11);
  return [
    { key: base64(new TextEncoder().encode('beacon_app')), value: { bytes: '', type: 2, uint: 600011887 } },
    { key: base64(new TextEncoder().encode('pot')), value: { bytes: '', type: 2, uint: 0 } },
    { key: base64(new TextEncoder().encode('tickets')), value: { bytes: '', type: 2, uint: 0 } },
    { key: base64(new TextEncoder().encode('draw_id')), value: { bytes: '', type: 2, uint: 0 } },
    { key: base64(new TextEncoder().encode('draw_open')), value: { bytes: '', type: 2, uint: 0 } },
    { key: base64(new TextEncoder().encode('commit_round')), value: { bytes: '', type: 2, uint: 0 } },
    { key: base64(new TextEncoder().encode('prize')), value: { bytes: '', type: 2, uint: 0 } },
    { key: base64(new TextEncoder().encode('tickets_snapshot')), value: { bytes: '', type: 2, uint: 0 } },
    { key: base64(new TextEncoder().encode('draws_resolved')), value: { bytes: '', type: 2, uint: 0 } },
    { key: base64(new TextEncoder().encode('prize_asset')), value: { bytes: '', type: 2, uint: 0 } },
    {
      key: base64(new TextEncoder().encode('gate_creator')),
      value: { bytes: base64(gate), type: 1, uint: 0 },
    },
    {
      key: base64(new TextEncoder().encode('last_winner')),
      value: { bytes: base64(new Uint8Array(32)), type: 1, uint: 0 },
    },
    {
      key: base64(new TextEncoder().encode('gate_unit_prefix')),
      value: { bytes: base64(new TextEncoder().encode('corvid')), type: 1, uint: 0 },
    },
  ];
}

function applicationBody(appId: number): unknown {
  if (appId === RAIN_APP_ID) {
    return {
      id: appId,
      params: {
        'approval-program': base64(new Uint8Array([0x0a, 0x81, 0x01])),
        'clear-state-program': base64(new Uint8Array([0x0a, 0x81, 0x01])),
        creator: algosdk.encodeAddress(creator(9)),
        'global-state': rainState(),
        'global-state-schema': { 'num-byte-slice': 3, 'num-uint': 10 },
        'local-state-schema': { 'num-byte-slice': 0, 'num-uint': 0 },
      },
    };
  }
  return {
    id: appId,
    params: {
      'approval-program': base64(new Uint8Array([0x0a, 0x81, 0x01])),
      'clear-state-program': base64(new Uint8Array([0x0a, 0x81, 0x01])),
      creator: algosdk.encodeAddress(creator(9)),
      'global-state': [
        {
          key: base64(new TextEncoder().encode('next_upkeep_id')),
          value: { bytes: '', type: 2, uint: 59 },
        },
        // Deliberately unfrozen. The console is supposed to warn that the
        // creator can still replace the programs, and that warning is one of
        // the surfaces this suite audits.
        {
          key: base64(new TextEncoder().encode('frozen')),
          value: { bytes: '', type: 2, uint: 0 },
        },
      ],
      'global-state-schema': { 'num-byte-slice': 0, 'num-uint': 2 },
      'local-state-schema': { 'num-byte-slice': 0, 'num-uint': 0 },
    },
  };
}

function accountBody(address: string, amount: bigint, minBalance: bigint): unknown {
  return {
    address,
    amount: Number(amount),
    'amount-without-pending-rewards': Number(amount),
    'min-balance': Number(minBalance),
    'pending-rewards': 0,
    rewards: 0,
    round: Number(ROUND),
    status: 'Offline',
    'total-apps-opted-in': 0,
    'total-assets-opted-in': 1,
    'total-created-apps': 0,
    'total-created-assets': 0,
  };
}

/** Escrow the app is holding, plus a comfortable margin, so it reads solvent. */
const APP_AMOUNT = UPKEEPS.reduce((total, upkeep) => total + upkeep.balance, 0n) + 2_000_000n;
const APP_MIN_BALANCE = 100_000n + BigInt(UPKEEPS.length) * 58_100n;

function json(body: unknown): { status: number; contentType: string; body: string } {
  return { status: 200, contentType: 'application/json', body: JSON.stringify(body) };
}

/**
 * Answer every algod call the console makes, and fail loudly on one it does not.
 *
 * A stub that quietly 200s an unknown path is worse than no stub: the console
 * would render a blank registry and the suite would audit an empty page while
 * reporting success. Anything unrecognised comes back 501 with the path in it.
 */
export async function stubAlgod(page: Page): Promise<void> {
  await page.route(`**://${ALGOD_HOST}/**`, async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === '/v2/status') return route.fulfill(json(statusBody()));
    if (path === '/v2/transactions/params') return route.fulfill(json(paramsBody()));

    const application = /^\/v2\/applications\/(\d+)$/.exec(path);
    if (application) {
      const appId = Number(application[1]);
      if (!KNOWN_APPS.has(appId)) {
        return route.fulfill({
          status: 404,
          contentType: 'application/json',
          body: JSON.stringify({ message: 'application does not exist' }),
        });
      }
      return route.fulfill(json(applicationBody(appId)));
    }

    const boxes = /^\/v2\/applications\/(\d+)\/boxes$/.exec(path);
    if (boxes) {
      const appId = Number(boxes[1]);
      if (appId === RAIN_APP_ID) return route.fulfill(json({ boxes: [] }));
      return route.fulfill(json({ boxes: BOXES.map((box) => ({ name: box.name })) }));
    }

    const box = /^\/v2\/applications\/(\d+)\/box$/.exec(path);
    if (box) {
      // `URLSearchParams` decodes `+` as a space, which corrupts base64 box
      // names, so the query is read off the raw URL instead.
      const raw = /[?&]name=([^&]*)/.exec(url.search);
      const name = raw === null ? '' : decodeURIComponent(raw[1]).replace(/^b64:/, '');
      const found = BOXES.find((candidate) => candidate.name === name);
      if (found === undefined) {
        return route.fulfill({
          status: 404,
          contentType: 'application/json',
          body: JSON.stringify({ message: 'box not found' }),
        });
      }
      return route.fulfill(json({ name: found.name, round: Number(ROUND), value: found.value }));
    }

    const account = /^\/v2\/accounts\/([A-Z2-7]+)$/.exec(path);
    if (account) {
      const address = account[1];
      const appAddresses = new Set(
        [...KNOWN_APPS].map((appId) => algosdk.getApplicationAddress(appId).toString()),
      );
      return route.fulfill(
        json(
          appAddresses.has(address)
            ? accountBody(address, APP_AMOUNT, APP_MIN_BALANCE)
            : accountBody(address, 25_000_000n, 200_000n),
        ),
      );
    }

    return route.fulfill({
      status: 501,
      contentType: 'application/json',
      body: JSON.stringify({ message: `arcron e2e: no stub for ${path}` }),
    });
  });
}

/**
 * Cut the page off from the internet entirely.
 *
 * `index.html` pulls Schibsted Grotesk and Spline Sans Mono from Google Fonts.
 * Left alone, every measurement here would depend on whether the machine
 * running the suite had a network, which is the definition of a flaky test:
 * the same code would report different overflow on a plane. The stylesheet is
 * answered with nothing, so the fallback stack applies on every run.
 *
 * Set `ARCRON_E2E_WEBFONTS=1` to let the real faces through when the point of
 * the run is a screenshot somebody is going to look at.
 */
export async function stubWebFonts(page: Page): Promise<void> {
  if (process.env['ARCRON_E2E_WEBFONTS'] === '1') return;
  await page.route('**://fonts.googleapis.com/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/css', body: '/* blocked by the e2e suite */' }),
  );
  await page.route('**://fonts.gstatic.com/**', (route) => route.abort());
}
