/**
 * The Test button's honesty, pinned.
 *
 * `testTarget` itself is a network round trip and is proved against a real
 * chain (`scripts/spike_simulate_test_button.py` on LocalNet, and the live
 * TestNet keeper for the recipe). What is worth pinning here is the part with
 * no network in it: the grading, and the reference accounting behind it.
 *
 * Every test below imports the code the console actually runs. That matters
 * more than usual here: this repository has four times shipped a test that
 * declared its own copy of the predicate and therefore stayed green with the
 * predicate deleted. Break `gradeReferences`' boundaries or delete the
 * `- ARCRON_REFERENCES` in `readSimulatedCall` and these fail.
 */

import { describe, expect, test } from 'bun:test';
import algosdk from 'algosdk';

import {
  ARCRON_REFERENCES,
  gradeReferences,
  readSimulatedCall,
  REFERENCE_BUDGET,
  REFERENCE_KEEPER_REFERENCES,
  REFERENCES_FOR_TARGET,
  type SimulatedGroup,
} from '../src/target-test';

const KEEPER_APP = 769_891_898;
const TARGET_APP = 769_891_902;

const ACCOUNTS = Array.from({ length: 8 }, () => algosdk.generateAccount().addr.toString());

function accepted(unnamed?: algosdk.modelsv2.SimulateUnnamedResourcesAccessed) {
  const group: SimulatedGroup = { failureMessage: '', unnamedResourcesAccessed: unnamed };
  return readSimulatedCall(group, { keeperAppId: KEEPER_APP, targetApp: TARGET_APP });
}

function refused(failureMessage: string) {
  return readSimulatedCall({ failureMessage }, { keeperAppId: KEEPER_APP, targetApp: TARGET_APP });
}

describe('the reference budget the grades are drawn against', () => {
  test("Arcron leaves six of the AVM's eight for the target", () => {
    // Measured in scripts/spike_resources.py and recorded in docs/arcron.md.
    // The whole point of the grading is that a standalone simulate does not
    // pay these two, so if this arithmetic drifts the button starts lying.
    expect(REFERENCE_BUDGET).toBe(8);
    expect(ARCRON_REFERENCES).toBe(2);
    expect(REFERENCES_FOR_TARGET).toBe(6);
  });
});

describe('gradeReferences', () => {
  test('nothing discovered is servable by any keeper', () => {
    const grade = gradeReferences(0);
    expect(grade.key).toBe('none');
    expect(grade.count).toBe(0);
  });

  test('everything up to the reference keeper cap is servable today', () => {
    for (let count = 1; count <= REFERENCE_KEEPER_REFERENCES; count += 1) {
      expect(gradeReferences(count).key).toBe('servable');
    }
  });

  test('the reference keeper now services everything the protocol allows', () => {
    // The `protocol-only` grade existed because scripts/keeper_bot.py sent
    // through algokit-utils' populator, which caps at four account references.
    // It now attaches them directly, so there is no longer a band that the
    // protocol permits and the reference keeper refuses. The grade is kept in
    // the type because a third-party keeper built on a bare `send.execute`
    // still has the cap, and the band returns the moment these two numbers
    // differ again.
    expect(REFERENCE_KEEPER_REFERENCES).toBe(REFERENCES_FOR_TARGET);
    expect(gradeReferences(REFERENCES_FOR_TARGET).key).toBe('servable');
  });

  test('the grade band reappears if the keeper ever falls behind the protocol', () => {
    // Guards the collapse above: if someone lowers the keeper's cap without
    // restoring the wording, this fails rather than silently grading a
    // six-reference target as servable by a bot that will refuse it.
    const grade = gradeReferences(REFERENCES_FOR_TARGET + 1);
    expect(grade.key).toBe('unexecutable');
    expect(grade.detail).toContain(String(REFERENCES_FOR_TARGET));
  });

  test('one past the protocol can never execute, and says so', () => {
    const grade = gradeReferences(REFERENCES_FOR_TARGET + 1);
    expect(grade.key).toBe('unexecutable');
    expect(grade.detail).toContain('pull pattern');
  });

  test('the seven-reference target the spike measured is the unexecutable one', () => {
    // sim_probe's `needs_seven` passes a naive standalone simulate and is
    // rejected by a real execute() with "9 references requested, 8 available".
    // That is the exact case this button exists to catch.
    expect(gradeReferences(7).key).toBe('unexecutable');
  });
});

