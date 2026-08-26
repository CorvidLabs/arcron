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
import { ASSET_OPT_IN_MBR, boxMbr, EXECUTE_FEE, type Upkeep, upkeepBoxName } from './upkeep';

export interface Signing {
  readonly sender: string;
  readonly signer: algosdk.TransactionSigner;
}

export interface RegisterParams {
  readonly targetApp: number;
  /** Every app arg of the call, in order; element 0 is app arg 0. */
  readonly callArgs: readonly Uint8Array[];
  readonly intervalRounds: number;
  readonly feePerExecution: number;
  readonly funding: number;
  /** CATCH_UP replays every missed interval; SKIP_AHEAD runs once and moves on. */
  readonly policy: number;
  /** The most one run may ever cost; 0 means the fee never escalates. */
  readonly feeCap: number;
  /** An ASA bonus on top of the ALGO fee; 0 means ALGO only. */
  readonly feeAsset: number;
  /** The bonus per execution, in the asset's base units. */
  readonly assetFee: number;
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

/**
 * Whether `address` can receive `assetId`.
 *
 * An account that never opted in cannot be sent the asset at all, which is
 * why the contract checks the same thing before paying a bonus.
 */
async function optedIn(
  algod: algosdk.Algodv2,
  address: string,
  assetId: bigint,
): Promise<boolean> {
  if (assetId === 0n) return false;
  const account = await algod.accountInformation(address).do();
  return (account.assets ?? []).some((holding) => BigInt(holding.assetId) === assetId);
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

/** The id `register` will assign next, which is also the box it must reference. */
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
      payment(boxMbr(params.callArgs)),
      payment(params.funding),
      params.targetApp,
      // algosdk encodes `byte[][]` from arrays of numbers, not Uint8Arrays.
      params.callArgs.map((arg) => Array.from(arg)),
      params.intervalRounds,
      params.feePerExecution,
      params.policy,
      params.feeCap,
      params.feeAsset,
      params.assetFee,
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
  feeAsset = 0n,
): Promise<CallResult> {
  // An upkeep holding an ASA bonus makes `cancel` read that asset's holding
  // and freeze flags before deciding whether to send it, so the asset has to
  // be an available resource even on the paths where nothing is transferred.
  // Without it the call fails with `unavailable Asset N`. The Python side
  // never noticed: algokit-utils simulates and fills resources in, and this
  // client builds its transactions by hand.
  const usesAsset = feeAsset > 0n;
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: keeperMethod('cancel'),
    sender: signing.sender,
    signer: signing.signer,
    // Covers the refund payment, and the bonus transfer when there is one.
    suggestedParams: await flatFee(algod, usesAsset ? 3_000 : 2_000),
    methodArgs: [upkeepId],
    boxes: [{ appIndex: 0, name: upkeepBoxName(upkeepId) }],
    ...(usesAsset ? { appForeignAssets: [Number(feeAsset)] } : {}),
  });
  return run(algod, composer);
}

export async function execute(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  upkeep: Pick<Upkeep, 'id' | 'targetApp' | 'feeAsset'>,
): Promise<CallResult> {
  // Same reason as `cancel`: with a fee asset set, `execute` reads that
  // asset's holding and freeze flags whether or not a bonus ends up moving,
  // so it has to be available. The extra 1,000 covers the bonus transfer,
  // which is a third inner transaction on top of the registered call and the
  // keeper's own payment.
  const usesAsset = (upkeep.feeAsset ?? 0n) > 0n;
  // The contract pays the bonus only to a keeper opted in to the asset, so
  // the surcharge has to ask the same question. Paying it regardless meant a
  // keeper that could never receive a bonus funded its transfer anyway:
  // Algorand pools fees and keeps the unused part, so exactly the executions
  // that pay most were the ones that netted least.
  const paysBonus = usesAsset && (await optedIn(algod, signing.sender, upkeep.feeAsset ?? 0n));
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: keeperMethod('execute'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams: await flatFee(algod, paysBonus ? EXECUTE_FEE + 1_000 : EXECUTE_FEE),
    methodArgs: [upkeep.id],
    boxes: [{ appIndex: 0, name: upkeepBoxName(upkeep.id) }],
    appForeignApps: [Number(upkeep.targetApp)],
    ...(usesAsset ? { appForeignAssets: [Number(upkeep.feeAsset)] } : {}),
  });
  return run(algod, composer);
}

/**
 * Let the app account hold an asset, so an upkeep can escrow a bonus in it.
 *
 * Permissionless but tied to an upkeep that names the asset, and the 0.1 ALGO
 * it costs is not refundable, because there is no opt-out.
 */
export async function optInAsset(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  upkeepId: bigint,
  assetId: number,
): Promise<CallResult> {
  const suggestedParams = await algod.getTransactionParams().do();
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: keeperMethod('optInAsset'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams: { ...suggestedParams, flatFee: true, fee: 2_000 },
    methodArgs: [
      {
        txn: algosdk.makePaymentTxnWithSuggestedParamsFromObject({
          sender: signing.sender,
          receiver: algosdk.getApplicationAddress(appId),
          amount: ASSET_OPT_IN_MBR,
          suggestedParams,
        }),
        signer: signing.signer,
      },
      upkeepId,
      assetId,
    ],
    appForeignAssets: [assetId],
    boxes: [{ appIndex: 0, name: upkeepBoxName(upkeepId) }],
  });
  return run(algod, composer);
}

/** Add to an upkeep's ASA bonus escrow, in the asset's base units. */
export async function topUpAsset(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  upkeepId: bigint,
  assetId: number,
  amount: number,
): Promise<CallResult> {
  const suggestedParams = await algod.getTransactionParams().do();
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: keeperMethod('topUpAsset'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams,
    methodArgs: [
      upkeepId,
      {
        txn: algosdk.makeAssetTransferTxnWithSuggestedParamsFromObject({
          sender: signing.sender,
          receiver: algosdk.getApplicationAddress(appId),
          assetIndex: assetId,
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
