/**
 * Rain, the scheduled draw Arcron calls.
 *
 * Tickets persist across draws: `enter` once and you are in every future
 * fire, whether the upkeep is hourly, daily or weekly. Cadence is the
 * keeper's job. Claiming still requires holding a collection token at
 * that moment, on a gated instance.
 */

import algosdk from 'algosdk';

import type { NetworkKey } from './networks';

/** One ticket box: 9-byte name, 32-byte address. Same as the Python contract. */
export const TICKET_MBR = 2_500 + 400 * 41;
/** Winner allocation box, same size class as a ticket. */
export const ALLOCATION_MBR = TICKET_MBR;
/** Inner payment on `claim` is fee=0; the outer call covers it. */
export const CLAIM_FEE = 2_000;
/** `resolve` inner-calls the beacon. */
export const RESOLVE_FEE = 3_000;
export const ENTER_FEE = 2_000;
export const DEPOSIT_FEE = 2_000;
export const ABANDON_FEE = 2_000;

export const ZERO_ADDRESS = algosdk.ALGORAND_ZERO_ADDRESS_STRING;

export interface RainDeployment {
  readonly appId: number;
  /** Arcron upkeep that calls `draw()uint64`. */
  readonly upkeepId: number;
  readonly keeperAppId: number;
}

/** Live TestNet dogfood: gated to Corvid NFTs, ALGO pot, upkeep 79. */
export const TESTNET_RAIN: RainDeployment = {
  appId: 770_029_154,
  upkeepId: 79,
  keeperAppId: 769_891_898,
};

export function rainFor(network: NetworkKey): RainDeployment | null {
  return network === 'testnet' ? TESTNET_RAIN : null;
}

export interface RainState {
  readonly appId: number;
  readonly beaconApp: bigint;
  readonly pot: bigint;
  readonly tickets: bigint;
  readonly drawId: bigint;
  readonly drawOpen: boolean;
  readonly commitRound: bigint;
  readonly prize: bigint;
  readonly ticketsSnapshot: bigint;
  readonly drawsResolved: bigint;
  readonly lastWinner: string;
  readonly gateCreator: string;
  /** Empty: any asset from `gateCreator`. Set: unit name must start with this. */
  readonly gateUnitPrefix: string;
  readonly prizeAsset: bigint;
  readonly gated: boolean;
}

export interface QualifyingAsset {
  readonly id: number;
  readonly unitName: string;
  readonly name: string;
  readonly amount: bigint;
}

function textKey(key: Uint8Array): string {
  return new TextDecoder().decode(key);
}

function asBytes(value: Uint8Array | string | undefined): Uint8Array {
  if (value === undefined) return new Uint8Array();
  if (value instanceof Uint8Array) return value;
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function asUint(value: { uint?: number | bigint } | undefined): bigint {
  if (value?.uint === undefined) return 0n;
  return BigInt(value.uint);
}

function asAddress(bytes: Uint8Array): string {
  if (bytes.length !== 32) return ZERO_ADDRESS;
  return algosdk.encodeAddress(bytes);
}

export interface GlobalEntry {
  readonly key: Uint8Array;
  readonly value: {
    readonly bytes?: Uint8Array | string;
    readonly uint?: number | bigint;
    readonly type?: number;
  };
}

const BYTE_KEYS = new Set(['last_winner', 'gate_creator', 'gate_unit_prefix']);

/** Decode rain global state. Missing keys read as zero / the zero address. */
export function decodeRainState(appId: number, entries: readonly GlobalEntry[]): RainState {
  const ints = new Map<string, bigint>();
  const blobs = new Map<string, Uint8Array>();
  for (const entry of entries) {
    const name = textKey(entry.key);
    if (BYTE_KEYS.has(name)) blobs.set(name, asBytes(entry.value.bytes));
    else ints.set(name, asUint(entry.value));
  }

  const gateCreator = asAddress(blobs.get('gate_creator') ?? new Uint8Array(32));
  const lastWinner = asAddress(blobs.get('last_winner') ?? new Uint8Array(32));
  const prefixBytes = blobs.get('gate_unit_prefix') ?? new Uint8Array();
  const gateUnitPrefix = new TextDecoder().decode(prefixBytes);

  return {
    appId,
    beaconApp: ints.get('beacon_app') ?? 0n,
    pot: ints.get('pot') ?? 0n,
    tickets: ints.get('tickets') ?? 0n,
    drawId: ints.get('draw_id') ?? 0n,
    drawOpen: (ints.get('draw_open') ?? 0n) === 1n,
    commitRound: ints.get('commit_round') ?? 0n,
    prize: ints.get('prize') ?? 0n,
    ticketsSnapshot: ints.get('tickets_snapshot') ?? 0n,
    drawsResolved: ints.get('draws_resolved') ?? 0n,
    lastWinner,
    gateCreator,
    gateUnitPrefix,
    prizeAsset: ints.get('prize_asset') ?? 0n,
    gated: gateCreator !== ZERO_ADDRESS,
  };
}

export function ticketBoxName(index: bigint): Uint8Array {
  const name = new Uint8Array(9);
  name[0] = 0x74; // t
  const view = new DataView(name.buffer);
  view.setBigUint64(1, index);
  return name;
}

export function allocationBoxName(address: string): Uint8Array {
  const decoded = algosdk.decodeAddress(address).publicKey;
  const name = new Uint8Array(1 + decoded.length);
  name[0] = 0x61; // a
  name.set(decoded, 1);
  return name;
}

/** Whether this holding is a ticket to a gated rain. */
export function qualifies(
  state: Pick<RainState, 'gated' | 'gateCreator' | 'gateUnitPrefix' | 'prizeAsset'>,
  asset: { creator: string; unitName: string; id: number; amount: bigint },
): boolean {
  if (!state.gated) return true;
  if (asset.amount <= 0n) return false;
  if (asset.creator !== state.gateCreator) return false;
  if (BigInt(asset.id) === state.prizeAsset && state.prizeAsset !== 0n) return false;
  if (state.gateUnitPrefix.length === 0) return true;
  return asset.unitName.startsWith(state.gateUnitPrefix);
}

/** Beacon window from the Python contract: resolve until commit_round + 1000. */
export const BEACON_WINDOW = 1_000n;
export const BEACON_DELAY = 8n;

export function resolveOpen(state: RainState, round: bigint): boolean {
  return state.drawOpen && round > state.commitRound && round <= state.commitRound + BEACON_WINDOW;
}

export function abandonOpen(state: RainState, round: bigint): boolean {
  return state.drawOpen && round > state.commitRound + BEACON_WINDOW;
}
