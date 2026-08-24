/**
 * The keeper app's ABI surface, as method signatures.
 *
 * Kept as signatures rather than importing the generated ARC-56 artifact so
 * the web app has no build-time coupling to the Python toolchain;
 * `keeper-abi.test.ts` checks them against that artifact so drift is caught
 * in CI instead of at runtime.
 */

import algosdk from 'algosdk';

export const KEEPER_METHOD_SIGNATURES = {
  register: 'register(pay,pay,uint64,byte[],uint64,uint64)uint64',
  topUp: 'top_up(uint64,pay)uint64',
  cancel: 'cancel(uint64)uint64',
  execute: 'execute(uint64)uint64',
} as const;

export type KeeperMethodName = keyof typeof KEEPER_METHOD_SIGNATURES;

export function keeperMethod(name: KeeperMethodName): algosdk.ABIMethod {
  return algosdk.ABIMethod.fromSignature(KEEPER_METHOD_SIGNATURES[name]);
}

/** The selector a target app's hook is called with, e.g. `tick()uint64`. */
export function methodSelector(signature: string): Uint8Array {
  return algosdk.ABIMethod.fromSignature(signature).getSelector();
}

/** The demo target's hook — the default when registering an upkeep. */
export const PULSE_TICK_SIGNATURE = 'tick()uint64';
