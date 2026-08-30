/**
 * Rain hub: anyone opens a rain, Arcron fires the ones that are due.
 *
 * Each rain is a box. `draw` takes a configured slice of that rain's pot
 * and credits whoever that mode says — everyone who entered, one random
 * ticket, or the people who checked in. Holders pull `claim`. Cadence is
 * both the Arcron upkeep and `interval_rounds` on each rain.
 */

import algosdk from 'algosdk';

import { algos, tokens } from './format';
import type { NetworkKey } from './networks';

export const RAIN_PREFIX = 0x72;
export const TICKET_PREFIX = 0x74;
export const INDEX_PREFIX = 0x6e;

export const RAIN_BOX_BYTES = 224;
export const TICKET_BOX_BYTES = 24;
export const RAIN_BOX_MBR = 2_500 + 400 * 233;
export const TICKET_MBR = 2_500 + 400 * 65;
export const INDEX_MBR = 2_500 + 400 * 49;
export const APP_BASE_MBR = 100_000;

export const SPLIT = 0n;
export const ONE = 1n;
export const WAVE = 2n;

export const DRAW_SCAN = 4;
export const COMMIT_DELAY = 8;
export const SEED_WINDOW = 800;

export const CLAIM_FEE = 2_000;
export const ENTER_FEE = 2_000;
export const GM_FEE = 2_000;
export const DEPOSIT_FEE = 2_000;
export const CREATE_FEE = 2_000;
export const RESOLVE_FEE = 2_000;
export const OPT_IN_FEE = 2_000;

export const ZERO_ADDRESS = algosdk.ALGORAND_ZERO_ADDRESS_STRING;

/** TestNet Corvid NFT minter. Gate on this for Corvid holder rains. */
export const CORVID_TESTNET_MINTER = 'WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A';
export const CORVID_UNIT_PREFIX = 'corvid';
/** First numbered TestNet Nevermore (`Corvid #0001`). The gate is the minter, not this id. */
export const CORVID_TESTNET_NFT = 746_557_513;
/** On-chain name of `CORVID_TESTNET_NFT`. */
export const CORVID_TESTNET_NFT_NAME = 'Corvid #0001';

export const CADENCES = [
  { label: 'hourly', rounds: 1_286 },
  { label: 'daily', rounds: 30_857 },
  { label: 'weekly', rounds: 216_000 },
  { label: 'monthly', rounds: 925_714 },
] as const;

export interface RainDeployment {
  readonly appId: number;
  readonly upkeepId: number;
  readonly keeperAppId: number;
  readonly gateUnitPrefix: string;
}

/** Live TestNet hub. Zero until this tree deploys one. */
export const TESTNET_RAIN: RainDeployment = {
  appId: 770_130_162,
  upkeepId: 91,
  keeperAppId: 769_891_898,
  gateUnitPrefix: CORVID_UNIT_PREFIX,
};

export function rainFor(network: NetworkKey): RainDeployment | null {
  if (network !== 'testnet') return null;
  if (TESTNET_RAIN.appId === 0) return null;
  return TESTNET_RAIN;
}

export interface RainHubState {
  readonly appId: number;
  readonly nextRainId: bigint;
  readonly cursor: bigint;
  readonly bootstrapped: boolean;
}

export interface RainRec {
  readonly id: bigint;
  readonly creator: string;
  readonly gateCreator: string;
  readonly label: string;
  readonly prizeAsset: bigint;
  readonly drip: bigint;
  readonly intervalRounds: bigint;
  readonly lastRainRound: bigint;
  readonly pot: bigint;
  readonly tickets: bigint;
  readonly drawId: bigint;
  readonly cumulative: bigint;
  readonly mode: bigint;
  readonly waveCap: bigint;
  readonly waveCount: bigint;
  readonly lastShare: bigint;
  readonly lastWaveId: bigint;
  readonly waveUnclaimed: bigint;
  readonly commitRound: bigint;
  readonly prizeLocked: bigint;
  readonly gated: boolean;
}

