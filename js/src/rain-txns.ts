/**
 * Building and sending Rain's holder-facing calls.
 *
 * `draw` is the Arcron hook and is not sent from this UI. Create a rain,
 * enter once (SPLIT/ONE) or check in (WAVE), deposit, claim. ONE still
 * needs `resolve` after the committed round.
 */

import algosdk from 'algosdk';

import { rainMethod } from './rain-abi';
import { ASSET_OPT_IN_MBR } from './upkeep';
import {
  CLAIM_FEE,
  CREATE_FEE,
  DEPOSIT_FEE,
  ENTER_FEE,
  GM_FEE,
  OPT_IN_FEE,
  RAIN_BOX_MBR,
  RESOLVE_FEE,
  enterMbr,
  indexBoxName,
  rainBoxName,
  ticketBoxName,
  ONE,
} from './rain';
import { foldUnnamedResources, type CallResult, type ResourceRefs, type Signing } from './keeper-txns';

export type { Signing, CallResult } from './keeper-txns';

export interface CreateRainParams {
  readonly label: Uint8Array;
  readonly gateCreator: string;
  readonly prizeAsset: number | bigint;
  readonly drip: number | bigint;
  readonly intervalRounds: number | bigint;
  readonly mode: number | bigint;
  readonly waveCap: number | bigint;
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

type CallBuilder = (
  composer: algosdk.AtomicTransactionComposer,
  signer: algosdk.TransactionSigner,
  resources: ResourceRefs,
) => void;

async function discoverCall(
  algod: algosdk.Algodv2,
  appId: number,
  known: ResourceRefs,
  addCall: CallBuilder,
): Promise<ResourceRefs> {
  const probe = new algosdk.AtomicTransactionComposer();
  addCall(probe, algosdk.makeEmptyTransactionSigner(), known);
  const { simulateResponse } = await probe.simulate(
    algod,
    new algosdk.modelsv2.SimulateRequest({
      txnGroups: [],
      allowEmptySignatures: true,
      allowUnnamedResources: true,
    }),
  );
  const group = simulateResponse.txnGroups[0];
  if (group?.failureMessage) {
    throw new Error(`This call would fail, so it was not sent: ${group.failureMessage}`);
  }
  let resources = foldUnnamedResources(known, group?.unnamedResourcesAccessed, appId);
  for (const result of group?.txnResults ?? []) {
    resources = foldUnnamedResources(resources, result.unnamedResourcesAccessed, appId);
  }
  return resources;
}

function emptyRefs(): ResourceRefs {
  return { appAccounts: [], appForeignApps: [], appForeignAssets: [], boxes: [] };
}

export async function createRain(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  params: CreateRainParams,
): Promise<CallResult> {
  const suggestedParams = await algod.getTransactionParams().do();
  const application = await algod.getApplicationByID(appId).do();
  const next = application.params?.globalState?.find(
    (entry) => new TextDecoder().decode(entry.key) === 'next_rain_id',
  );
  const nextId = BigInt(next?.value.uint ?? 0) + 1n;
  const known: ResourceRefs = {
    ...emptyRefs(),
    boxes: [{ appIndex: 0, name: rainBoxName(nextId) }],
  };
  const addCall: CallBuilder = (composer, signer, refs) => {
    composer.addMethodCall({
      appID: appId,
      method: rainMethod('createRain'),
      sender: signing.sender,
      signer,
      suggestedParams: { ...suggestedParams, fee: BigInt(CREATE_FEE), flatFee: true },
      methodArgs: [
        payArg(signing, appId, RAIN_BOX_MBR, suggestedParams),
        params.label,
        params.gateCreator,
        BigInt(params.prizeAsset),
        BigInt(params.drip),
        BigInt(params.intervalRounds),
        BigInt(params.mode),
        BigInt(params.waveCap),
      ],
      appForeignAssets: [...refs.appForeignAssets],
      appForeignApps: [...refs.appForeignApps],
      appAccounts: [...refs.appAccounts],
      boxes: [...refs.boxes],
    });
  };
  const resources = await discoverCall(algod, appId, known, addCall);
  const composer = new algosdk.AtomicTransactionComposer();
  addCall(composer, signing.signer, resources);
  return run(algod, composer);
}

export async function optInPrizeAsset(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  assetId: number,
): Promise<CallResult> {
  const suggestedParams = await algod.getTransactionParams().do();
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: appId,
    method: rainMethod('optInPrizeAsset'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams: { ...suggestedParams, fee: BigInt(OPT_IN_FEE), flatFee: true },
    methodArgs: [BigInt(assetId), payArg(signing, appId, ASSET_OPT_IN_MBR, suggestedParams)],
    appForeignAssets: [assetId],
  });
  return run(algod, composer);
}

