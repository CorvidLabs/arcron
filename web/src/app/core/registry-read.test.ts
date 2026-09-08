/**
 * A full read of the registry, driven through a fake node.
 *
 * The console used to sum whatever boxes the node agreed to serve, compare
 * the balance against that, and print "covers every escrow". A node that
 * refused half the boxes made the page more confident, not less. These tests
 * pin the replacement: an unreadable box makes solvency unknown rather than
 * true, a balance that moves mid-read does the same, and a clean read
 * afterwards brings the boolean back.
 *
 * Everything here imports the code the console runs: `readRegistry` is what
 * `ArcronService.refreshApp` calls, and `solvencyOf` is what its `solvent`
 * signal computes. A test that redeclared either would pass with the fix
 * reverted, which is how the partial-sum solvency shipped in the first place.
 */

import { describe, expect, test } from 'bun:test';

import { encodeCallArgs, upkeepBoxName } from '@corvidlabs/arcron/upkeep';

import {
  type BoxPage,
  escrowReadOf,
  listBoxes,
  mapPooled,
  readRegistry,
  type RegistryReader,
  solvencyOf,
} from './arcron.service';

const APP_ADDRESS = 'YHXQIH2MYQE6AU26YT7VNNNVSY4BJXPUFZ2ZJOSKIXWBZSBRKSVQKYADVE';

/**
 * The 130-byte ARC-4 head written out by offset, like the rendering suite's
 * fixture and deliberately not through the decoder's own constants.
 */
function encodeBox(balance: bigint): Uint8Array {
  const head = new Uint8Array(130);
  const view = new DataView(head.buffer);
  view.setBigUint64(32, 769_891_902n); // target app
  view.setUint16(40, 130); // tail offset, which the decoder insists on
  view.setBigUint64(42, 1_286n); // interval
  view.setBigUint64(50, 55_400_100n); // next execution
  view.setBigUint64(58, 10_000n); // fee
  view.setBigUint64(66, balance);
  const tail = encodeCallArgs([]);
  const box = new Uint8Array(head.length + tail.length);
  box.set(head, 0);
  box.set(tail, head.length);
  return box;
}

type Stored = Uint8Array | 'unreadable';

/**
 * A node with a fixed set of boxes and a scripted sequence of balances.
 *
 * `amounts` is consumed one per account read; the last value repeats. Two
 * equal values are a still balance, two different ones are a torn read.
 */
function fakeNode(options: {
  boxes: Record<number, Stored>;
  amounts: bigint[];
  minBalance?: bigint;
  pageSize?: number;
}): RegistryReader & { pages: number; inFlight: number; peakInFlight: number } {
  const ids = Object.keys(options.boxes).map((id) => Number(id));
  const names = ids.map((id) => ({ name: upkeepBoxName(id) }));
  const pageSize = options.pageSize ?? names.length;
  let accountReads = 0;
  const node = {
    pages: 0,
    inFlight: 0,
    peakInFlight: 0,
    async account() {
      const amount = options.amounts[Math.min(accountReads, options.amounts.length - 1)];
      accountReads += 1;
      return { amount, minBalance: options.minBalance ?? 200_000n };
    },
    async boxPage(next: string | undefined): Promise<BoxPage> {
      node.pages += 1;
      const start = next === undefined ? 0 : Number(next);
      const boxes = names.slice(start, start + pageSize);
      const end = start + pageSize;
      return end < names.length ? { boxes, nextToken: String(end) } : { boxes };
    },
    async box(name: Uint8Array) {
      node.inFlight += 1;
      node.peakInFlight = Math.max(node.peakInFlight, node.inFlight);
      await new Promise((resolve) => setTimeout(resolve, 1));
      node.inFlight -= 1;
      const id = Number(new DataView(name.buffer, name.byteOffset).getBigUint64(1));
      const stored = options.boxes[id];
      if (stored === undefined || stored === 'unreadable') throw new Error('403 rate limited');
      return stored;
    },
  };
  return node;
}

