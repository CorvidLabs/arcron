/**
 * The register form's Test button: simulate the call Arcron will make, before
 * a creator escrows anything.
 *
 * The recipe below is not the obvious one, and every part of it was measured
 * rather than reasoned about. `scripts/spike_simulate_test_button.py` drove a
 * fixed set of targets (`smart_contracts/sim_probe/`) through both a standalone
 * simulate and a real `execute()` on LocalNet, and found three ways the two
 * disagree. Each is closed here:
 *
 *   * **Simulate the inner call, never the outer `execute()`.** `execute`
 *     needs the upkeep's box, and before registration there is no box, so the
 *     outer call fails on the box under every combination of simulate flags.
 *     There is nothing to learn from it.
 *   * **The sender is the keeper application's own account.** A real execution
 *     arrives at the target as an inner call from that address, which is what
 *     `docs/integrating.md` tells every integrator to check. Testing as the
 *     connected user would give a confident wrong answer. That account has no
 *     private key, so `allowEmptySignatures` is what makes it sendable at all,
 *     and `allowUnnamedResources` is what makes the recommended check pass:
 *     looking up `Application(keeper_app).address` costs a reference that a
 *     real execution gets free (as the top-level call's own ApplicationID) and
 *     a standalone simulated call has no equivalent of.
 *   * **`extraOpcodeBudget` stays at zero, always.** Raising it makes a target
 *     that exhausts the budget pass here and fail on chain. The spike measured
 *     exactly that: `burns_budget` fails for real, fails honestly at 0, and
 *     passes at 17,000. There is no reason to touch this field.
 *
 * What the recipe still cannot do is certify that a target's resource needs
 * are *servable*. A standalone simulate is one transaction with all 8 of the
 * AVM's references to itself, while a real Arcron execution has already spent
 * two of them on the upkeep box and the target app. A target needing seven
 * references passes a naive simulate and is then permanently unexecutable,
 * after the creator has escrowed. So this grades resource use against the six
 * that are actually left, instead of returning a flat pass. See
 * `gradeReferences`.
 */

import algosdk from 'algosdk';

import { foldUnnamedResources, type ResourceRefs } from './keeper-txns';
import { upkeepBoxName } from './upkeep';

/** References one Algorand transaction may carry, of any kind. */
export const REFERENCE_BUDGET = 8;

/**
 * References Arcron spends on every execution before the target sees any: the
 * upkeep's own box, and the target app itself. Measured in
 * `scripts/spike_resources.py`; documented in `docs/arcron.md`.
 */
export const ARCRON_REFERENCES = 2;

/** What is left for the target, and everything it reaches, to share. */
export const REFERENCES_FOR_TARGET = REFERENCE_BUDGET - ARCRON_REFERENCES;

/**
 * How many references the keeper bot in this repository attaches.
 *
 * This was 4 until 2026-08-26, because `scripts/keeper_bot.py` sent through
 * algokit-utils' typed client and its resource populator caps at four direct
 * account references, refusing a fifth with "No more transactions below
 * reference limit". The AVM takes six once Arcron's own box and target app are
 * paid for, so five and six were protocol-legal and not serviced by the bot a
 * third-party keeper was most likely to have copied.
 *
 * `keeper_bot.py` now resolves and attaches its references directly and turns
 * the populator off, so the reference keeper matches the protocol and the
 * middle grade collapses on its own. `scripts/reference_boundary.py` asserts
 * both halves on every run of the `local` lane: six is serviced, seven is
 * refused by the AVM.
 */
export const REFERENCE_KEEPER_REFERENCES = 6;

/** Where a creator is sent when a target cannot be serviced at all. */
export const PULL_PATTERN_URL =
  'https://github.com/CorvidLabs/arcron/blob/main/docs/integrating.md#the-pull-pattern';

export type ReferenceGradeKey = 'none' | 'servable' | 'protocol-only' | 'unexecutable';

export interface ReferenceGrade {
  readonly key: ReferenceGradeKey;
  /** References a real execution would have to carry beyond Arcron's own two. */
  readonly count: number;
  readonly headline: string;
  readonly detail: string;
}

