/**
 * Rain's ABI surface, as method signatures.
 *
 * Same reason as `keeper-abi.ts`: the web app must not import the Python
 * artifact at build time; `rain-abi.test.ts` checks these against it.
 */

import algosdk from 'algosdk';

export const RAIN_METHOD_SIGNATURES = {
  configure: 'configure(pay,uint64,address,uint64)void',
  optInPrizeAsset: 'opt_in_prize_asset(uint64,pay)uint64',
  enter: 'enter(pay,uint64)uint64',
  deposit: 'deposit(pay)uint64',
  depositAsset: 'deposit_asset(axfer)uint64',
  draw: 'draw()uint64',
  resolve: 'resolve()address',
  claim: 'claim(uint64)uint64',
  abandon: 'abandon()uint64',
  allocationOf: 'allocation_of(address)uint64',
} as const;

export type RainMethodName = keyof typeof RAIN_METHOD_SIGNATURES;

export function rainMethod(name: RainMethodName): algosdk.ABIMethod {
  return algosdk.ABIMethod.fromSignature(RAIN_METHOD_SIGNATURES[name]);
}
