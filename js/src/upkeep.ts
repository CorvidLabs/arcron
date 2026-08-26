/**
 * The upkeep registry as the chain stores it.
 *
 * Box values are ARC-4 head/tail encoded `Upkeep` structs, the same layout
 * `scripts/keeper_bot.py::_decode_upkeep` reads. The head is 130 bytes: a
 * 32-byte creator, the target app, a 2-byte offset to the dynamic argument
 * list, then the eleven uint64 fields. The tail is an ARC-4 `byte[][]`: a
 * count, an offset per argument, then each argument's own length and bytes.
 */

import algosdk from 'algosdk';

/** Box names are `"u"` followed by the id as a big-endian uint64. */
export const BOX_NAME_PREFIX = 'u';
const BOX_NAME_BYTES = 9;
const HEAD_BYTES = 130;

/** Mirrors `BOX_MBR_FIXED` in smart_contracts/keeper/contract.py. */
export const BOX_MBR_FIXED = 2_500 + 400 * 139;
/** Mirrors MIN_UPKEEP_FEE / MAX_UPKEEP_FEE / MIN_INTERVAL_ROUNDS. */
export const MIN_UPKEEP_FEE = 4_000;
export const MAX_UPKEEP_FEE = 1_000_000_000;
export const MIN_INTERVAL_ROUNDS = 10;
export const MAX_INTERVAL_ROUNDS = 1_000_000_000;
/** How many app args an execution may carry, counting the selector. */
export const MAX_CALL_ARGS = 3;
/** What holding one more asset costs the app account, permanently. */
export const ASSET_OPT_IN_MBR = 100_000;
/** Outer fee plus the extra fee covering `execute`'s two inner transactions. */
export const EXECUTE_FEE = 1_000 + 2_000;

/** Whether a missed schedule is replayed or dropped. Mirrors the contract. */
export const CATCH_UP = 0n;
export const SKIP_AHEAD = 1n;

export interface Upkeep {
  readonly id: bigint;
  readonly creator: string;
  readonly targetApp: bigint;
  /** Every app arg of the registered call, in order; element 0 is app arg 0. */
  readonly callArgs: readonly Uint8Array[];
  readonly intervalRounds: bigint;
  readonly nextExecutionRound: bigint;
  readonly feePerExecution: bigint;
  readonly balance: bigint;
  readonly timesExecuted: bigint;
  readonly policy: bigint;
  /** The most this upkeep will ever pay for one run; 0n means it never escalates. */
  readonly feeCap: bigint;
  /** The round it last ran in, not the round it was scheduled for. */
  readonly lastServicedRound: bigint;
  /** An optional ASA bonus on top of the ALGO fee; 0n means ALGO only. */
  readonly feeAsset: bigint;
  readonly assetFee: bigint;
  readonly assetBalance: bigint;
}

/**
 * The ARC-4 `byte[][]` an upkeep stores: a uint16 count, a uint16 offset per
 * argument, then each argument's own uint16 length and bytes.
 *
 * The offsets are relative to the *end of the count*, not to the start of the
 * array. That one detail is worth stating, because getting it wrong produces a
 * plausible-looking encoding that decodes to garbage.
 */
export function encodeCallArgs(callArgs: readonly Uint8Array[]): Uint8Array {
  const count = callArgs.length;
  const headerBytes = 2 + 2 * count;
  const bodies = callArgs.map((arg) => {
    const body = new Uint8Array(2 + arg.length);
    new DataView(body.buffer).setUint16(0, arg.length);
    body.set(arg, 2);
    return body;
  });
  const out = new Uint8Array(headerBytes + bodies.reduce((sum, body) => sum + body.length, 0));
  const view = new DataView(out.buffer);
  view.setUint16(0, count);
  let position = headerBytes;
  bodies.forEach((body, index) => {
    view.setUint16(2 + 2 * index, position - 2);
    out.set(body, position);
    position += body.length;
  });
  return out;
}

/** What one upkeep box costs the app account, per the contract's formula. */
export function boxMbr(callArgs: readonly Uint8Array[]): number {
  return BOX_MBR_FIXED + 400 * encodeCallArgs(callArgs).length;
}

export function upkeepBoxName(id: bigint | number): Uint8Array {
  const name = new Uint8Array(BOX_NAME_BYTES);
  name[0] = BOX_NAME_PREFIX.charCodeAt(0);
  new DataView(name.buffer).setBigUint64(1, BigInt(id));
  return name;
}

export function upkeepIdFromBoxName(name: Uint8Array): bigint | null {
  if (name.length < BOX_NAME_BYTES || name[0] !== BOX_NAME_PREFIX.charCodeAt(0)) return null;
  return new DataView(name.buffer, name.byteOffset, name.byteLength).getBigUint64(1);
}

