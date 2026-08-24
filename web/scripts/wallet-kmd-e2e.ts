/**
 * Prove the console's wallet path end to end, headlessly.
 *
 * Builds the same use-wallet manager the app builds, connects the KMD wallet,
 * and drives a real register → cancel through the app's own transaction
 * builders using the manager's signer — so what is exercised is exactly what
 * the browser does, minus the browser.
 *
 *   bun run scripts/wallet-kmd-e2e.ts <keeperAppId> <targetAppId>
 */
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost:4200/' });
const globals = globalThis as Record<string, unknown>;
globals['window'] = dom.window;
globals['document'] = dom.window.document;
globals['navigator'] = dom.window.navigator;
globals['localStorage'] = dom.window.localStorage;

const algosdk = (await import('algosdk')).default;
const { WalletManager } = await import('@txnlab/use-wallet');
const { walletsFor, managerNetworks } = await import('../src/app/core/wallets');
const { register, cancel, nextUpkeepId } = await import('../src/app/core/keeper-txns');
const { methodSelector, PULSE_TICK_SIGNATURE } = await import('../src/app/core/keeper-abi');
const { decodeUpkeep, upkeepBoxName } = await import('../src/app/core/upkeep');

const [keeperAppId, targetApp] = process.argv.slice(2).map(Number);
const manager = new WalletManager({
  wallets: walletsFor('localnet'),
  networks: managerNetworks(),
  defaultNetwork: 'localnet',
  options: { persistNetwork: false },
});

console.log('picker offers:', manager.wallets.map((w) => w.metadata?.name ?? String(w.id)).join(', '));

const kmdWallet = manager.wallets.find((w) => String(w.id) === 'kmd');
if (!kmdWallet) throw new Error('KMD wallet missing');
await kmdWallet.connect();
const sender = manager.activeAddress;
if (!sender) throw new Error('no active address after connect');
console.log('connected:', kmdWallet.metadata?.name, '→', sender);
console.log('accounts exposed:', manager.activeWalletAddresses?.length);

const signing = { sender, signer: manager.transactionSigner };
const algod = new algosdk.Algodv2('a'.repeat(64), 'http://localhost', 4001);

const expected = await nextUpkeepId(algod, keeperAppId);
const registered = await register(algod, keeperAppId, signing, {
  targetApp,
  callData: methodSelector(PULSE_TICK_SIGNATURE),
  intervalRounds: 10,
  feePerExecution: 4_000,
  funding: 12_000,
});
const id = registered.returnValue as bigint;
console.log(`register → upkeep ${id} (expected ${expected}), round ${registered.confirmedRound}`);

const box = await algod.getApplicationBoxByName(keeperAppId, upkeepBoxName(id)).do();
const upkeep = decodeUpkeep(id, box.value);
console.log('box creator matches the connected wallet:', upkeep.creator === sender);

const refunded = await cancel(algod, keeperAppId, signing, id);
console.log(`cancel → refunded ${refunded.returnValue} µALGO`);
await manager.disconnect();
console.log('disconnected cleanly');
