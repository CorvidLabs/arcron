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
 *   * the target's own inner call can reach further accounts, assets, apps
 *     and boxes that no argument names, so `execute` simulates itself first
 *     and attaches whatever algod reports, the same idea algokit-utils uses
 *     for the Python bot (see `discoverResources` below)
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

  // The note is what keeps the two legs distinct. They share sender, receiver
  // and suggested params, so when the box MBR happens to equal the funding
  // amount they serialise to byte-identical transactions with the same txid,
  // and the group is unsubmittable. The register form's whole stated aim is
  // to turn a rejected transaction into a disabled button, and this got past
  // it: every validator passes and the failure arrives from the network.
  const payment = (amount: number, leg: string) => ({
    txn: algosdk.makePaymentTxnWithSuggestedParamsFromObject({
      sender: signing.sender,
      receiver: appAddress,
      amount,
      suggestedParams,
      note: new TextEncoder().encode(`arcron:${leg}`),
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
      payment(boxMbr(params.callArgs), 'mbr'),
      payment(params.funding, 'funding'),
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
  const suggestedParams = await flatFee(algod, paysBonus ? EXECUTE_FEE + 1_000 : EXECUTE_FEE);

  // `execute`'s inner call reaches whatever the target's own logic reaches,
  // and Arcron stores no list of that: the docs' answer is "a keeper
  // simulates first and attaches what algod reports", which the Python bot
  // gets for free from algokit-utils. This is that same idea against raw
  // algosdk. `discoverResources` runs the call once against a throwaway
  // composer that never needs a real signature, reads back whatever the
  // target touched that this group did not already declare, and folds it
  // into what gets attached for the real, signed submission below.
  const known = knownExecuteResources(upkeep, usesAsset);
  const resources = await discoverResources(algod, appId, signing.sender, upkeep.id, suggestedParams, known);

  const composer = new algosdk.AtomicTransactionComposer();
  addExecuteCall(composer, appId, signing.sender, signing.signer, upkeep.id, suggestedParams, resources);
  return run(algod, composer);
}

/** The upkeep box, the target app and the fee asset: known before simulating anything. */
function knownExecuteResources(
  upkeep: Pick<Upkeep, 'id' | 'targetApp' | 'feeAsset'>,
  usesAsset: boolean,
): ResourceRefs {
  return {
    appAccounts: [],
    appForeignApps: [Number(upkeep.targetApp)],
    appForeignAssets: usesAsset ? [Number(upkeep.feeAsset)] : [],
    boxes: [{ appIndex: 0, name: upkeepBoxName(upkeep.id) }],
  };
}

/** Add the `execute` method call to `composer`, with a given resource set and signer. */
function addExecuteCall(
  composer: algosdk.AtomicTransactionComposer,
  appId: number,
  sender: string,
  signer: algosdk.TransactionSigner,
  upkeepId: bigint,
  suggestedParams: algosdk.SuggestedParams,
  resources: ResourceRefs,
): void {
  composer.addMethodCall({
    appID: appId,
    method: keeperMethod('execute'),
    sender,
    signer,
    suggestedParams,
    methodArgs: [upkeepId],
    // Copied rather than passed through: `ResourceRefs` is `readonly` because
    // `foldUnnamedResources` is meant to be used as a pure function, and
    // `addMethodCall`'s own parameter types are plain mutable arrays.
    boxes: [...resources.boxes],
    appAccounts: [...resources.appAccounts],
    appForeignApps: [...resources.appForeignApps],
    appForeignAssets: [...resources.appForeignAssets],
  });
}

/**
 * Simulate the call once, with an empty signer and `allowUnnamedResources`,
 * and fold whatever algod reports the target touched into `known`.
 *
 * The empty signer and `allowEmptySignatures` mean this never asks a wallet
 * to sign anything: it is a read, not a transaction the caller is committing
 * to. `composer.simulate` still calls through `gatherSignatures`, which is
 * exactly what produces the placeholder signature `allowEmptySignatures`
 * tells algod to accept.
 */
async function discoverResources(
  algod: algosdk.Algodv2,
  appId: number,
  sender: string,
  upkeepId: bigint,
  suggestedParams: algosdk.SuggestedParams,
  known: ResourceRefs,
): Promise<ResourceRefs> {
  const probe = new algosdk.AtomicTransactionComposer();
  addExecuteCall(probe, appId, sender, algosdk.makeEmptyTransactionSigner(), upkeepId, suggestedParams, known);

  const { simulateResponse } = await probe.simulate(
    algod,
    new algosdk.modelsv2.SimulateRequest({
      txnGroups: [],
      allowEmptySignatures: true,
      allowUnnamedResources: true,
    }),
  );

  // Group-level, not per-transaction: this group is one transaction, and the
  // API's own distinction is that only the group-level object qualifies for
  // group resource sharing, which is what a single-transaction group needs.
  const unnamed = simulateResponse.txnGroups[0]?.unnamedResourcesAccessed;
  return foldUnnamedResources(known, unnamed, appId);
}

/** The four legacy foreign-reference arrays a v1 AVM app call still uses. */
export interface ResourceRefs {
  readonly appAccounts: readonly string[];
  readonly appForeignApps: readonly number[];
  readonly appForeignAssets: readonly number[];
  readonly boxes: readonly { appIndex: number; name: Uint8Array }[];
}

/**
 * Fold a simulate response's `unnamedResourcesAccessed` into `known`, the same
 * union `algokit-utils`' `populate_app_call_resources` produces.
 *
 * Exported and pure, with no `algod` argument, so this folding logic can be
 * tested without a node: the network round trip is `discoverResources` above.
 * `callingAppId` is the keeper app, so a reference to it (or the sentinel `0`
 * the API uses for "the calling app") needs no declaration of its own.
 */
export function foldUnnamedResources(
  known: ResourceRefs,
  unnamed: algosdk.modelsv2.SimulateUnnamedResourcesAccessed | undefined,
  callingAppId: number,
): ResourceRefs {
  if (!unnamed) return known;

  const accounts = new Set(known.appAccounts);
  const apps = new Set(known.appForeignApps);
  const assets = new Set(known.appForeignAssets);
  const boxes = [...known.boxes];
  const zero = algosdk.Address.zeroAddress();

  const addAccount = (address: algosdk.Address) => {
    if (!address.equals(zero)) accounts.add(address.toString());
  };
  const addApp = (id: bigint) => {
    if (id !== 0n && id !== BigInt(callingAppId)) apps.add(Number(id));
  };

  for (const address of unnamed.accounts ?? []) addAccount(address);
  for (const app of unnamed.apps ?? []) addApp(app);
  for (const asset of unnamed.assets ?? []) assets.add(Number(asset));
  for (const box of unnamed.boxes ?? []) {
    const isOwn = box.app === 0n || box.app === BigInt(callingAppId);
    if (!isOwn) addApp(box.app);
    boxes.push({ appIndex: isOwn ? 0 : Number(box.app), name: box.name });
  }
  // A holding or a local read needs the account AND the asset or app present;
  // the legacy arrays have no reference shape narrower than that cross
  // product, so this is the closest a v1 app call can declare either.
  for (const holding of unnamed.assetHoldings ?? []) {
    addAccount(holding.account);
    assets.add(Number(holding.asset));
  }
  for (const local of unnamed.appLocals ?? []) {
    addAccount(local.account);
    addApp(local.app);
  }
  // Extra box references bump the box I/O budget without naming a box: an
  // empty reference asks for exactly that and nothing else.
  for (let index = 0; index < (unnamed.extraBoxRefs ?? 0); index += 1) {
    boxes.push({ appIndex: 0, name: new Uint8Array(0) });
  }

  return {
    appAccounts: [...accounts],
    appForeignApps: [...apps],
    appForeignAssets: [...assets],
    boxes,
  };
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