/**
 * How a call's resource needs read against what a keeper can actually supply.
 *
 * Four bands, and the boundaries are the measured ones rather than round
 * numbers: 0 needs nothing of any keeper; up to 4 is what the reference bot
 * attaches; 5 and 6 fit the AVM and not that bot; above 6 does not fit the AVM
 * at all once Arcron's two references are counted, so no keeper can ever run
 * it and the answer is to stop pushing and let the counterparty pull.
 */
export function gradeReferences(count: number): ReferenceGrade {
  if (count <= 0) {
    return {
      key: 'none',
      count: 0,
      headline: 'Needs nothing beyond your app',
      detail:
        'The call reached for no account, asset, app or box that a keeper would have to ' +
        'name for it. Any keeper can service this.',
    };
  }
  if (count <= REFERENCE_KEEPER_REFERENCES) {
    return {
      key: 'servable',
      count,
      headline: `Needs ${references(count)}`,
      detail:
        `A keeper has to attach ${references(count)} for this call to reach what it touched. ` +
        `That is inside the ${REFERENCE_KEEPER_REFERENCES} the keeper bot in this repository ` +
        `attaches today, so a keeper running it should service this.`,
    };
  }
  if (count <= REFERENCES_FOR_TARGET) {
    return {
      key: 'protocol-only',
      count,
      headline: `Needs ${references(count)}, more than the usual keeper attaches`,
      detail:
        `The protocol allows ${REFERENCES_FOR_TARGET}, so this can be executed. The keeper bot ` +
        `in this repository attaches at most ${REFERENCE_KEEPER_REFERENCES} account references ` +
        `and refuses a fifth, and it is the bot a third-party keeper is most likely to have ` +
        `copied. Expect to run a keeper yourself, or to keep this under ` +
        `${REFERENCE_KEEPER_REFERENCES}.`,
    };
  }
  return {
    key: 'unexecutable',
    count,
    headline: `Needs ${references(count)}, which can never execute`,
    detail:
      `One transaction carries ${REFERENCE_BUDGET} references and Arcron has already spent ` +
      `${ARCRON_REFERENCES} of them on the upkeep box and your app, leaving ` +
      `${REFERENCES_FOR_TARGET}. No keeper can supply more than that, so an upkeep registered ` +
      `against this call would escrow money it can never spend. Move the work that reaches ` +
      `those resources into a transaction the interested party sends themselves: the pull ` +
      `pattern in the integration guide.`,
  };
}

function references(count: number): string {
  return `${count} extra reference${count === 1 ? '' : 's'}`;
}

/** How a refusal should be read. Each carries its own remedy in the console. */
export type FailureKind = 'no-such-app' | 'budget' | 'unavailable' | 'rejected';

export interface ReferenceCounts {
  readonly accounts: number;
  readonly apps: number;
  readonly assets: number;
  readonly boxes: number;
}

export interface TargetTestOutcome {
  /**
   * Whether the target accepted the call. Never a claim that a real execution
   * will succeed: see `gradeReferences` for the part this cannot certify.
   */
  readonly accepted: boolean;
  /** What the AVM said, when it said no. */
  readonly failure: string | null;
  readonly failureKind: FailureKind | null;
  /** Present only on acceptance: a failing call's resource trace stops early. */
  readonly grade: ReferenceGrade | null;
  /** The composition behind the grade, so the count can be checked by eye. */
  readonly counts: ReferenceCounts | null;
}

/** The shape this reads out of a simulate response, kept structural so it can be built in a test. */
export interface SimulatedGroup {
  readonly failureMessage?: string;
  readonly unnamedResourcesAccessed?: algosdk.modelsv2.SimulateUnnamedResourcesAccessed;
}

/**
 * Turn one simulated group into a graded outcome.
 *
 * Pure, and exported, so the grading is tested against the code the console
 * runs rather than against a second copy of the same arithmetic.
 */
