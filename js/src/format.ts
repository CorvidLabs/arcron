/**
 * Display helpers.
 *
 * ALGO leads everywhere a person reads a value. µALGO is what the contract
 * counts in, but nobody thinks in millionths. Durations are derived from the
 * chain's observed round rate, so "every 1,286 rounds" also reads as "~1 hour".
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
export function intervalLabel(intervalRounds: bigint, secondsPerRound: number | null): string {
  const time = roundsAsTime(intervalRounds, secondsPerRound);
  return time === null ? rounds(intervalRounds) : `${rounds(intervalRounds)} · ~${time}`;
}

/** "due now", "overdue by ~2 min", "in ~1 d 6 h". */
export function dueLabel(untilDue: bigint, secondsPerRound: number | null): string {
  if (untilDue === 0n) return 'due now';
  const time = roundsAsTime(untilDue, secondsPerRound);
  const amount = time === null ? rounds(untilDue) : `~${time}`;
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
  const runs = `${executionsLeft.toLocaleString('en-US')} run${executionsLeft === 1n ? '' : 's'}`;
  return time === null ? runs : `${runs} · ~${time}`;
}