/** The connected account opts into an ASA so it can hold the prize. */
export async function optInHolderAsset(
  algod: algosdk.Algodv2,
  signing: Signing,
  assetId: number,
): Promise<CallResult> {
  const suggestedParams = await algod.getTransactionParams().do();
  const minFee = Number(suggestedParams.minFee ?? 1_000);
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addTransaction({
    txn: algosdk.makeAssetTransferTxnWithSuggestedParamsFromObject({
      sender: signing.sender,
      receiver: signing.sender,
      assetIndex: assetId,
      amount: 0,
      suggestedParams: { ...suggestedParams, fee: BigInt(Math.max(minFee, OPT_IN_FEE)), flatFee: true },
    }),
    signer: signing.signer,
  });
  const result = await composer.execute(algod, 6);
  return {
    txId: result.txIDs.at(-1) ?? '',
    confirmedRound: result.confirmedRound,
    returnValue: undefined,
  };
}

export async function enter(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  rainId: bigint,
  gateAsset: number,
  mode: bigint,
): Promise<CallResult> {
  const suggestedParams = await algod.getTransactionParams().do();
  const boxes = [
    { appIndex: 0, name: rainBoxName(rainId) },
    { appIndex: 0, name: ticketBoxName(rainId, signing.sender) },
  ];
  if (mode === ONE) {
    // Index of the ticket about to be written is the current ticket count;
    // simulation attaches the real one.
    boxes.push({ appIndex: 0, name: indexBoxName(rainId, 0n) });
  }
  const known: ResourceRefs = {
    appAccounts: [],
    appForeignApps: [],
    appForeignAssets: gateAsset > 0 ? [gateAsset] : [],
    boxes,
  };
  const addCall: CallBuilder = (composer, signer, refs) => {
    composer.addMethodCall({
      appID: appId,
      method: rainMethod('enter'),
      sender: signing.sender,
      signer,
      suggestedParams: { ...suggestedParams, fee: BigInt(ENTER_FEE), flatFee: true },
      methodArgs: [payArg(signing, appId, enterMbr(mode), suggestedParams), rainId, BigInt(gateAsset)],
      appForeignAssets: [...refs.appForeignAssets],
      appForeignApps: [...refs.appForeignApps],
      appAccounts: [...refs.appAccounts],
      boxes: [...refs.boxes],
    });
  };
  const resources = await discoverCall(algod, appId, known, addCall);
  const composer = new algosdk.AtomicTransactionComposer();
  addCall(composer, signing.signer, resources);
  return run(algod, composer);
}

export async function gm(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  rainId: bigint,
  gateAsset: number,
): Promise<CallResult> {
  const suggestedParams = await algod.getTransactionParams().do();
  const known: ResourceRefs = {
    appAccounts: [],
    appForeignApps: [],
    appForeignAssets: gateAsset > 0 ? [gateAsset] : [],
    boxes: [
      { appIndex: 0, name: rainBoxName(rainId) },
      { appIndex: 0, name: ticketBoxName(rainId, signing.sender) },
    ],
  };
  const addCall: CallBuilder = (composer, signer, refs) => {
    composer.addMethodCall({
      appID: appId,
      method: rainMethod('gm'),
      sender: signing.sender,
      signer,
      suggestedParams: { ...suggestedParams, fee: BigInt(GM_FEE), flatFee: true },
      methodArgs: [rainId, BigInt(gateAsset)],
      appForeignAssets: [...refs.appForeignAssets],
      appForeignApps: [...refs.appForeignApps],
      appAccounts: [...refs.appAccounts],
      boxes: [...refs.boxes],
    });
  };
  const resources = await discoverCall(algod, appId, known, addCall);
  const composer = new algosdk.AtomicTransactionComposer();
  addCall(composer, signing.signer, resources);
  return run(algod, composer);
}

export async function deposit(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  rainId: bigint,
  microAlgo: number,
): Promise<CallResult> {
  if (microAlgo <= 0) throw new Error('Deposit something');
  const suggestedParams = await algod.getTransactionParams().do();
  const known: ResourceRefs = {
    ...emptyRefs(),
    boxes: [{ appIndex: 0, name: rainBoxName(rainId) }],
  };
  const addCall: CallBuilder = (composer, signer, refs) => {
    composer.addMethodCall({
      appID: appId,
      method: rainMethod('deposit'),
      sender: signing.sender,
      signer,
      suggestedParams: { ...suggestedParams, fee: BigInt(DEPOSIT_FEE), flatFee: true },
      methodArgs: [payArg(signing, appId, microAlgo, suggestedParams), rainId],
      boxes: [...refs.boxes],
    });
  };
  const resources = await discoverCall(algod, appId, known, addCall);
  const composer = new algosdk.AtomicTransactionComposer();
  addCall(composer, signing.signer, resources);
  return run(algod, composer);
}

