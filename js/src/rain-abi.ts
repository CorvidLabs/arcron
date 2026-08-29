/**
 * Rain's ABI surface, as method signatures.
 *
 * Same reason as `keeper-abi.ts`: the web app must not import the Python
 * artifact at build time; `rain-abi.test.ts` checks these against it.
 */

import algosdk from 'algosdk';

export const RAIN_METHOD_SIGNATURES = {
  // The `byte[]` is `gate_unit_prefix`, added when Rain learned to gate on
  // a unit-name prefix as well as a creator. It is case sensitive on
  // purpose: two collections whose unit names differ only in case are two
  // different collections, and a holder needs to be able to tell them
  // apart. Empty means creator-only gating.
  configure: 'configure(pay,uint64,address,byte[],uint64)void',
  optInPrizeAsset: 'opt_in_prize_asset(uint64,pay)uint64',
  enter: 'enter(pay,uint64)uint64',
  deposit: 'deposit(pay)uint64',
  depositAsset: 'deposit_asset(axfer)uint64',
  draw: 'draw()uint64',
  resolve: 'resolve()address',
  claim: 'claim(uint64)uint64',
  abandon: 'abandon()uint64',
  allocationOf: 'allocation_of(address)uint64',
  update: 'update()void',
  freeze: 'freeze()void',
} as const;

export type RainMethodName = keyof typeof RAIN_METHOD_SIGNATURES;

export function rainMethod(name: RainMethodName): algosdk.ABIMethod {
  return algosdk.ABIMethod.fromSignature(RAIN_METHOD_SIGNATURES[name]);
}
