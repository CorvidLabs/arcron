/**
 * The upkeep registry as the chain stores it.
 *
 * Box values are ARC-4 head/tail encoded `Upkeep` structs — the same layout
 * `scripts/keeper_bot.py::_decode_upkeep` reads. The head is 106 bytes: a
 * 32-byte creator, the target app, a 2-byte offset to the dynamic call data,
 * then the eight uint64 fields. The tail holds a uint16 length followed by the
 * call data itself.
 */

import algosdk from 'algosdk';

/** Box names are `"u"` followed by the id as a big-endian uint64. */
export const BOX_NAME_PREFIX = 'u';
const BOX_NAME_BYTES = 9;
const HEAD_BYTES = 106;

/** Mirrors `BOX_MBR_FIXED` in smart_contracts/keeper/contract.py. */
export const BOX_MBR_FIXED = 2_500 + 400 * 117;
/** Mirrors MIN_UPKEEP_FEE / MAX_UPKEEP_FEE / MIN_INTERVAL_ROUNDS. */
export const MIN_UPKEEP_FEE = 4_000;
export const MAX_UPKEEP_FEE = 1_000_000_000;
export const MIN_INTERVAL_ROUNDS = 10;
/** Outer fee plus the extra fee covering `execute`'s two inner transactions. */
export const EXECUTE_FEE = 1_000 + 2_000;

/** Whether a missed schedule is replayed or dropped. Mirrors the contract. */
export const CATCH_UP = 0n;
export const SKIP_AHEAD = 1n;

export interface Upkeep {
  readonly id: bigint;
  readonly creator: string;
  readonly targetApp: bigint;
  readonly callData: Uint8Array;
  readonly intervalRounds: bigint;
  readonly nextExecutionRound: bigint;
  readonly feePerExecution: bigint;
  readonly balance: bigint;
  readonly timesExecuted: bigint;
  readonly policy: bigint;
  /** The most this upkeep will ever pay for one run; 0n means it never escalates. */
  readonly feeCap: bigint;
  /** The round it last ran in — not the round it was scheduled for. */
  readonly lastServicedRound: bigint;
}

/** What one upkeep box costs the app account, per the contract's formula. */
export function boxMbr(callDataLength: number): number {
  return BOX_MBR_FIXED + 400 * callDataLength;
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
  const callDataLength = view.getUint16(tailOffset);
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
    callData: raw.slice(tailOffset + 2, tailOffset + 2 + callDataLength),
  };
}

/**
 * What one execution of this upkeep would pay at `currentRound`.
 *
 * The twin of `execute`'s escalation arithmetic in
 * smart_contracts/keeper/contract.py, and of `effective_fee` in
 * scripts/keeper_bot.py. The fee rises linearly from the base to the cap over
 * one missed interval and then holds, and lateness is measured from the last
 * service rather than from the schedule — so a keeper draining a backlog is
 * paid the ceiling once, not once per replay. A zero cap never escalates.
 */
export function effectiveFee(upkeep: Upkeep, currentRound: bigint): bigint {
  const base = upkeep.feePerExecution;
  const cap = upkeep.feeCap;
  if (cap <= base) return base;
  const interval = upkeep.intervalRounds > 0n ? upkeep.intervalRounds : 1n;
  const lateness = max(currentRound - upkeep.lastServicedRound, 0n);
  const excess = min(max(lateness - interval, 0n), interval);
  return base + ((cap - base) * excess) / interval;
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
