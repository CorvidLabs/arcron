/**
 * The registry as a keeper sees it: what is worth doing, and what it pays.
 *
 * All of this is derived from box state, which any algod will serve for free —
 * the console needs no backend and no indexer, and that property is worth
 * protecting. Even "how much has been paid to keepers" falls out of
 * `times_executed × fee_per_execution` without any transaction history.
 *
 * The one thing that does *not*: which keeper earned it. Per-keeper
 * attribution is not stored on-chain, which is why the leaderboard is a
 * separate decision (see docs/archon.md).
 */

import { EXECUTE_FEE, executionsRemaining, type Upkeep } from './upkeep';

export type Availability = 'due' | 'scheduled' | 'dormant';

export type SortKey = 'reward' | 'overdue' | 'runway' | 'cadence' | 'id';

export interface BoardEntry {
  readonly upkeep: Upkeep;
  readonly availability: Availability;
  /** Rounds past its due round; 0 when not yet due. */
  readonly overdueRounds: bigint;
  /** What a keeper clears after the 3,000 µALGO it spends executing. */
  readonly netReward: bigint;
  readonly runsRemaining: bigint;
  /** The round it last ran, or null if it never has. */
  readonly lastExecutionRound: bigint | null;
}

export interface BoardStats {
  readonly upkeeps: number;
  readonly due: number;
  readonly dormant: number;
  readonly totalExecutions: bigint;
  /** Σ times_executed × fee — what keepers have earned from this app. */
  readonly paidToKeepers: bigint;
  readonly escrowed: bigint;
  /** Median rounds overdue across upkeeps that are due; 0n when none are. */
  readonly medianLateness: bigint;
}

export function classify(upkeep: Upkeep, currentRound: bigint): Availability {
  // Dormant first: an upkeep that cannot pay its fee is nobody's work, however
  // overdue it looks.
  if (upkeep.balance < upkeep.feePerExecution) return 'dormant';
  return currentRound >= upkeep.nextExecutionRound ? 'due' : 'scheduled';
}

export function toEntry(upkeep: Upkeep, currentRound: bigint): BoardEntry {
  const overdue = currentRound - upkeep.nextExecutionRound;
  return {
    upkeep,
    availability: classify(upkeep, currentRound),
    overdueRounds: overdue > 0n ? overdue : 0n,
    // A keeper pays the outer fee plus the pooled extra out of its own pocket.
    netReward: upkeep.feePerExecution - BigInt(EXECUTE_FEE),
    runsRemaining: executionsRemaining(upkeep),
    lastExecutionRound:
      upkeep.timesExecuted > 0n ? upkeep.nextExecutionRound - upkeep.intervalRounds : null,
  };
}

/** Descending for everything a keeper wants most of; ascending for cadence. */
export function sortEntries(entries: readonly BoardEntry[], key: SortKey): BoardEntry[] {
  const sorted = [...entries];
  const compare: Record<SortKey, (a: BoardEntry, b: BoardEntry) => number> = {
    reward: (a, b) => bigintCompare(b.netReward, a.netReward),
    overdue: (a, b) => bigintCompare(b.overdueRounds, a.overdueRounds),
    runway: (a, b) => bigintCompare(a.runsRemaining, b.runsRemaining),
    cadence: (a, b) => bigintCompare(a.upkeep.intervalRounds, b.upkeep.intervalRounds),
    id: (a, b) => bigintCompare(a.upkeep.id, b.upkeep.id),
  };
  // Ties keep a stable, meaningful order rather than an arbitrary one.
  return sorted.sort((a, b) => compare[key](a, b) || bigintCompare(a.upkeep.id, b.upkeep.id));
}

export function summarise(entries: readonly BoardEntry[]): BoardStats {
  const lateness = entries
    .filter((entry) => entry.availability === 'due')
    .map((entry) => entry.overdueRounds)
    .sort(bigintCompare);

  return {
    upkeeps: entries.length,
    due: entries.filter((entry) => entry.availability === 'due').length,
    dormant: entries.filter((entry) => entry.availability === 'dormant').length,
    totalExecutions: entries.reduce((total, entry) => total + entry.upkeep.timesExecuted, 0n),
    paidToKeepers: entries.reduce(
      (total, entry) => total + entry.upkeep.timesExecuted * entry.upkeep.feePerExecution,
      0n,
    ),
    escrowed: entries.reduce((total, entry) => total + entry.upkeep.balance, 0n),
    medianLateness: median(lateness),
  };
}

function median(sorted: readonly bigint[]): bigint {
  if (sorted.length === 0) return 0n;
  const middle = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[middle];
  // Even count: the lower of the two middles, so the figure is always one an
  // upkeep actually has rather than an average of two that do not.
  return sorted[middle - 1];
}

function bigintCompare(left: bigint, right: bigint): number {
  if (left < right) return -1;
  return left > right ? 1 : 0;
}