/** The three reads a solvency verdict is built from, as the service wires them. */
function verdict(snapshot: Awaited<ReturnType<typeof readRegistry>>) {
  const totalEscrowed = snapshot.upkeeps.reduce((sum, upkeep) => sum + upkeep.balance, 0n);
  const state = {
    account: snapshot.account,
    totalEscrowed,
    unreadableBoxes: snapshot.unreadableBoxes,
    consistent: snapshot.consistent,
  };
  return { read: escrowReadOf(state), solvent: solvencyOf(state) };
}

describe('a complete read', () => {
  test('sums every box and compares the balance against the sum', async () => {
    const node = fakeNode({
      boxes: { 1: encodeBox(1_000_000n), 2: encodeBox(2_000_000n) },
      amounts: [3_300_000n],
    });
    const snapshot = await readRegistry(node, APP_ADDRESS);
    expect(snapshot.upkeeps.map((upkeep) => upkeep.id)).toEqual([1n, 2n]);
    expect(snapshot.listedBoxes).toBe(2);
    expect(snapshot.unreadableBoxes).toBe(0);
    expect(snapshot.consistent).toBe(true);
    expect(snapshot.account.spendable).toBe(3_100_000n);
    expect(verdict(snapshot)).toEqual({ read: 'complete', solvent: true });
  });

  test('an actually short balance reads as insolvent, not unknown', async () => {
    // The point of "unknown" is that it is reserved for reads that cannot
    // decide. A read that can decide, and decides against the app, must still
    // say so.
    const node = fakeNode({
      boxes: { 1: encodeBox(1_000_000n), 2: encodeBox(2_000_000n) },
      amounts: [2_500_000n],
    });
    expect(verdict(await readRegistry(node, APP_ADDRESS))).toEqual({
      read: 'complete',
      solvent: false,
    });
  });
});

describe('an incomplete read', () => {
  test('one unreadable box makes solvency unknown rather than true', async () => {
    // Before: box 2 dropped silently, the sum came to 1 ALGO, 3.1 covered it,
    // and the tile said "covers every escrow" over 3 ALGO of escrow it had
    // seen a third of.
    const node = fakeNode({
      boxes: { 1: encodeBox(1_000_000n), 2: 'unreadable', 3: encodeBox(2_000_000n) },
      amounts: [3_300_000n],
    });
    const snapshot = await readRegistry(node, APP_ADDRESS);
    expect(snapshot.listedBoxes).toBe(3);
    expect(snapshot.unreadableBoxes).toBe(1);
    expect(snapshot.upkeeps.map((upkeep) => upkeep.id)).toEqual([1n, 3n]);
    expect(verdict(snapshot)).toEqual({ read: 'incomplete', solvent: null });
  });

  test('every box unreadable is still unknown, and still not an accusation', async () => {
    // A rate-limited node answers nothing for anything. The sum is zero,
    // any balance covers zero, and the old code called that solvent.
    const node = fakeNode({
      boxes: { 1: 'unreadable', 2: 'unreadable', 3: 'unreadable' },
      amounts: [3_300_000n],
    });
    const snapshot = await readRegistry(node, APP_ADDRESS);
    expect(snapshot.unreadableBoxes).toBe(3);
    expect(snapshot.undecodableBoxes).toBe(0);
    expect(snapshot.upkeeps).toEqual([]);
    expect(verdict(snapshot)).toEqual({ read: 'incomplete', solvent: null });
  });

  test('unreadable and undecodable are still counted apart', async () => {
    // The banner treats one as a read problem and the other as an accusation,
    // so the counts must not bleed into each other.
    const node = fakeNode({
      boxes: { 1: 'unreadable', 2: new Uint8Array(140), 3: encodeBox(5n) },
      amounts: [1_000_000n],
    });
    const snapshot = await readRegistry(node, APP_ADDRESS);
    expect(snapshot.unreadableBoxes).toBe(1);
    expect(snapshot.undecodableBoxes).toBe(1);
    expect(snapshot.upkeeps.length).toBe(1);
  });

  test('a full read afterwards brings the boolean back', async () => {
    // Recovery is the other half of the contract. Unknown must not be sticky:
    // the next read that sees everything gets to answer.
    const boxes: Record<number, Stored> = { 1: encodeBox(1_000_000n), 2: 'unreadable' };
    const failing = fakeNode({ boxes, amounts: [3_300_000n] });
    expect(verdict(await readRegistry(failing, APP_ADDRESS)).solvent).toBeNull();

    boxes[2] = encodeBox(2_000_000n);
    const recovered = fakeNode({ boxes, amounts: [3_300_000n] });
    expect(verdict(await readRegistry(recovered, APP_ADDRESS))).toEqual({
      read: 'complete',
      solvent: true,
    });
  });
});