export async function depositAsset(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  rainId: bigint,
  assetId: number,
  amount: number,
): Promise<CallResult> {
  if (amount <= 0) throw new Error('Deposit something');
  const suggestedParams = await algod.getTransactionParams().do();
  const transfer = {
    txn: algosdk.makeAssetTransferTxnWithSuggestedParamsFromObject({
      sender: signing.sender,
      receiver: algosdk.getApplicationAddress(appId),
      assetIndex: assetId,
      amount,
      suggestedParams,
    }),
    signer: signing.signer,
  };
  const known: ResourceRefs = {
    appAccounts: [],
    appForeignApps: [],
    appForeignAssets: [assetId],
    boxes: [{ appIndex: 0, name: rainBoxName(rainId) }],
  };
  const addCall: CallBuilder = (composer, signer, refs) => {
    composer.addMethodCall({
      appID: appId,
      method: rainMethod('depositAsset'),
      sender: signing.sender,
      signer,
      suggestedParams: { ...suggestedParams, fee: BigInt(DEPOSIT_FEE), flatFee: true },
      methodArgs: [transfer, rainId],
      appForeignAssets: [...refs.appForeignAssets],
      boxes: [...refs.boxes],
    });
  };
  const resources = await discoverCall(algod, appId, known, addCall);
  const composer = new algosdk.AtomicTransactionComposer();
  addCall(composer, signing.signer, resources);
  return run(algod, composer);
}

export async function claim(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  rainId: bigint,
  gateAsset: number,
): Promise<CallResult> {
  const suggestedParams = await algod.getTransactionParams().do();
  const known: ResourceRefs = {
    appAccounts: [],
    appForeignApps: [],
    appForeignAssets: gateAsset > 0 ? [gateAsset] : [],
    boxes: [
      { appIndex: 0, name: rainBoxName(rainId) },
      { appIndex: 0, name: ticketBoxName(rainId, signing.sender) },
    ],
  };
  const addCall: CallBuilder = (composer, signer, refs) => {
    composer.addMethodCall({
      appID: appId,
      method: rainMethod('claim'),
      sender: signing.sender,
      signer,
      suggestedParams: { ...suggestedParams, fee: BigInt(CLAIM_FEE), flatFee: true },
      methodArgs: [rainId, BigInt(gateAsset)],
      appForeignAssets: [...refs.appForeignAssets],
      appForeignApps: [...refs.appForeignApps],
      appAccounts: [...refs.appAccounts],
      boxes: [...refs.boxes],
    });
  };
  const resources = await discoverCall(algod, appId, known, addCall);
  const composer = new algosdk.AtomicTransactionComposer();
  addCall(composer, signing.signer, resources);
  return run(algod, composer);
}

export async function resolve(
  algod: algosdk.Algodv2,
  appId: number,
  signing: Signing,
  rainId: bigint,
): Promise<CallResult> {
  const suggestedParams = await algod.getTransactionParams().do();
  const known: ResourceRefs = {
    ...emptyRefs(),
    boxes: [{ appIndex: 0, name: rainBoxName(rainId) }],
  };
  const addCall: CallBuilder = (composer, signer, refs) => {
    composer.addMethodCall({
      appID: appId,
      method: rainMethod('resolve'),
      sender: signing.sender,
      signer,
      suggestedParams: { ...suggestedParams, fee: BigInt(RESOLVE_FEE), flatFee: true },
      methodArgs: [rainId],
      appAccounts: [...refs.appAccounts],
      appForeignApps: [...refs.appForeignApps],
      appForeignAssets: [...refs.appForeignAssets],
      boxes: [...refs.boxes],
    });
  };
  const resources = await discoverCall(algod, appId, known, addCall);
  const composer = new algosdk.AtomicTransactionComposer();
  addCall(composer, signing.signer, resources);
  return run(algod, composer);
}

export async function readTicket(
  algod: algosdk.Algodv2,
  appId: number,
  rainId: bigint,
  who: string,
): Promise<Uint8Array | null> {
  try {
    const box = await algod.getApplicationBoxByName(appId, ticketBoxName(rainId, who)).do();
    return box.value instanceof Uint8Array ? box.value : new Uint8Array();
  } catch {
    return null;
  }
}

/** @deprecated ticket boxes are per-rain; use readTicket. */
export async function readTicketDebt(
  algod: algosdk.Algodv2,
  appId: number,
  who: string,
): Promise<bigint | null> {
  try {
    const name = new Uint8Array(33);
    name[0] = 0x74;
    name.set(algosdk.decodeAddress(who).publicKey, 1);
    const box = await algod.getApplicationBoxByName(appId, name).do();
    const raw = box.value instanceof Uint8Array ? box.value : new Uint8Array();
    if (raw.length < 8) return 0n;
    return new DataView(raw.buffer, raw.byteOffset, raw.byteLength).getBigUint64(0);
  } catch {
    return null;
  }
}