describe('readSimulatedCall: what a passing simulation is graded on', () => {
  test("Arcron's own two references are not charged to the target", () => {
    // No unnamed resources at all: the reference set is the upkeep box and the
    // target app, which Arcron always pays for. Grading those against the
    // target would report every trivial hook as needing two.
    const outcome = accepted(undefined);
    expect(outcome.accepted).toBe(true);
    expect(outcome.grade?.count).toBe(0);
    expect(outcome.grade?.key).toBe('none');
    expect(outcome.counts).toEqual({ accounts: 0, apps: 1, assets: 0, boxes: 1 });
  });

  test('the keeper app itself is free, so the recommended sender check costs nothing', () => {
    // docs/integrating.md tells every integrator to assert
    // `Txn.sender == Application(keeper_app).address`. Looking that up costs a
    // reference under a standalone simulate and nothing in a real execution,
    // where the keeper app is the top-level call's own ApplicationID. Counting
    // it would grade the guide's own recommended pattern as a cost.
    const outcome = accepted(
      new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({ apps: [KEEPER_APP] }),
    );
    expect(outcome.grade?.count).toBe(0);
  });

  test('a discovered account is one reference', () => {
    const outcome = accepted(
      new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({ accounts: [ACCOUNTS[0]] }),
    );
    expect(outcome.grade?.count).toBe(1);
    expect(outcome.grade?.key).toBe('servable');
    expect(outcome.counts?.accounts).toBe(1);
  });

  test('four accounts is well inside what the reference keeper attaches', () => {
    const outcome = accepted(
      new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({ accounts: ACCOUNTS.slice(0, 4) }),
    );
    expect(outcome.grade?.count).toBe(4);
    expect(outcome.grade?.key).toBe('servable');
  });

  test('six accounts is the most a call can touch, and is serviced', () => {
    // scripts/reference_boundary.py asserts this same boundary on a chain,
    // through the real bot: six is executed, seven is refused by the AVM.
    const outcome = accepted(
      new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({ accounts: ACCOUNTS.slice(0, 6) }),
    );
    expect(outcome.grade?.count).toBe(6);
    expect(outcome.grade?.key).toBe('servable');
  });

  test('seven accounts is graded unexecutable rather than passed', () => {
    // The naive Test button returns a flat PASS here, the creator escrows, and
    // the upkeep is then permanently unexecutable. This is the bug the whole
    // grading exists to prevent.
    const outcome = accepted(
      new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({ accounts: ACCOUNTS.slice(0, 7) }),
    );
    expect(outcome.accepted).toBe(true);
    expect(outcome.grade?.count).toBe(7);
    expect(outcome.grade?.key).toBe('unexecutable');
  });

  test("a box of the target's own costs both a box reference and nothing extra for the app", () => {
    // The target app is already one of Arcron's two, so reading its own box
    // adds the box and not the app.
    const outcome = accepted(
      new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
        boxes: [new algosdk.modelsv2.BoxReference({ app: TARGET_APP, name: new Uint8Array([7]) })],
      }),
    );
    expect(outcome.grade?.count).toBe(1);
    expect(outcome.counts).toEqual({ accounts: 0, apps: 1, assets: 0, boxes: 2 });
  });

  test('an asset holding costs two references, the account and the asset', () => {
    const outcome = accepted(
      new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
        assetHoldings: [
          new algosdk.modelsv2.AssetHoldingReference({ account: ACCOUNTS[0], asset: 31_566_704 }),
        ],
      }),
    );
    expect(outcome.grade?.count).toBe(2);
    expect(outcome.counts).toEqual({ accounts: 1, apps: 1, assets: 1, boxes: 1 });
  });
});

describe('readSimulatedCall: what a refusal is called', () => {
  test('an app id that is not an application on this chain is named as that', () => {
    // Measured against live TestNet with app id 1.
    const outcome = refused(
      'transaction R77D: only ClearState is supported for an application (1) that does not exist',
    );
    expect(outcome.accepted).toBe(false);
    expect(outcome.failureKind).toBe('no-such-app');
    expect(outcome.grade).toBeNull();
  });

  test('an exhausted opcode budget is named as that', () => {
    expect(refused('logic eval error: dynamic cost budget exceeded').failureKind).toBe('budget');
  });

  test('an unavailable resource is named as that', () => {
    expect(refused('logic eval error: unavailable App 769891898').failureKind).toBe('unavailable');
  });

  test("anything else is the target's own refusal, quoted", () => {
    // Measured against live TestNet: a selector the target's router does not
    // match falls through to `err`.
    const failure =
      'transaction TJ5G: logic eval error: err opcode executed. Details: app=769891902, pc=92';
    const outcome = refused(failure);
    expect(outcome.failureKind).toBe('rejected');
    expect(outcome.failure).toBe(failure);
  });

  test('a refusal is never graded, because a failing call stops touching things', () => {
    const outcome = refused('logic eval error: assert failed');
    expect(outcome.grade).toBeNull();
    expect(outcome.counts).toBeNull();
  });
});
