/**
 * What a deployment's governance state is, and whether this browser can change it.
 *
 * The MainNet creator is a single account held in a wallet, decided 2026-08-29
 * after establishing that no wallet will sign for a multisig sender. That makes
 * governance a thing a person can do from a page with their own wallet, which
 * was never true of a 2-of-3.
 *
 * Only `freeze` is offered here. `update` replaces the programs, and a browser
 * cannot compile Algorand Python, so the bytes would have to come from
 * somewhere the page cannot verify. Offering an update whose payload the page
 * cannot check would be worse than not offering it, so that stays on the
 * command line where `verify_build` is a rebuild rather than a download.
 *
 * `freeze` needs no payload at all. It is a bare NoOp call, and its whole risk
 * is that it is permanent.
 */

/** The three states a deployment's freeze flag can be in. */
export type FreezeState = 'upgradeable' | 'frozen' | 'absent';

export interface GovernState {
  readonly appId: bigint;
  readonly creator: string;
  readonly freeze: FreezeState;
  /** sha256 over approval + 0x00 + clear, matching `scripts/verify_build`. */
  readonly digest: string;
  readonly approvalBytes: number;
  readonly clearBytes: number;
}

/** Why the freeze button is unavailable, or null when it is available. */
export function whyCannotFreeze(
  state: GovernState | null,
  connected: string | null,
): string | null {
  if (state === null) return 'Reading the deployment.';
  if (state.freeze === 'frozen') {
    return 'Already frozen. The programs can never be replaced, and there is no way back.';
  }
  if (state.freeze === 'absent') {
    return (
      'This app predates the freeze flag and has no update path at all, so there is ' +
      'nothing to give up.'
    );
  }
  if (connected === null) return 'Connect the creator account.';
  if (connected !== state.creator) {
    return (
      `Connected as an account that is not the creator. Only ${shorten(state.creator)} ` +
      'can freeze this deployment.'
    );
  }
  return null;
}

/**
 * Whether the typed confirmation matches.
 *
 * `govern freeze` on the command line asks the operator to type the app id back
 * before it will act. A button that only needs one click would be a downgrade
 * dressed as an improvement, so the page asks for the same thing. This is the
 * only irreversible action in the whole console.
 */
export function confirmationMatches(typed: string, appId: bigint): boolean {
  return typed.trim() === appId.toString();
}

/** What freezing actually costs, in the words a person should read first. */
export const FREEZE_CONSEQUENCE = [
  'The programs can never be replaced again, by anyone, including you.',
  'A bug found afterwards cannot be fixed in place. Every creator would have to ' +
    'cancel and re-register against a new deployment.',
  'This is the one action in this console with no way back.',
] as const;

/** What freezing is worth, which is the reason to do it anyway. */
export const FREEZE_BENEFIT = [
  'The creator key stops mattering. Losing it, or its being compromised, no longer ' +
    'threatens anyone escrowing here.',
  'Anyone reading the contract can verify there is no admin, rather than trusting ' +
    'that there will not be one.',
] as const;

function shorten(address: string): string {
  return `${address.slice(0, 8)}…${address.slice(-4)}`;
}

/** A one-line description of the state, for the page header. */
export function describeState(state: GovernState | null): string {
  if (state === null) return 'Reading…';
  switch (state.freeze) {
    case 'frozen':
      return 'Frozen. The programs cannot be replaced.';
    case 'absent':
      return 'No freeze flag. This deployment has no update path.';
    case 'upgradeable':
      return 'Upgradeable. The creator can still replace the programs.';
  }
}
