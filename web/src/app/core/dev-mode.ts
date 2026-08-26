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
 * in dev mode, which means a poisoned link is inert for everyone who is not
 * already editing this code.
 *
 * Quarantine stays, because dev mode still honours `?app=` and a developer
 * pointed at the wrong app should still be told.
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
export function devModeFrom(search: string, storage: DevStorage | null): boolean {
    const requested = new URLSearchParams(search).get(DEV_PARAM);

    if (requested === '1' || requested === 'true') {
        try {
            storage?.setItem(DEV_STORAGE_KEY, '1');
        } catch {
            // Not remembered is survivable; not working is not.
        }
        return true;
    }

    if (requested === '0' || requested === 'false') {
        try {
            storage?.removeItem(DEV_STORAGE_KEY);
        } catch {
            // As above.
        }
        return false;
    }

    try {
        return storage?.getItem(DEV_STORAGE_KEY) === '1';
    } catch {
        return false;
    }
}
