/**
 * Building and sending the keeper app's four calls.
 *
 * Written as plain functions over algosdk so they can be exercised against a
 * real node without a browser. Two details the AVM insists on and a typed
 * AlgoKit client would otherwise hide:
 *
 *   * every call touching an upkeep must reference its box, and `register`
 *     must reference the box the app is *about* to allocate (`next_upkeep_id`)
 *   * `execute` performs an inner app call, so the target app has to be in the
 *     foreign apps array, and the caller pays the inner fees (fee pooling)
 */

import algosdk from 'algosdk';

import { keeperMethod } from './keeper-abi';
import { boxMbr, EXECUTE_FEE, type Upkeep, upkeepBoxName } from './upkeep';

export interface Signing {
  readonly sender: string;
  readonly signer: algosdk.TransactionSigner;
}

export interface RegisterParams {
  readonly targetApp: number;
  readonly callData: Uint8Array;
  readonly intervalRounds: number;
  readonly feePerExecution: number;
  readonly funding: number;
  /** CATCH_UP replays every missed interval; SKIP_AHEAD runs once and moves on. */
  readonly policy: number;
  /** The most one run may ever cost; 0 means the fee never escalates. */
  readonly feeCap: number;
}

/** A confirmed call: the round it landed in and whatever the method returned. */
export interface CallResult<Value = bigint | undefined> {
  readonly txId: string;
  readonly confirmedRound: bigint;
  readonly returnValue: Value;
}

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

/** The id `register` will assign next — also the box it must reference. */
export async function nextUpkeepId(algod: algosdk.Algodv2, appId: number): Promise<bigint> {
  const application = await algod.getApplicationByID(appId).do();
  const counter = application.params?.globalState?.find(
    (entry) => new TextDecoder().decode(entry.key) === 'next_upkeep_id',
  );
  return BigInt(counter?.value.uint ?? 0);
}

export async function register(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  params: RegisterParams,
): Promise<CallResult> {
  const appAddress = algosdk.getApplicationAddress(appId);
  const suggestedParams = await algod.getTransactionParams().do();
  const composer = new algosdk.AtomicTransactionComposer();

  const payment = (amount: number) => ({
    txn: algosdk.makePaymentTxnWithSuggestedParamsFromObject({
      sender: signing.sender,
      receiver: appAddress,
      amount,
      suggestedParams,
    }),
    signer: signing.signer,
  });

  composer.addMethodCall({
    appID: appId,
    method: keeperMethod('register'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams,
    methodArgs: [
      payment(boxMbr(params.callData.length)),
      payment(params.funding),
      params.targetApp,
      params.callData,
      params.intervalRounds,
      params.feePerExecution,
      params.policy,
      params.feeCap,
    ],
    boxes: [{ appIndex: 0, name: upkeepBoxName(await nextUpkeepId(algod, appId)) }],
  });
  return run(algod, composer);
}

export async function topUp(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  upkeepId: bigint,
  amount: number,
): Promise<CallResult> {
  const suggestedParams = await algod.getTransactionParams().do();
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: keeperMethod('topUp'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams,
    methodArgs: [
      upkeepId,
      {
        txn: algosdk.makePaymentTxnWithSuggestedParamsFromObject({
          sender: signing.sender,
          receiver: algosdk.getApplicationAddress(appId),
          amount,
          suggestedParams,
        }),
        signer: signing.signer,
      },
    ],
    boxes: [{ appIndex: 0, name: upkeepBoxName(upkeepId) }],
  });
  return run(algod, composer);
}

export async function cancel(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  upkeepId: bigint,
): Promise<CallResult> {
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: keeperMethod('cancel'),
    sender: signing.sender,
    signer: signing.signer,
    // Covers the refund payment the contract sends back.
    suggestedParams: await flatFee(algod, 2_000),
    methodArgs: [upkeepId],
    boxes: [{ appIndex: 0, name: upkeepBoxName(upkeepId) }],
  });
  return run(algod, composer);
}

export async function execute(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  upkeep: Pick<Upkeep, 'id' | 'targetApp'>,
): Promise<CallResult> {
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: keeperMethod('execute'),
    sender: signing.sender,
    signer: signing.signer,
    // The outer fee pools for both inner transactions: the registered app
    // call and the keeper's payment.
    suggestedParams: await flatFee(algod, EXECUTE_FEE),
    methodArgs: [upkeep.id],
    boxes: [{ appIndex: 0, name: upkeepBoxName(upkeep.id) }],
    appForeignApps: [Number(upkeep.targetApp)],
  });
  return run(algod, composer);
}
