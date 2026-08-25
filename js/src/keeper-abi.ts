/**
 * The keeper app's ABI surface, as method signatures.
 *
 * Kept as signatures rather than importing the generated ARC-56 artifact so
 * the web app has no build-time coupling to the Python toolchain;
 * `keeper-abi.test.ts` checks them against that artifact so drift is caught
 * in CI instead of at runtime.
 */

import algosdk from 'algosdk';

export const KEEPER_METHOD_SIGNATURES = {
  register:
    'register(pay,pay,uint64,byte[][],uint64,uint64,uint64,uint64,uint64,uint64)uint64',
  topUp: 'top_up(uint64,pay)uint64',
  cancel: 'cancel(uint64)uint64',
  execute: 'execute(uint64)uint64',
  optInAsset: 'opt_in_asset(pay,uint64,uint64)uint64',
  topUpAsset: 'top_up_asset(uint64,axfer)uint64',
} as const;

export type KeeperMethodName = keyof typeof KEEPER_METHOD_SIGNATURES;

export function keeperMethod(name: KeeperMethodName): algosdk.ABIMethod {
  return algosdk.ABIMethod.fromSignature(KEEPER_METHOD_SIGNATURES[name]);
}

/** The selector a target app's hook is called with, e.g. `tick()uint64`. */
export function methodSelector(signature: string): Uint8Array {
  return algosdk.ABIMethod.fromSignature(signature).getSelector();
}

/** The demo target's hook, the default when registering an upkeep. */
export const PULSE_TICK_SIGNATURE = 'tick()uint64';

/**
 * The app args a call to `signature` needs: its selector, then each argument
 * ARC-4 encoded. `values` are the arguments as typed by a person, one string
 * per argument, in order.
 *
 * Throws with a message worth showing if the signature will not parse or a
 * value will not encode as its declared type.
 */
export function encodeCall(signature: string, values: readonly string[]): Uint8Array[] {
  const method = algosdk.ABIMethod.fromSignature(signature);
  if (values.length !== method.args.length) {
    throw new Error(
      `${signature} takes ${method.args.length} argument(s), got ${values.length}`,
    );
  }
  return [
    method.getSelector(),
    ...method.args.map((arg, index) => {
      const type = arg.type;
      if (typeof type === 'string') throw new Error(`${arg.name ?? index}: ${type} is not an ABI value type`);
      try {
        return type.encode(parseAbiValue(String(type), values[index]));
      } catch (cause) {
        throw new Error(`argument ${index + 1} (${type}): ${(cause as Error).message}`);
      }
    }),
  ];
}

/** A typed ABI value from what someone typed into a text box. */
function parseAbiValue(type: string, raw: string): algosdk.ABIValue {
  const text = raw.trim();
  if (type === 'bool') {
    if (text === 'true' || text === 'false') return text === 'true';
    throw new Error('expected true or false');
  }
  if (type.startsWith('uint') || type === 'byte') {
    if (!/^\d+$/.test(text)) throw new Error('expected a whole number');
    return BigInt(text);
  }
  if (type === 'string') return text;
  if (type === 'address') {
    if (!algosdk.isValidAddress(text)) throw new Error('not a valid address');
    return text;
  }
  if (type === 'byte[]' || /^byte\[\d+\]$/.test(type)) {
    const hex = text.startsWith('0x') ? text.slice(2) : text;
    if (hex.length % 2 !== 0 || !/^[0-9a-fA-F]*$/.test(hex)) throw new Error('expected hex bytes');
    return Uint8Array.from(hex.match(/.{2}/g) ?? [], (byte) => parseInt(byte, 16));
  }
  throw new Error(`${type} is not supported here; encode it as byte[] hex`);
}
