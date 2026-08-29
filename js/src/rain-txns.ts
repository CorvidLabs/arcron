/**
 * Building and sending Rain's holder-facing calls.
 *
 * `draw` is the Arcron hook and is not sent from this UI. `enter` once;
 * cadence is the upkeep. `claim` still needs a collection token in hand.
 */

import algosdk from 'algosdk';

import { rainMethod } from './rain-abi';
import {
  ABANDON_FEE,
  CLAIM_FEE,
  DEPOSIT_FEE,
  ENTER_FEE,
  RESOLVE_FEE,
  TICKET_MBR,
  allocationBoxName,
  ticketBoxName,
} from './rain';
import type { Signing, CallResult } from './keeper-txns';

export type { Signing, CallResult } from './keeper-txns';

async function flatFee(algod: algosdk.Algodv2, microAlgo: number): Promise<algosdk.SuggestedParams> {
  const params = await algod.getTransactionParams().do();
  return { ...params, fee: BigInt(microAlgo), flatFee: true };
}

async function run(
  algod: algosdk.Algodv2,
  composer: algosdk.AtomicTransactionComposer,
): Promise<CallResult> {
  const result = await composer.execute(algod, 6);
  const returned = result.methodResults.at(-1);
  if (returned?.decodeError) throw returned.decodeError;
  return {
    txId: result.txIDs.at(-1) ?? '',
    confirmedRound: result.confirmedRound,
    returnValue: returned?.returnValue as bigint | undefined,
  };
}

function payArg(
  signing: Signing,
  appId: number,
  amount: number,
  suggestedParams: algosdk.SuggestedParams,
): { txn: algosdk.Transaction; signer: algosdk.TransactionSigner } {
  return {
    txn: algosdk.makePaymentTxnWithSuggestedParamsFromObject({
      sender: signing.sender,
      receiver: algosdk.getApplicationAddress(appId),
      amount,
      suggestedParams,
    }),
    signer: signing.signer,
  };
}

/** Buy one ticket. Lasts for every future draw. `gateAsset` is the NFT you hold. */
export async function enter(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  gateAsset: number,
): Promise<CallResult> {
  const suggestedParams = await algod.getTransactionParams().do();
  const callParams = { ...suggestedParams, fee: BigInt(ENTER_FEE), flatFee: true };
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: rainMethod('enter'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams: callParams,
    methodArgs: [payArg(signing, appId, TICKET_MBR, suggestedParams), BigInt(gateAsset)],
    appForeignAssets: [gateAsset],
  });
  return run(algod, composer);
}

/** Add ALGO to the pot. Anyone. Amount is microAlgos. */
export async function deposit(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  microAlgo: number,
): Promise<CallResult> {
  if (microAlgo <= 0) throw new Error('Deposit something');
  const suggestedParams = await algod.getTransactionParams().do();
  const callParams = { ...suggestedParams, fee: BigInt(DEPOSIT_FEE), flatFee: true };
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: rainMethod('deposit'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams: callParams,
    methodArgs: [payArg(signing, appId, microAlgo, suggestedParams)],
  });
  return run(algod, composer);
}

/** Pull a prize. `gateAsset` is the collection token you still hold. */
export async function claim(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  gateAsset: number,
): Promise<CallResult> {
  const suggestedParams = await flatFee(algod, CLAIM_FEE);
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: rainMethod('claim'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams,
    methodArgs: [BigInt(gateAsset)],
    appForeignAssets: gateAsset > 0 ? [gateAsset] : [],
    boxes: [{ appIndex: 0, name: allocationBoxName(signing.sender) }],
  });
  return run(algod, composer);
}

/** Ask the beacon who won. Permissionless; attaches the beacon as a foreign app. */
export async function resolve(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  beaconApp: number,
): Promise<CallResult> {
  const suggestedParams = await flatFee(algod, RESOLVE_FEE);
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: rainMethod('resolve'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams,
    methodArgs: [],
    appForeignApps: [beaconApp],
  });
  return run(algod, composer);
}

/** Put an expired draw's prize back in the pot. Permissionless. */
export async function abandon(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
): Promise<CallResult> {
  const suggestedParams = await flatFee(algod, ABANDON_FEE);
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: rainMethod('abandon'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams,
    methodArgs: [],
  });
  return run(algod, composer);
}

export async function readAllocation(
  algod: algosdk.Algodv2,
  appId: number,
  who: string,
): Promise<bigint> {
  try {
    const box = await algod.getApplicationBoxByName(appId, allocationBoxName(who)).do();
    const raw = box.value instanceof Uint8Array ? box.value : new Uint8Array();
    if (raw.length < 8) return 0n;
    return new DataView(raw.buffer, raw.byteOffset, raw.byteLength).getBigUint64(0);
  } catch {
    return 0n;
  }
}

export async function readTicketHolder(
  algod: algosdk.Algodv2,
  appId: number,
  index: bigint,
): Promise<string | null> {
  try {
    const box = await algod.getApplicationBoxByName(appId, ticketBoxName(index)).do();
    const raw = box.value instanceof Uint8Array ? box.value : new Uint8Array();
    if (raw.length !== 32) return null;
    return algosdk.encodeAddress(raw);
  } catch {
    return null;
  }
}
