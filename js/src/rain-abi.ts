/**
 * Rain's ABI surface, as method signatures.
 *
 * Same reason as `keeper-abi.ts`: the web app must not import the Python
 * artifact at build time; `rain-abi.test.ts` checks these against it.
 */

import algosdk from 'algosdk';

export const RAIN_METHOD_SIGNATURES = {
  bootstrap: 'bootstrap(pay)void',
  optInPrizeAsset: 'opt_in_prize_asset(uint64,pay)uint64',
  createRain: 'create_rain(pay,byte[32],address,uint64,uint64,uint64,uint64,uint64)uint64',
  setRain: 'set_rain(uint64,uint64,uint64)void',
  enter: 'enter(pay,uint64,uint64)uint64',
  gm: 'gm(uint64,uint64)uint64',
  deposit: 'deposit(pay,uint64)uint64',
  depositAsset: 'deposit_asset(axfer,uint64)uint64',
  draw: 'draw()uint64',
  resolve: 'resolve(uint64)uint64',
  abandon: 'abandon(uint64)uint64',
  claim: 'claim(uint64,uint64)uint64',
  allocationOf: 'allocation_of(uint64,address)uint64',
  rainOf:
    'rain_of(uint64)(address,address,byte[32],uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64,uint64)',
} as const;

export type RainMethodName = keyof typeof RAIN_METHOD_SIGNATURES;

export function rainMethod(name: RainMethodName): algosdk.ABIMethod {
  return algosdk.ABIMethod.fromSignature(RAIN_METHOD_SIGNATURES[name]);
}