export interface Ticket {
  readonly credit: bigint;
  readonly waveId: bigint;
  readonly settledId: bigint;
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

export interface GlobalEntry {
  readonly key: Uint8Array;
  readonly value: {
    readonly bytes?: Uint8Array | string;
    readonly uint?: number | bigint;
    readonly type?: number;
  };
}

export function decodeHubState(appId: number, entries: readonly GlobalEntry[]): RainHubState {
  const ints = new Map<string, bigint>();
  for (const entry of entries) {
    ints.set(textKey(entry.key), asUint(entry.value));
  }
  return {
    appId,
    nextRainId: ints.get('next_rain_id') ?? 0n,
    cursor: ints.get('cursor') ?? 0n,
    bootstrapped: (ints.get('bootstrapped') ?? 0n) === 1n,
  };
}

/** Decode a single-rain global layout, used only if a hub is not present. */
export function decodeRainState(appId: number, entries: readonly GlobalEntry[]): RainRec {
  const ints = new Map<string, bigint>();
  const blobs = new Map<string, Uint8Array>();
  for (const entry of entries) {
    const name = textKey(entry.key);
    if (name === 'gate_creator') blobs.set(name, asBytes(entry.value.bytes));
    else ints.set(name, asUint(entry.value));
  }
  const gateCreator = addressFrom(blobs.get('gate_creator') ?? new Uint8Array(32));
  return {
    id: 0n,
    creator: ZERO_ADDRESS,
    gateCreator,
    label: 'Corvid daily',
    prizeAsset: ints.get('prize_asset') ?? 0n,
    drip: ints.get('drip') ?? 0n,
    intervalRounds: ints.get('interval_rounds') ?? 0n,
    lastRainRound: ints.get('last_rain_round') ?? 0n,
    pot: ints.get('pot') ?? 0n,
    tickets: ints.get('tickets') ?? 0n,
    drawId: ints.get('draw_id') ?? 0n,
    cumulative: ints.get('cumulative') ?? 0n,
    mode: SPLIT,
    waveCap: 0n,
    waveCount: 0n,
    lastShare: 0n,
    lastWaveId: 0n,
    waveUnclaimed: 0n,
    commitRound: 0n,
    prizeLocked: 0n,
    gated: gateCreator !== ZERO_ADDRESS,
  };
}

function addressFrom(bytes: Uint8Array): string {
  if (bytes.length !== 32) return ZERO_ADDRESS;
  return algosdk.encodeAddress(bytes);
}

export function encodeLabel(text: string): Uint8Array {
  const raw = new TextEncoder().encode(text).slice(0, 32);
  const out = new Uint8Array(32);
  out.set(raw);
  return out;
}

export function decodeLabel(bytes: Uint8Array): string {
  let end = bytes.length;
  while (end > 0 && bytes[end - 1] === 0) end -= 1;
  if (end === 0) return '';
  return new TextDecoder().decode(bytes.subarray(0, end));
}

export function rainBoxName(id: bigint | number): Uint8Array {
  const name = new Uint8Array(9);
  name[0] = RAIN_PREFIX;
  new DataView(name.buffer).setBigUint64(1, BigInt(id));
  return name;
}

export function rainIdFromBoxName(name: Uint8Array): bigint | null {
  if (name.length !== 9 || name[0] !== RAIN_PREFIX) return null;
  return new DataView(name.buffer, name.byteOffset, name.byteLength).getBigUint64(1);
}

export function ticketBoxName(rainId: bigint | number, address: string): Uint8Array {
  const decoded = algosdk.decodeAddress(address).publicKey;
  const name = new Uint8Array(1 + 8 + decoded.length);
  name[0] = TICKET_PREFIX;
  new DataView(name.buffer).setBigUint64(1, BigInt(rainId));
  name.set(decoded, 9);
  return name;
}

/** Rain id encoded in a ticket box, if that box belongs to `address`. */
export function ticketRainIdForHolder(name: Uint8Array, address: string): bigint | null {
  if (name.length !== 41 || name[0] !== TICKET_PREFIX) return null;
  let publicKey: Uint8Array;
  try {
    publicKey = algosdk.decodeAddress(address).publicKey;
  } catch {
    return null;
  }
  for (let index = 0; index < 32; index += 1) {
    if (name[9 + index] !== publicKey[index]) return null;
  }
  return new DataView(name.buffer, name.byteOffset, name.byteLength).getBigUint64(1);
}

export function indexBoxName(rainId: bigint | number, index: bigint | number): Uint8Array {
  const name = new Uint8Array(17);
  name[0] = INDEX_PREFIX;
  const view = new DataView(name.buffer);
  view.setBigUint64(1, BigInt(rainId));
  view.setBigUint64(9, BigInt(index));
  return name;
}

export function sameBoxName(left: Uint8Array, right: Uint8Array): boolean {
  if (left.length !== right.length) return false;
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) return false;
  }
  return true;
}