describe('a torn read', () => {
  test('a balance that moves mid-read makes solvency unknown', async () => {
    // An execution landed between the first balance and the last box. The
    // boxes and the balance are from different moments, and the sum of one
    // against the other proves nothing.
    const node = fakeNode({
      boxes: { 1: encodeBox(1_000_000n) },
      amounts: [1_300_000n, 1_290_000n],
    });
    const snapshot = await readRegistry(node, APP_ADDRESS);
    expect(snapshot.consistent).toBe(false);
    expect(snapshot.unreadableBoxes).toBe(0);
    // The later balance is the one reported, because the earlier one is
    // certainly stale.
    expect(snapshot.account.amount).toBe(1_290_000n);
    expect(verdict(snapshot)).toEqual({ read: 'torn', solvent: null });
  });

  test('and recovers on a read the balance sits still for', async () => {
    const node = fakeNode({
      boxes: { 1: encodeBox(1_000_000n) },
      amounts: [1_290_000n, 1_290_000n],
    });
    expect(verdict(await readRegistry(node, APP_ADDRESS))).toEqual({
      read: 'complete',
      solvent: true,
    });
  });
});

describe('the box listing', () => {
  test('follows next-token until the node stops handing one out', async () => {
    const boxes: Record<number, Stored> = {};
    for (let id = 1; id <= 23; id += 1) boxes[id] = encodeBox(BigInt(id));
    const node = fakeNode({ boxes, amounts: [10_000_000n], pageSize: 10 });
    const snapshot = await readRegistry(node, APP_ADDRESS);
    expect(node.pages).toBe(3);
    expect(snapshot.listedBoxes).toBe(23);
    expect(snapshot.upkeeps.length).toBe(23);
  });

  test('refuses a node that repeats its token instead of looping forever', async () => {
    const stuck = async (next: string | undefined): Promise<BoxPage> => ({
      boxes: [{ name: upkeepBoxName(1) }],
      nextToken: next ?? 'again',
    });
    let calls = 0;
    await expect(
      listBoxes((next) => {
        calls += 1;
        return stuck(next);
      }),
    ).rejects.toThrow(/repeated/);
    expect(calls).toBe(2);
  });
});

describe('the read pool', () => {
  test('never has more than the limit in flight', async () => {
    const boxes: Record<number, Stored> = {};
    for (let id = 1; id <= 30; id += 1) boxes[id] = encodeBox(1n);
    const node = fakeNode({ boxes, amounts: [10_000_000n] });
    await readRegistry(node, APP_ADDRESS, 8);
    expect(node.peakInFlight).toBeLessThanOrEqual(8);
    expect(node.peakInFlight).toBeGreaterThan(1);
  });

  test('keeps results in input order whatever order they finish in', async () => {
    const delays = [30, 5, 20, 1, 10];
    const out = await mapPooled(delays, 2, async (delay, index) => {
      await new Promise((resolve) => setTimeout(resolve, delay));
      return index;
    });
    expect(out).toEqual([0, 1, 2, 3, 4]);
  });

  test('an empty input resolves without spawning a worker', async () => {
    expect(await mapPooled([], 8, async () => 1)).toEqual([]);
  });

  test('a limit below one is a programming error, not a silent serial read', async () => {
    await expect(mapPooled([1], 0, async (x) => x)).rejects.toThrow(/limit/);
  });
});

describe('what solvency means with no read at all', () => {
  test('no account is unread, whatever the counters say', () => {
    const state = { account: null, totalEscrowed: 0n, unreadableBoxes: 0, consistent: null };
    expect(escrowReadOf(state)).toBe('unread');
    expect(solvencyOf(state)).toBeNull();
  });
});
