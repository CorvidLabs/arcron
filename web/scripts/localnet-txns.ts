/**
 * Drive the console's own transaction builders against LocalNet, headlessly.
 *
 * Same code path the browser uses (src/app/core/keeper-txns.ts), without the
 * browser: register → top up → wait for the due round → execute → cancel,
 * checking Pulse's counter moves and the refund comes back.
 *
 *   bun run scripts/localnet-txns.ts <keeperAppId> <targetAppId>
 */
import algosdk from 'algosdk';
import { cancel, execute, nextUpkeepId, register, topUp } from '../src/app/core/keeper-txns';
import { methodSelector, PULSE_TICK_SIGNATURE } from '../src/app/core/keeper-abi';
import { decodeUpkeep, upkeepBoxName } from '../src/app/core/upkeep';

const [keeperAppId, pulseAppId] = [Number(process.argv[2]), Number(process.argv[3])];
const algod = new algosdk.Algodv2('a'.repeat(64), 'http://localhost', 4001);
const kmd = new algosdk.Kmd('a'.repeat(64), 'http://localhost', 4002);

const wallets = await kmd.listWallets();
const handle = (await kmd.initWalletHandle(wallets.wallets[0].id, '')).wallet_handle_token;
const addresses: string[] = (await kmd.listKeys(handle)).addresses;
const balances = await Promise.all(addresses.map(async (a) => (await algod.accountInformation(a).do()).amount));
const sender = addresses[balances.indexOf(balances.reduce((m, b) => (b > m ? b : m), 0n))];
const signer: algosdk.TransactionSigner = async (group, indexes) =>
  Promise.all(indexes.map(async (i) => new Uint8Array(await kmd.signTransaction(handle, '', group[i]))));
const signing = { sender, signer };
console.log('sender:', sender);

const expectedId = await nextUpkeepId(algod, keeperAppId);
const registered = await register(algod, keeperAppId, signing, {
  targetApp: pulseAppId,
  callData: methodSelector(PULSE_TICK_SIGNATURE),
  intervalRounds: 10,
  feePerExecution: 4_000,
  funding: 12_000,
});
console.log('register →', registered.returnValue, '(expected', expectedId, ') round', registered.confirmedRound);
const id = registered.returnValue as bigint;

const read = async () =>
  decodeUpkeep(id, (await algod.getApplicationBoxByName(keeperAppId, upkeepBoxName(id)).do()).value);
console.log('box:', await read());

console.log('top_up →', (await topUp(algod, keeperAppId, signing, id, 4_000)).returnValue);

// Advance to the due round (LocalNet is dev mode: one block per transaction).
const due = (await read()).nextExecutionRound;
const sp = await algod.getTransactionParams().do();
while ((await algod.status().do()).lastRound < due) {
  const txn = algosdk.makePaymentTxnWithSuggestedParamsFromObject({
    sender, receiver: sender, amount: 0, suggestedParams: await algod.getTransactionParams().do(),
  });
  await algod.sendRawTransaction(await kmd.signTransaction(handle, '', txn)).do();
}

const beatsBefore = (await algod.getApplicationByID(pulseAppId).do()).params.globalState
  ?.find((s) => new TextDecoder().decode(s.key) === 'beats')?.value.uint;
const executed = await execute(algod, keeperAppId, signing, { id, targetApp: BigInt(pulseAppId) });
const beatsAfter = (await algod.getApplicationByID(pulseAppId).do()).params.globalState
  ?.find((s) => new TextDecoder().decode(s.key) === 'beats')?.value.uint;
console.log('execute → next due', executed.returnValue, '| Pulse.beats', beatsBefore, '→', beatsAfter);
console.log('box after execute:', await read());

console.log('cancel → refunded', (await cancel(algod, keeperAppId, signing, id)).returnValue, 'µALGO');
const boxes = await algod.getApplicationBoxes(keeperAppId).do();
console.log('boxes remaining:', boxes.boxes.length);