export function decodeRainRec(id: bigint, raw: Uint8Array): RainRec {
  if (raw.length < RAIN_BOX_BYTES) {
    throw new Error(`Rain box ${id} is ${raw.length} bytes, too short to decode`);
  }
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  const uint = (offset: number): bigint => view.getBigUint64(offset);
  const gateCreator = addressFrom(raw.subarray(32, 64));
  return {
    id,
    creator: addressFrom(raw.subarray(0, 32)),
    gateCreator,
    label: decodeLabel(raw.subarray(64, 96)),
    prizeAsset: uint(96),
    drip: uint(104),
    intervalRounds: uint(112),
    lastRainRound: uint(120),
    pot: uint(128),
    tickets: uint(136),
    drawId: uint(144),
    cumulative: uint(152),
    mode: uint(160),
    waveCap: uint(168),
    waveCount: uint(176),
    lastShare: uint(184),
    lastWaveId: uint(192),
    waveUnclaimed: uint(200),
    commitRound: uint(208),
    prizeLocked: uint(216),
    gated: gateCreator !== ZERO_ADDRESS,
  };
}

export function encodeRainRec(rain: Omit<RainRec, 'gated'>): Uint8Array {
  const raw = new Uint8Array(RAIN_BOX_BYTES);
  const view = new DataView(raw.buffer);
  raw.set(algosdk.decodeAddress(rain.creator).publicKey, 0);
  raw.set(algosdk.decodeAddress(rain.gateCreator).publicKey, 32);
  raw.set(encodeLabel(rain.label), 64);
  const fields: bigint[] = [
    rain.prizeAsset,
    rain.drip,
    rain.intervalRounds,
    rain.lastRainRound,
    rain.pot,
    rain.tickets,
    rain.drawId,
    rain.cumulative,
    rain.mode,
    rain.waveCap,
    rain.waveCount,
    rain.lastShare,
    rain.lastWaveId,
    rain.waveUnclaimed,
    rain.commitRound,
    rain.prizeLocked,
  ];
  fields.forEach((value, index) => {
    view.setBigUint64(96 + index * 8, value);
  });
  return raw;
}

export function decodeTicket(raw: Uint8Array): Ticket {
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  if (raw.length < 8) return { credit: 0n, waveId: 0n, settledId: 0n };
  return {
    credit: view.getBigUint64(0),
    waveId: raw.length >= 16 ? view.getBigUint64(8) : 0n,
    settledId: raw.length >= 24 ? view.getBigUint64(16) : 0n,
  };
}

export function qualifies(
  rain: Pick<RainRec, 'gated' | 'gateCreator' | 'prizeAsset'>,
  asset: { creator: string; unitName: string; id: number; amount: bigint },
  unitPrefix = '',
): boolean {
  if (!rain.gated) return true;
  if (asset.amount <= 0n) return false;
  if (asset.creator !== rain.gateCreator) return false;
  if (BigInt(asset.id) === rain.prizeAsset && rain.prizeAsset !== 0n) return false;
  if (unitPrefix.length === 0) return true;
  return asset.unitName.startsWith(unitPrefix);
}

export function enterMbr(mode: bigint): number {
  return mode === ONE ? TICKET_MBR + INDEX_MBR : TICKET_MBR;
}

export function rainsRemaining(rain: Pick<RainRec, 'pot' | 'drip' | 'tickets' | 'mode' | 'waveCount'>): bigint {
  if (rain.drip <= 0n) return 0n;
  if (rain.mode === WAVE) {
    const count = rain.waveCount > 0n ? rain.waveCount : 1n;
    const share = rain.drip / count;
    if (share <= 0n) return 0n;
    return rain.pot / (share * count);
  }
  if (rain.tickets <= 0n) return 0n;
  if (rain.mode === ONE) return rain.pot / rain.drip;
  const share = rain.drip / rain.tickets;
  if (share <= 0n) return 0n;
  return rain.pot / (share * rain.tickets);
}

