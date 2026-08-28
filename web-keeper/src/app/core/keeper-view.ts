/**
 * What an operator running a keeper needs to see, derived from public state.
 *
 * The console answers "should I register an upkeep". This answers a different
 * question: "is my keeper working, and is it worth running". Those need
 * different numbers, which is why this is a separate app rather than a page
 * in the console.
 *
 * Everything here is a pure function over values the caller fetched. No algod
 * client, no signals, no Angular. The chain access lives in the service; the
 * arithmetic that decides what an operator is told lives here, where it can be
 * tested without a network.
 *
 * `@corvidlabs/arcron/board` already models the registry as a keeper sees it,
 * including `netReward`, which is the fee less what an execution costs. This
 * adds only what board cannot know: whether *this* keeper is alive, and
 * whether running it is paying for itself.
 */

import type { BoardEntry } from '@corvidlabs/arcron/board';

/** How long a keeper may be silent before silence means something. */
export const SILENT_ROUNDS_WARNING = 1_200n;

/** Beyond this, a keeper is not late, it is off. */
export const SILENT_ROUNDS_STOPPED = 6_000n;

export type Liveness = 'working' | 'quiet' | 'stopped' | 'unknown';

/**
 * Whether the keeper is running, judged only by what it has done on chain.
 *
 * Deliberately not "is the process up". The UI reads the chain and nothing
 * else, so it can only report what a keeper *did*. A bot that is running but
 * winning no races looks identical to one that is switched off, and saying
 * `quiet` rather than `stopped` for the first stretch is what keeps that
 * honest: an idle registry produces the same silence as a dead machine.
 */
export function liveness(
    lastSeenRound: bigint | null,
    currentRound: bigint,
    dueCount: number,
): Liveness {
    if (lastSeenRound === null) return 'unknown';
    const silent = currentRound - lastSeenRound;
    if (silent < SILENT_ROUNDS_WARNING) return 'working';
    // Silence with nothing due is not evidence of anything. A keeper on an
    // empty registry has nothing to win, and calling that "stopped" would cry
    // wolf on every quiet night.
    if (dueCount === 0) return 'quiet';
    return silent >= SILENT_ROUNDS_STOPPED ? 'stopped' : 'quiet';
}

export function livenessReason(state: Liveness, dueCount: number): string {
    switch (state) {
        case 'working':
            return 'Executed recently.';
        case 'quiet':
            return dueCount === 0
                ? 'Nothing has been due, so there has been nothing to win. This is not a fault.'
                : `${dueCount} upkeep(s) are due and this keeper has not taken one. It may be losing races.`;
        case 'stopped':
            return 'Due work has gone unserviced by this keeper for a long time. Check the process.';
        case 'unknown':
            return 'This address has never executed here, so there is nothing to judge yet.';
    }
}

/** What the registry is worth to a keeper right now, in microAlgos. */
export function claimableNow(entries: readonly BoardEntry[]): bigint {
    return entries
        .filter((entry) => entry.availability === 'due')
        .reduce((total, entry) => total + entry.netReward, 0n);
}

/**
 * The best thing to execute next, or null when nothing is due.
 *
 * Highest net reward, then longest overdue. Reward first because that is what
 * an operator is optimising, and overdue as the tie-break because a fee that
 * has escalated will keep escalating.
 */
export function nextBest(entries: readonly BoardEntry[]): BoardEntry | null {
    const due = entries.filter((entry) => entry.availability === 'due');
    if (due.length === 0) return null;
    return due.reduce((best, entry) => {
        if (entry.netReward !== best.netReward) {
            return entry.netReward > best.netReward ? entry : best;
        }
        return entry.overdueRounds > best.overdueRounds ? entry : best;
    });
}

/**
 * Whether the registry currently pays for a keeper at all.
 *
 * `netReward` is already net of the execution cost, so anything at or below
 * zero is work that costs more than it pays. An operator deciding whether to
 * keep a machine running needs this separated from the headline reward, which
 * counts only what is due this instant and so flatters a quiet registry.
 */
export function unprofitable(entries: readonly BoardEntry[]): BoardEntry[] {
    return entries.filter((entry) => entry.netReward <= 0n);
}

/** Rounds to a human span, at a block time the caller measured. */
export function humanRounds(rounds: bigint, secondsPerRound: number): string {
    const seconds = Number(rounds) * secondsPerRound;
    if (seconds < 90) return `${Math.round(seconds)}s`;
    if (seconds < 5_400) return `${Math.round(seconds / 60)}m`;
    if (seconds < 172_800) return `${(seconds / 3_600).toFixed(1)}h`;
    return `${(seconds / 86_400).toFixed(1)}d`;
}
