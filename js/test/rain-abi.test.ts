/** The signatures the Rain UI calls must match the compiled contract. */

import { describe, expect, test } from 'bun:test';

import { RAIN_METHOD_SIGNATURES } from '../src/rain-abi';
import { qualifies, TICKET_MBR, ZERO_ADDRESS, decodeRainState } from '../src/rain';

const arc56 = await Bun.file(
  new URL('../../smart_contracts/artifacts/rain/Rain.arc56.json', import.meta.url),
).json();

function signatureOf(method: { name: string; args: { type: string }[]; returns: { type: string } }) {
  return `${method.name}(${method.args.map((arg) => arg.type).join(',')})${method.returns.type}`;
}

describe('rain ABI', () => {
  const onChain = new Set(arc56.methods.map(signatureOf));

  test.each(Object.entries(RAIN_METHOD_SIGNATURES))('%s matches the artifact', (_name, signature) => {
    expect(onChain).toContain(signature);
  });

  test('covers every method the contract exposes', () => {
    expect(new Set(Object.values(RAIN_METHOD_SIGNATURES))).toEqual(onChain);
  });
});

describe('ticket MBR', () => {
  test('matches the Python contract: 2,500 + 400 × 41', () => {
    expect(TICKET_MBR).toBe(18_900);
  });
});

describe('qualifies', () => {
  const gated = {
    gated: true,
    gateCreator: 'WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A',
    gateUnitPrefix: 'corvid',
    prizeAsset: 0n,
  };

  test('a Corvid NFT from the minter counts', () => {
    expect(
      qualifies(gated, {
        creator: gated.gateCreator,
        unitName: 'corvid8',
        id: 746557618,
        amount: 1n,
      }),
    ).toBe(true);
  });

  test('the same minter with a non-corvid unit name does not', () => {
    expect(
      qualifies(gated, {
        creator: gated.gateCreator,
        unitName: 'Test',
        id: 1,
        amount: 1n,
      }),
    ).toBe(false);
  });

  test('someone else minting corvid does not', () => {
    expect(
      qualifies(gated, {
        creator: ZERO_ADDRESS,
        unitName: 'corvid1',
        id: 1,
        amount: 1n,
      }),
    ).toBe(false);
  });

  test('an empty prefix falls back to creator only', () => {
    expect(
      qualifies(
        { ...gated, gateUnitPrefix: '' },
        { creator: gated.gateCreator, unitName: 'Test', id: 1, amount: 1n },
      ),
    ).toBe(true);
  });
});

describe('decodeRainState', () => {
  test('missing keys are zeros, not throws', () => {
    const state = decodeRainState(1, []);
    expect(state.pot).toBe(0n);
    expect(state.tickets).toBe(0n);
    expect(state.gated).toBe(false);
    expect(state.gateCreator).toBe(ZERO_ADDRESS);
  });
});
