/**
 * The upkeep registry as the chain stores it.
 *
 * Box values are ARC-4 head/tail encoded `Upkeep` structs — the same layout
 * `scripts/keeper_bot.py::_decode_upkeep` reads. The head is 82 bytes: a
 * 32-byte creator, the target app, a 2-byte offset to the dynamic call data,
 * then the five uint64 fields. The tail holds a uint16 length followed by the
 * call data itself.
 */

import algosdk from 'algosdk';

/** Box names are `"u"` followed by the id as a big-endian uint64. */
export const BOX_NAME_PREFIX = 'u';
const BOX_NAME_BYTES = 9;
const HEAD_BYTES = 82;

/** Mirrors `BOX_MBR_FIXED` in smart_contracts/keeper/contract.py. */
export const BOX_MBR_FIXED = 2_500 + 400 * 93;
/** Mirrors MIN_UPKEEP_FEE / MIN_INTERVAL_ROUNDS. */
export const MIN_UPKEEP_FEE = 4_000;
export const MIN_INTERVAL_ROUNDS = 10;
/** Outer fee plus the extra fee covering `execute`'s two inner transactions. */
export const EXECUTE_FEE = 1_000 + 2_000;

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
    callData: raw.slice(tailOffset + 2, tailOffset + 2 + callDataLength),
  };
}

export function executionsRemaining(upkeep: Upkeep): bigint {
  return upkeep.feePerExecution === 0n ? 0n : upkeep.balance / upkeep.feePerExecution;
}

export function isDue(upkeep: Upkeep, currentRound: bigint): boolean {
  return currentRound >= upkeep.nextExecutionRound;
}

export function isExecutable(upkeep: Upkeep, currentRound: bigint): boolean {
  return isDue(upkeep, currentRound) && upkeep.balance >= upkeep.feePerExecution;
}

/** Rounds until due; negative once overdue. */
export function roundsUntilDue(upkeep: Upkeep, currentRound: bigint): bigint {
  return upkeep.nextExecutionRound - currentRound;
}

export function toHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
