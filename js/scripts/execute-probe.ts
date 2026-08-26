/**
 * One-off proof for #103: does `execute()` service an upkeep whose target
 * reaches an account no argument names?
 *
 * Driven from `scripts/spike_js_execute_resources.py`, which deploys Keeper
 * and ResourceProbe, points `probe_payment()` at an account named nowhere in
 * the call, registers, and waits for the upkeep to come due. This script then
 * runs one of two shapes against that same due upkeep:
 *
 *   bun run js/scripts/execute-probe.ts <keeperAppId> <upkeepId> <probeAppId> naive
 *   bun run js/scripts/execute-probe.ts <keeperAppId> <upkeepId> <probeAppId> fixed
 *
 * `naive` rebuilds the call the way `execute()` looked before this fix:
 * target app and box, nothing discovered. `fixed` calls the real, current
 * `execute()` from `../src/keeper-txns.ts`. Prints one JSON line and exits 0
 * on success, 1 with the algod rejection otherwise.
 *
 * LocalNet only: signing comes from KMD, the same pattern
 * `web/scripts/localnet-txns.ts` uses.
 */
import algosdk from 'algosdk';

import { execute } from '../src/keeper-txns';
import { keeperMethod } from '../src/keeper-abi';
import { NETWORKS } from '../src/networks';
import { EXECUTE_FEE, upkeepBoxName } from '../src/upkeep';

const [keeperAppIdArg, upkeepIdArg, probeAppIdArg, mode] = process.argv.slice(2);
if (!keeperAppIdArg || !upkeepIdArg || !probeAppIdArg || (mode !== 'naive' && mode !== 'fixed')) {
  console.error('usage: execute-probe.ts <keeperAppId> <upkeepId> <probeAppId> naive|fixed');
  process.exit(2);
}
const keeperAppId = Number(keeperAppIdArg);
const upkeepId = BigInt(upkeepIdArg);
const probeAppId = Number(probeAppIdArg);

const localnet = NETWORKS.localnet;
if (!localnet.kmd) throw new Error('localnet config has no kmd entry');
const algod = new algosdk.Algodv2(localnet.algod.token, localnet.algod.server, localnet.algod.port);
const kmd = new algosdk.Kmd(localnet.kmd.token, localnet.kmd.server, localnet.kmd.port);

const wallets = await kmd.listWallets();
const handle = (await kmd.initWalletHandle(wallets.wallets[0].id, '')).wallet_handle_token;
const addresses: string[] = (await kmd.listKeys(handle)).addresses;
const balances = await Promise.all(
  addresses.map(async (address) => (await algod.accountInformation(address).do()).amount),
);
const richest = balances.indexOf(balances.reduce((max, balance) => (balance > max ? balance : max), 0n));
const sender = addresses[richest];
const signer: algosdk.TransactionSigner = async (group, indexes) =>
  Promise.all(indexes.map(async (index) => new Uint8Array(await kmd.signTransaction(handle, '', group[index]))));
const signing = { sender, signer };

async function naiveExecute(): Promise<{ confirmedRound: bigint }> {
  // The shape `execute()` used before #103: target app and box, and nothing
  // a simulate pass would have discovered. Built by hand, not by calling the
  // real `execute()`, so this keeps proving the regression even after the
  // fixed shape changes.
  const suggestedParams = await algod.getTransactionParams().do();
  suggestedParams.fee = BigInt(EXECUTE_FEE);
  suggestedParams.flatFee = true;
  const composer = new algosdk.AtomicTransactionComposer();
  composer.addMethodCall({
    appID: keeperAppId,
    method: keeperMethod('execute'),
    sender: signing.sender,
    signer: signing.signer,
    suggestedParams,
    methodArgs: [upkeepId],
    boxes: [{ appIndex: 0, name: upkeepBoxName(upkeepId) }],
    appForeignApps: [probeAppId],
  });
  const result = await composer.execute(algod, 6);
  return { confirmedRound: result.confirmedRound };
}

try {
  const result =
    mode === 'fixed'
      ? await execute(algod, keeperAppId, signing, { id: upkeepId, targetApp: BigInt(probeAppId), feeAsset: 0n })
      : await naiveExecute();
  console.log(JSON.stringify({ ok: true, mode, confirmedRound: result.confirmedRound.toString() }));
} catch (error) {
  console.log(JSON.stringify({ ok: false, mode, error: error instanceof Error ? error.message : String(error) }));
  process.exit(1);
}
