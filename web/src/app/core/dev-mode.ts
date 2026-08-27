/**
 * Whether the console is being driven by someone developing it.
 *
 * The console has two audiences with opposite needs. Somebody arriving at the
 * published URL wants one deployment, the real one, with nothing to configure
 * and nothing to get wrong. Somebody working on the console wants to point it
 * at LocalNet, or at a superseded app, or at a deployment they just made.
 *
 * Serving both from the same controls is what created the only attack this
 * project has: a link carrying `?app=` pointed a stranger at a look-alike
 * contract with the same ABI and box layout, which shows the same registry and
 * accepts the same register form. `quarantine.ts` mitigates that. Not honouring
 * the parameter at all removes it.
 *
 * So the network picker and the app id field are developer controls, and the
 * published console does not have them. `?network=` and `?app=` are read only
 * in dev mode.
 *
 * That was once described here as making a poisoned link "inert for everyone
 * who is not already editing this code", which was false, and a review said so.
 * `?dev=1` is a public query parameter: one link of the form
 * `?dev=1&app=<look-alike>` turns dev mode on and re-arms `?app=` in the same
 * navigation, so anybody could make a stranger "already editing this code".
 * Worse, the flag persists, so a later and much more innocent-looking `?app=`
 * link stayed honoured on that browser.
 *
 * So enabling and redirecting are now separated: a navigation that turns dev
 * mode on does not also honour `?app=` or `?network=`. `established` reports
 * whether dev mode was on *before* this navigation, which is what those
 * parameters require. A developer who already has it on is unaffected; the
 * single-link attack needs both halves at once and no longer gets them.
 *
 * Quarantine stays regardless, because dev mode still honours `?app=` on
 * subsequent navigations and a developer pointed at the wrong app should still
 * be told.
 */

/** Query parameter that turns on the developer controls: `?dev=1`. */
export const DEV_PARAM = 'dev';

/** Where the flag is kept once set, so it survives navigation. */
export const DEV_STORAGE_KEY = 'arcron.dev';

/** The subset of `Storage` this module needs, so a test can supply its own. */
export type DevStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

/**
 * Whether developer controls are on.
 *
 * `?dev=1` turns it on and is remembered; `?dev=0` turns it off and forgets.
 * Anything else falls back to what was remembered.
 *
 * Reading and writing storage is wrapped because a browser with site data
 * blocked throws on access rather than returning null, and a console that
 * cannot open in a private window is worse than one without dev mode.
 */
export interface DevModeState {
    /** Whether developer controls are on at all. */
    readonly enabled: boolean;
    /**
     * Whether dev mode was already on before this navigation.
     *
     * `?app=` and `?network=` require this rather than `enabled`, so that a
     * single link cannot both turn dev mode on and point the console somewhere.
     */
    readonly established: boolean;
}

export function devModeFrom(search: string, storage: DevStorage | null): DevModeState {
    const requested = new URLSearchParams(search).get(DEV_PARAM);

    let remembered = false;
    try {
        remembered = storage?.getItem(DEV_STORAGE_KEY) === '1';
    } catch {
        // A browser blocking site data throws rather than returning null. Not
        // remembering is survivable; not opening is not.
    }

    if (requested === '1' || requested === 'true') {
        try {
            storage?.setItem(DEV_STORAGE_KEY, '1');
        } catch {
            // As above.
        }
        // `established` stays false when this navigation is what turned it on,
        // which is exactly the case `?dev=1&app=<look-alike>` relies on.
        return { enabled: true, established: remembered };
    }

    if (requested === '0' || requested === 'false') {
        try {
            storage?.removeItem(DEV_STORAGE_KEY);
        } catch {
            // As above.
        }
        return { enabled: false, established: false };
    }

    return { enabled: remembered, established: remembered };
}
