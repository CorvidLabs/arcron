/** The signatures the web app calls must match the compiled contract. */

import { describe, expect, test } from 'bun:test';
import { KEEPER_METHOD_SIGNATURES, methodSelector, PULSE_TICK_SIGNATURE } from './keeper-abi';
import { toHex } from './upkeep';

const arc56 = await Bun.file(
  new URL('../../../../smart_contracts/artifacts/keeper/Keeper.arc56.json', import.meta.url),
).json();

function signatureOf(method: { name: string; args: { type: string }[]; returns: { type: string } }) {
  return `${method.name}(${method.args.map((arg) => arg.type).join(',')})${method.returns.type}`;
}

describe('keeper ABI', () => {
  const onChain = new Set(arc56.methods.map(signatureOf));

  test.each(Object.entries(KEEPER_METHOD_SIGNATURES))('%s matches the artifact', (_name, signature) => {
    expect(onChain).toContain(signature);
  });

  test('covers every method the contract exposes', () => {
    expect(new Set(Object.values(KEEPER_METHOD_SIGNATURES))).toEqual(onChain);
  });
});

describe('selectors', () => {
  test('tick()uint64 is the selector stored in the live upkeeps', () => {
    expect(toHex(methodSelector(PULSE_TICK_SIGNATURE))).toBe('4d4d5f0b');
  });
});
