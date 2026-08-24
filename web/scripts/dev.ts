/**
 * LocalNet helpers for driving the console by hand.
 *
 *   bun run scripts/dev.ts poke 120            advance N rounds (dev mode makes a block per txn)
 *   bun run scripts/dev.ts seed <keeper> <target>  register upkeeps at hour and day cadences
 */
import algosdk from 'algosdk';
import { methodSelector, PULSE_TICK_SIGNATURE } from '../src/app/core/keeper-abi';
import { register } from '../src/app/core/keeper-txns';

const algod = new algosdk.Algodv2('a'.repeat(64), 'http://localhost', 4001);
const kmd = new algosdk.Kmd('a'.repeat(64), 'http://localhost', 4002);

/** The account the console signs with: the richest one KMD knows about. */
async function richestAccount() {
  const { wallets } = await kmd.listWallets();
  let best: { address: string; handle: string; amount: bigint } | null = null;
  for (const wallet of wallets) {
    const handle = (await kmd.initWalletHandle(wallet.id, '')).wallet_handle_token;
    for (const address of (await kmd.listKeys(handle)).addresses as string[]) {
      const { amount } = await algod.accountInformation(address).do();
      if (best === null || amount > best.amount) best = { address, handle, amount };
    }
  }
  if (best === null) throw new Error('no KMD accounts');
  return best;
}

const [command, ...rest] = process.argv.slice(2);
const account = await richestAccount();
const signer: algosdk.TransactionSigner = async (group, indexes) =>
  Promise.all(
    indexes.map(async (i) => new Uint8Array(await kmd.signTransaction(account.handle, '', group[i]))),
  );

if (command === 'poke') {
  const count = Number(rest[0] ?? 1);
  const start = (await algod.status().do()).lastRound;
  for (let i = 0; i < count; i++) {
    const txn = algosdk.makePaymentTxnWithSuggestedParamsFromObject({
      sender: account.address,
      receiver: account.address,
      amount: 0,
      suggestedParams: await algod.getTransactionParams().do(),
      note: new TextEncoder().encode(`arcron: advance ${i}`),
    });
    await algod.sendRawTransaction(await kmd.signTransaction(account.handle, '', txn)).do();
  }
  const end = (await algod.status().do()).lastRound;
  console.log(`advanced ${end - start} rounds: ${start} → ${end}`);
} else if (command === 'seed') {
  const [keeperAppId, targetApp] = rest.map(Number);
  // 2.8 s/round: an hourly and a daily upkeep, funded for a week and a month.
  const plans = [
    { label: 'hourly', intervalRounds: 1_286, funding: 4_000 * 24 * 7 },
    { label: 'daily', intervalRounds: 30_857, funding: 4_000 * 30 },
  ];
  for (const plan of plans) {
    const result = await register(algod, keeperAppId, { sender: account.address, signer }, {
      targetApp,
      callData: methodSelector(PULSE_TICK_SIGNATURE),
      intervalRounds: plan.intervalRounds,
      feePerExecution: 4_000,
      funding: plan.funding,
    });
    console.log(`registered ${plan.label} upkeep ${result.returnValue} (every ${plan.intervalRounds} rounds)`);
  }
} else {
  console.error('usage: dev.ts poke <n> | seed <keeperAppId> <targetAppId>');
  process.exit(1);
}
