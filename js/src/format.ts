/**
 * Display helpers.
 *
 * ALGO leads everywhere a person reads a value. µALGO is what the contract
 * counts in, but nobody thinks in millionths. ASA amounts scale the same way:
 * base units on the wire, whole tokens on the page, by the asset's own
 * decimals. Durations are derived from the chain's observed round rate, so
 * "every 1,286 rounds" also reads as "~1 hour".
 */

const MICRO_ALGO_IN_ALGO = 1_000_000n;
const MAX_DECIMALS = 6;

/** "1.5 ALGO", "0.004 ALGO", "0 ALGO", with trailing zeros trimmed. */
export function algos(microAlgo: bigint, options: { sign?: boolean } = {}): string {
  const negative = microAlgo < 0n;
  const magnitude = negative ? -microAlgo : microAlgo;
  const whole = (magnitude / MICRO_ALGO_IN_ALGO).toLocaleString('en-US');
  const fraction = (magnitude % MICRO_ALGO_IN_ALGO)
    .toString()
    .padStart(MAX_DECIMALS, '0')
    .replace(/0+$/, '');
  const value = fraction.length > 0 ? `${whole}.${fraction}` : whole;
  const prefix = negative ? '−' : options.sign ? '+' : '';
  return `${prefix}${value} ALGO`;
}

/**
 * Base units of an ASA as whole tokens: "1.5", "0.004", "1,000", with
 * trailing zeros trimmed. The unit name is the caller's to append.
 */
export function tokens(baseUnits: bigint, decimals: number): string {
  if (decimals <= 0) return baseUnits.toLocaleString('en-US');
  const scale = 10n ** BigInt(decimals);
  const negative = baseUnits < 0n;
  const magnitude = negative ? -baseUnits : baseUnits;
  const whole = (magnitude / scale).toLocaleString('en-US');
  const fraction = (magnitude % scale)
    .toString()
    .padStart(decimals, '0')
    .replace(/0+$/, '');
  const value = fraction.length > 0 ? `${whole}.${fraction}` : whole;
  return negative ? `−${value}` : value;
}

/**
 * A typed whole-token amount as the ASA's base units: 1.5 of a 6-decimal
 * asset is 1_500_000n. Rounds to the nearest base unit, the same way the
 * ALGO forms round to the nearest µALGO.
 */
export function toBaseUnits(amount: number, decimals: number): bigint {
  return BigInt(Math.round(amount * 10 ** decimals));
}

export function shortAddress(address: string): string {
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

export function rounds(count: bigint): string {
  const magnitude = count < 0n ? -count : count;
  return `${magnitude.toLocaleString('en-US')} round${magnitude === 1n ? '' : 's'}`;
}

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * A duration a person can hold in their head: seconds up to a minute, then
 * minutes, hours, days. One unit of precision, plus a second when it earns
 * its place ("1 d 6 h", not "1 d 6 h 13 min 2 s").
 */
export function duration(seconds: number): string {
  const magnitude = Math.abs(Math.round(seconds));
  if (magnitude < 1) return 'moments';
  if (magnitude < MINUTE) return `${magnitude} s`;
  if (magnitude < HOUR) return split(magnitude, MINUTE, 1, 'min', 's');
  if (magnitude < DAY) return split(magnitude, HOUR, MINUTE, 'h', 'min');
  return split(magnitude, DAY, HOUR, 'd', 'h');
}

/**
 * One unit, plus a second when it earns its place ("1 d 6 h", never
 * "1 d 6 h 13 min"). The remainder is rounded, then carried, so 23.9 hours
 * reads as "1 d" rather than the misleading "23 h".
 */
function split(seconds: number, unit: number, subUnit: number, name: string, subName: string): string {
  let whole = Math.floor(seconds / unit);
  let rest = Math.round((seconds % unit) / subUnit);
  if (rest >= unit / subUnit) {
    whole += 1;
    rest = 0;
  }
  const showRest = rest > 0 && whole < 10 && (subUnit > 1 || rest >= 5);
  return showRest ? `${whole} ${name} ${rest} ${subName}` : `${whole} ${name}`;
}

/** Rounds as time, when we know how fast the chain is moving. */
export function roundsAsTime(count: bigint, secondsPerRound: number | null): string | null {
  if (secondsPerRound === null || secondsPerRound <= 0) return null;
  const magnitude = count < 0n ? -count : count;
  return duration(Number(magnitude) * secondsPerRound);
}

/** "every 10 rounds · ~28 s": the round count leads, time explains it. */
/**
 * Keep a phrase together, so a value only ever wraps where it means something.
 *
 * These labels are "A \u00b7 B" compounds, and as one plain string they wrap
 * wherever the box runs out: a registry card on a phone rendered a cadence as
 * "214 rounds \u00b7 ~8" on one line and "min 55 s" on the next, splitting a
 * duration between its number and its unit. Replacing the spaces *inside* each
 * half with non-breaking ones leaves the separator as the only break
 * opportunity, so a value that must wrap does it between the two facts rather
 * than through one of them.
 *
 * The mono font renders U+00A0 at the same advance as a space, so nothing moves
 * when a value does fit.
 */
function unbreakable(phrase: string): string {
  return phrase.replace(/ /g, '\u00a0');
}

export function intervalLabel(intervalRounds: bigint, secondsPerRound: number | null): string {
  const time = roundsAsTime(intervalRounds, secondsPerRound);
  const count = unbreakable(rounds(intervalRounds));
  return time === null ? count : `${count} · ${unbreakable(`~${time}`)}`;
}

/** "due now", "overdue by ~2 min", "in ~1 d 6 h". */
export function dueLabel(untilDue: bigint, secondsPerRound: number | null): string {
  if (untilDue === 0n) return 'due now';
  const time = roundsAsTime(untilDue, secondsPerRound);
  const amount = unbreakable(time === null ? rounds(untilDue) : `~${time}`);
  return untilDue < 0n ? `overdue by ${amount}` : `in ${amount}`;
}

/** How long an escrow lasts at one execution per interval. */
export function runwayLabel(
  executionsLeft: bigint,
  intervalRounds: bigint,
  secondsPerRound: number | null,
): string {
  if (executionsLeft === 0n) return 'empty';
  const time = roundsAsTime(executionsLeft * intervalRounds, secondsPerRound);
  const runs = unbreakable(`${executionsLeft.toLocaleString('en-US')} run${executionsLeft === 1n ? '' : 's'}`);
  return time === null ? runs : `${runs} · ${unbreakable(`~${time}`)}`;
}