export function roundsUntilRain(
  rain: Pick<RainRec, 'lastRainRound' | 'intervalRounds'>,
  round: bigint,
): bigint {
  if (rain.lastRainRound === 0n) return 0n;
  return rain.lastRainRound + rain.intervalRounds - round;
}

export function allocationOf(rain: RainRec, ticket: Ticket | null): bigint {
  if (ticket === null) return 0n;
  if (rain.mode === SPLIT) {
    return rain.cumulative > ticket.credit ? rain.cumulative - ticket.credit : 0n;
  }
  let owed = ticket.credit;
  if (
    rain.mode === WAVE &&
    ticket.waveId === rain.lastWaveId &&
    ticket.settledId !== rain.lastWaveId
  ) {
    owed += rain.lastShare;
  }
  return owed;
}

export function modeLabel(mode: bigint): string {
  if (mode === ONE) return 'One person';
  if (mode === WAVE) return 'Who shows up';
  return 'Everyone';
}

/**
 * A pot amount a person can read. ALGO scales by its fixed six decimals; an
 * ASA scales by its own. Until the asset lookup has said how many that is,
 * the raw number is labelled "base units" rather than shown as whole tokens:
 * a 6-decimal pot printed unscaled overstates itself a millionfold, and the
 * hub is permissionless, so that inflated number would be a lure.
 */
export function prizeLabel(
  amount: bigint,
  prizeAsset: bigint,
  unitName = '',
  decimals: number | null = null,
): string {
  if (prizeAsset === 0n) return algos(amount);
  const unit = unitName.trim() || 'ASA';
  if (decimals === null) return `${amount.toLocaleString('en-US')} base units of ${unit}`;
  return `${tokens(amount, decimals)} ${unit}`;
}

/** The prize ASA id from the rain box, even before algod has named it. */
export function prizeAssetId(rain: Pick<RainRec, 'prizeAsset'>): string | null {
  if (rain.prizeAsset === 0n) return null;
  return rain.prizeAsset.toString();
}

/**
 * Why a rain is `waiting` rather than due. Interval can already have passed
 * while the pot is empty or nobody has a ticket; calling that overdue is a lie.
 */
export function waitingReason(
  rain: Pick<RainRec, 'mode' | 'tickets' | 'waveCount' | 'pot' | 'drip'>,
): string | null {
  if (rain.mode === WAVE && rain.waveCount === 0n) return 'nobody checked in';
  if (rain.mode !== WAVE && rain.tickets === 0n) return 'no tickets yet';
  if (rain.pot < rain.drip) return 'pot is empty';
  return null;
}

export type RainStanding = 'due' | 'scheduled' | 'waiting';

export function rainStanding(
  rain: Pick<RainRec, 'mode' | 'tickets' | 'waveCount' | 'pot' | 'drip' | 'lastRainRound' | 'intervalRounds'>,
  round: bigint,
): RainStanding {
  const ready =
    rain.mode === WAVE
      ? rain.waveCount > 0n && rain.pot >= rain.drip
      : rain.tickets > 0n && rain.pot >= rain.drip;
  if (!ready) return 'waiting';
  if (roundsUntilRain(rain, round) <= 0n) return 'due';
  return 'scheduled';
}

export function modeHint(rain: Pick<RainRec, 'mode' | 'waveCap'>): string {
  if (rain.mode === ONE) return 'One random ticket each fire';
  if (rain.mode === WAVE) {
    const cap = rain.waveCap.toString();
    return `The first ${cap} to check in this drop`;
  }
  return 'Split across everyone who entered';
}

export function isCorvidRain(rain: Pick<RainRec, 'gateCreator'>): boolean {
  return rain.gateCreator === CORVID_TESTNET_MINTER;
}

export function unitPrefixFor(rain: Pick<RainRec, 'gateCreator'>, deployment: RainDeployment | null): string {
  if (deployment !== null && isCorvidRain(rain) && deployment.gateUnitPrefix.length > 0) {
    return deployment.gateUnitPrefix;
  }
  return '';
}