export function readSimulatedCall(
  group: SimulatedGroup | undefined,
  options: { keeperAppId: number; targetApp: number },
): TargetTestOutcome {
  const failure = group?.failureMessage ?? '';
  if (failure !== '') {
    return {
      accepted: false,
      failure,
      failureKind: classify(failure),
      grade: null,
      counts: null,
    };
  }

  // Seeded with the two references Arcron spends on every execution, so what
  // the fold produces is the reference set a real `execute` would have to
  // carry, and the count above these two is what a keeper still has room for.
  // `foldUnnamedResources` is `execute`'s own folding logic, reused rather
  // than reimplemented: it drops the keeper app (free in a real execution, as
  // the top-level call's own ApplicationID) and the zero address, and it turns
  // holdings and local-state reads into the account/asset and account/app
  // pairs a v1 app call actually has to declare.
  const known: ResourceRefs = {
    appAccounts: [],
    appForeignApps: [options.targetApp],
    appForeignAssets: [],
    boxes: [{ appIndex: 0, name: upkeepBoxName(0n) }],
  };
  const attached = foldUnnamedResources(known, group?.unnamedResourcesAccessed, options.keeperAppId);
  const counts: ReferenceCounts = {
    accounts: attached.appAccounts.length,
    apps: attached.appForeignApps.length,
    assets: attached.appForeignAssets.length,
    boxes: attached.boxes.length,
  };
  const total = counts.accounts + counts.apps + counts.assets + counts.boxes;
  return {
    accepted: true,
    failure: null,
    failureKind: null,
    grade: gradeReferences(total - ARCRON_REFERENCES),
    counts,
  };
}

function classify(failure: string): FailureKind {
  // algod's wording for an app id that is not an application on this chain.
  if (/does not exist/i.test(failure)) return 'no-such-app';
  if (/budget exceeded/i.test(failure)) return 'budget';
  if (/unavailable (App|Account|Asset|Box)/i.test(failure)) return 'unavailable';
  return 'rejected';
}

export interface TargetTestParams {
  /** The keeper app whose account the call will arrive from. */
  readonly keeperAppId: number;
  readonly targetApp: number;
  /** Every app arg of the call, in order; element 0 is the method selector. */
  readonly callArgs: readonly Uint8Array[];
}

/**
 * Simulate the inner call Arcron would make, and grade what comes back.
 *
 * Signs nothing, sends nothing, costs nothing, and needs no wallet: the sender
 * is an application account, which can never sign, and the group is never
 * submitted.
 */
export async function testTarget(
  algod: algosdk.Algodv2,
  params: TargetTestParams,
): Promise<TargetTestOutcome> {
  const suggestedParams = await algod.getTransactionParams().do();
  const txn = algosdk.makeApplicationNoOpTxnFromObject({
    // A real execution arrives from here, and a target following the guide
    // checks for exactly this address.
    sender: algosdk.getApplicationAddress(params.keeperAppId).toString(),
    appIndex: params.targetApp,
    appArgs: params.callArgs.map((arg) => arg),
    // The inner call a keeper sends carries no fee of its own and is paid for
    // by the group. A standalone group has nothing to pool from, and algod
    // rejects it outright ("txgroup with 0.0A fees is less than 1mA"), so this
    // pays the node's own minimum rather than pretending to be free.
    suggestedParams: { ...suggestedParams, flatFee: true, fee: suggestedParams.minFee },
  });

  const composer = new algosdk.AtomicTransactionComposer();
  composer.addTransaction({ txn, signer: algosdk.makeEmptyTransactionSigner() });
  const { simulateResponse } = await composer.simulate(
    algod,
    new algosdk.modelsv2.SimulateRequest({
      txnGroups: [],
      // The keeper app's account has no private key, ever.
      allowEmptySignatures: true,
      // Without this, the sender check `docs/integrating.md` recommends fails
      // with "unavailable App", which is the opposite of the truth.
      allowUnnamedResources: true,
      // Never raise this. See the module docstring.
      extraOpcodeBudget: 0,
    }),
  );
  return readSimulatedCall(simulateResponse.txnGroups[0], params);
}
