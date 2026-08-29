/**
 * Freezing is the one irreversible thing this console can do.
 *
 * Everything else here is recoverable: a bad upkeep can be cancelled and its
 * escrow refunded in full, a wrong app id can be corrected, a failed execution
 * costs nothing. `freeze` is different. After it, the programs can never be
 * replaced by anyone, and a bug found afterwards means every creator cancelling
 * and re-registering against a new deployment.
 *
 * So these tests are about refusing, not about doing. The gate has to hold
 * against the wrong account, an already-frozen app, a deployment with no freeze
 * flag at all, and a confirmation that does not actually match.
 */

import { describe, expect, test } from 'bun:test';

import {
  FREEZE_BENEFIT,
  FREEZE_CONSEQUENCE,
  confirmationMatches,
  describeState,
  whyCannotFreeze,
  type GovernState,
} from './govern-state';

const CREATOR = 'WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A';
const STRANGER = 'E5M2OH5XNDMNABJ6VOFOUVR2IKRPCGQH43PVC5P3DWQQ2LV2VJV2FJZQ3E';

function state(over: Partial<GovernState> = {}): GovernState {
  return {
    appId: 769891898n,
    creator: CREATOR,
    freeze: 'upgradeable',
    digest: 'c94c6e0cc561c028eeb3ccdd8c462c509ee106a28ba2e1d61469adbb62ffe124',
    approvalBytes: 2219,
    clearBytes: 4,
    ...over,
  };
}

describe('who may freeze', () => {
  test('the creator, on an upgradeable deployment', () => {
    expect(whyCannotFreeze(state(), CREATOR)).toBeNull();
  });

  test('nobody, while the state is still being read', () => {
    // A button enabled before the app has been read could act on assumptions
    // about a deployment nobody has looked at.
    expect(whyCannotFreeze(null, CREATOR)).toBe('Reading the deployment.');
  });

  test('not an unconnected visitor', () => {
    expect(whyCannotFreeze(state(), null)).toContain('Connect the creator');
  });

  test('not an account that is not the creator', () => {
    // The contract enforces this too. The page refusing first means an
    // operator finds out before signing rather than from a failed transaction.
    const reason = whyCannotFreeze(state(), STRANGER);
    expect(reason).toContain('not the creator');
    expect(reason).toContain('WGSHC4TY');
  });
});

describe('when there is nothing to freeze', () => {
  test('an already frozen deployment says so, and says it is permanent', () => {
    const reason = whyCannotFreeze(state({ freeze: 'frozen' }), CREATOR);
    expect(reason).toContain('Already frozen');
    expect(reason).toContain('no way back');
  });

  test('a deployment with no freeze flag is distinguished from a frozen one', () => {
    // These look similar and are not: one gave up its update path, the other
    // never had one. Telling an operator "already frozen" about an app that
    // predates the flag would be a lie about what it can do.
    const reason = whyCannotFreeze(state({ freeze: 'absent' }), CREATOR);
    expect(reason).toContain('predates');
    expect(reason).not.toContain('Already frozen');
  });
});

describe('the typed confirmation', () => {
  test('the app id, exactly, unlocks it', () => {
    // `govern freeze` on the command line asks for this too. A single click
    // would be a downgrade dressed as an improvement.
    expect(confirmationMatches('769891898', 769891898n)).toBe(true);
  });

  test('surrounding whitespace is forgiven', () => {
    expect(confirmationMatches('  769891898  ', 769891898n)).toBe(true);
  });

  test('a different app id does not', () => {
    // The mistake this catches: freezing the app you were looking at earlier.
    expect(confirmationMatches('769891902', 769891898n)).toBe(false);
  });

  test('nor does a prefix, a suffix, or nothing', () => {
    for (const typed of ['', '76989189', '7698918980', 'yes', 'freeze']) {
      expect(confirmationMatches(typed, 769891898n)).toBe(false);
    }
  });
});

describe('what an operator is told before they decide', () => {
  test('the cost is stated in full', () => {
    const text = FREEZE_CONSEQUENCE.join(' ');
    expect(text).toContain('never be replaced');
    expect(text).toContain('cannot be fixed in place');
    expect(text).toContain('no way back');
  });

  test('and so is the reason to do it anyway', () => {
    // A page that only lists consequences reads as a warning to avoid the
    // action. Freezing is the thing that makes a single-key creator
    // defensible, so the benefit belongs beside the cost.
    const text = FREEZE_BENEFIT.join(' ');
    expect(text).toContain('key stops mattering');
    expect(text).toContain('verify');
  });
});

describe('describing the state', () => {
  test('upgradeable says who can still act', () => {
    expect(describeState(state())).toContain('creator can still replace');
  });

  test('frozen and absent are not the same sentence', () => {
    expect(describeState(state({ freeze: 'frozen' }))).not.toBe(
      describeState(state({ freeze: 'absent' })),
    );
  });

  test('nothing read yet is not reported as a state', () => {
    expect(describeState(null)).toBe('Reading…');
  });
});