export function decodeUpkeep(id: bigint, raw: Uint8Array): Upkeep {
  if (raw.length < HEAD_BYTES + 2) {
    throw new Error(`Upkeep box ${id} is ${raw.length} bytes, too short to decode`);
  }
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  const tailOffset = view.getUint16(40);
  // The Python bot refuses a tail offset that is not exactly the head size,
  // and this did not, which made the two decoders disagree about what counts
  // as an upkeep. A box whose offset has been patched decodes here as a
  // plausible upkeep with no call args, so a hostile app's boxes read as
  // ordinary and the console's "does not decode" warning never fires. Reading
  // a foreign struct as one of ours is how a reader invents fees that are not
  // there.
  if (tailOffset !== HEAD_BYTES) {
    throw new Error(
      `Upkeep box ${id} has a tail offset of ${tailOffset}, not ${HEAD_BYTES}. ` +
        `This is not this contract's Upkeep struct.`,
    );
  }
  const argCount = view.getUint16(tailOffset);
  const callArgs: Uint8Array[] = [];
  for (let index = 0; index < argCount; index += 1) {
    // Offsets are measured from just after the count, so the +2.
    const argAt = tailOffset + 2 + view.getUint16(tailOffset + 2 + 2 * index);
    const length = view.getUint16(argAt);
    callArgs.push(raw.slice(argAt + 2, argAt + 2 + length));
  }
  return {
    id,
    creator: algosdk.encodeAddress(raw.subarray(0, 32)),
    targetApp: view.getBigUint64(32),
    intervalRounds: view.getBigUint64(42),
    nextExecutionRound: view.getBigUint64(50),
    feePerExecution: view.getBigUint64(58),
    balance: view.getBigUint64(66),
    timesExecuted: view.getBigUint64(74),
    policy: view.getBigUint64(82),
    feeCap: view.getBigUint64(90),
    lastServicedRound: view.getBigUint64(98),
    feeAsset: view.getBigUint64(106),
    assetFee: view.getBigUint64(114),
    assetBalance: view.getBigUint64(122),
    callArgs,
  };
}

/**
 * What one execution of this upkeep would pay at `currentRound`.
 *
 * The twin of `execute`'s escalation arithmetic in
 * smart_contracts/keeper/contract.py, and of `effective_fee` in
 * scripts/keeper_bot.py. The fee rises linearly from the base to the cap over
 * one missed interval and then holds, and lateness is measured from the last
 * service rather than from the schedule, so a keeper draining a backlog is
 * paid the ceiling once, not once per replay. A zero cap never escalates, and
 * an upkeep never bids more than it holds: an escrow below the escalated fee
 * drops back to the base fee rather than freezing at a price it cannot pay.
 * A replay of a backlog never escalates at all: `nextExecutionRound <=
 * lastServicedRound` means the upkeep was already behind when it last ran.
 */
export function effectiveFee(upkeep: Upkeep, currentRound: bigint): bigint {
  const base = upkeep.feePerExecution;
  const cap = upkeep.feeCap;
  if (cap <= base || upkeep.nextExecutionRound <= upkeep.lastServicedRound) return base;
  const interval = upkeep.intervalRounds > 0n ? upkeep.intervalRounds : 1n;
  const lateness = max(currentRound - upkeep.lastServicedRound, 0n);
  const excess = min(max(lateness - interval, 0n), interval);
  const fee = base + ((cap - base) * excess) / interval;
  return upkeep.balance < fee ? base : fee;
}

/** True when this upkeep's fee can rise above what its creator wrote down. */
export function escalates(upkeep: Upkeep): boolean {
  return upkeep.feeCap > upkeep.feePerExecution;
}

function max(a: bigint, b: bigint): bigint {
  return a > b ? a : b;
}

function min(a: bigint, b: bigint): bigint {
  return a < b ? a : b;
}

/**
 * Runs the escrow can still pay for.
 *
 * Priced at the cap when one is set: that is the worst case the creator can
 * actually be charged, and it is the number they need to budget against.
 */
export function executionsRemaining(upkeep: Upkeep): bigint {
  const worstCase = upkeep.feeCap > upkeep.feePerExecution ? upkeep.feeCap : upkeep.feePerExecution;
  return worstCase === 0n ? 0n : upkeep.balance / worstCase;
}

export function isDue(upkeep: Upkeep, currentRound: bigint): boolean {
  return currentRound >= upkeep.nextExecutionRound;
}

export function isExecutable(upkeep: Upkeep, currentRound: bigint): boolean {
  // Against the effective fee, not the base one: escalation raises the bar an
  // upkeep has to clear, so it can go dormant at a balance its creator thought
  // was enough.
  return isDue(upkeep, currentRound) && upkeep.balance >= effectiveFee(upkeep, currentRound);
}

/** Rounds until due; negative once overdue. */
export function roundsUntilDue(upkeep: Upkeep, currentRound: bigint): bigint {
  return upkeep.nextExecutionRound - currentRound;
}

export function toHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
