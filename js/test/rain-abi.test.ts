/** The signatures the Rain UI calls must match the compiled contract. */

import { describe, expect, test } from 'bun:test';

import { RAIN_METHOD_SIGNATURES } from '../src/rain-abi';
import {
  CORVID_TESTNET_MINTER,
  CORVID_TESTNET_NFT,
  CORVID_TESTNET_NFT_NAME,
  ONE,
  RAIN_BOX_BYTES,
  RAIN_BOX_MBR,
  SPLIT,
  TICKET_MBR,
  WAVE,
  ZERO_ADDRESS,
  allocationOf,
  decodeHubState,
  decodeLabel,
  decodeRainRec,
  encodeLabel,
  encodeRainRec,
  prizeAssetId,
  prizeLabel,
  qualifies,
  rainBoxName,
  rainIdFromBoxName,
  rainsRemaining,
  sameBoxName,
  ticketBoxName,
  ticketRainIdForHolder,
  waitingReason,
} from '../src/rain';

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

describe('box MBR', () => {
  test('rain box matches 2,500 + 400 × 233', () => {
    expect(RAIN_BOX_MBR).toBe(95_700);
  });

  test('ticket box matches 2,500 + 400 × 65', () => {
    expect(TICKET_MBR).toBe(28_500);
  });
});

describe('rain box names', () => {
  test('name is r plus the id as big-endian uint64', () => {
    const name = rainBoxName(1n);
    expect(name.length).toBe(9);
    expect(name[0]).toBe(0x72);
    expect(rainIdFromBoxName(name)).toBe(1n);
  });
});

describe('ticket box names', () => {
  const sample = 'WGSHC4TYKYBS6EX5V5E377BQDLKWIIPBCFOLZQZIXCKHFIEKRPBFOMW25A';

  test('name is t plus rain id plus the 32-byte public key', () => {
    const name = ticketBoxName(3n, sample);
    expect(name.length).toBe(41);
    expect(name[0]).toBe(0x74);
  });

  test('sameBoxName is length-and-byte equality', () => {
    expect(sameBoxName(ticketBoxName(1n, sample), ticketBoxName(1n, sample))).toBe(true);
    expect(sameBoxName(ticketBoxName(1n, sample), ticketBoxName(2n, sample))).toBe(false);
  });

  test('ticketRainIdForHolder returns the rain only for that address', () => {
    const name = ticketBoxName(7n, sample);
    expect(ticketRainIdForHolder(name, sample)).toBe(7n);
    expect(ticketRainIdForHolder(name, ZERO_ADDRESS)).toBeNull();
    expect(ticketRainIdForHolder(rainBoxName(7n), sample)).toBeNull();
  });
});

describe('label', () => {
  test('round-trips a short name and pads to 32 bytes', () => {
    const encoded = encodeLabel('Corvid daily');
    expect(encoded.length).toBe(32);
    expect(decodeLabel(encoded)).toBe('Corvid daily');
  });
});

describe('RainRec encoding', () => {
  const rec = {
    id: 1n,
    creator: CORVID_TESTNET_MINTER,
    gateCreator: CORVID_TESTNET_MINTER,
    label: 'Corvid daily',
    prizeAsset: 0n,
    drip: 50_000n,
    intervalRounds: 30_857n,
    lastRainRound: 1_000n,
    pot: 1_000_000n,
    tickets: 2n,
    drawId: 3n,
    cumulative: 25_000n,
    mode: SPLIT,
    waveCap: 0n,
    waveCount: 0n,
    lastShare: 0n,
    lastWaveId: 0n,
    waveUnclaimed: 0n,
    commitRound: 0n,
    prizeLocked: 0n,
  };

  test('round-trips through the box layout', () => {
    const raw = encodeRainRec(rec);
    expect(raw.length).toBe(RAIN_BOX_BYTES);
    const decoded = decodeRainRec(1n, raw);
    expect(decoded.creator).toBe(rec.creator);
    expect(decoded.gateCreator).toBe(rec.gateCreator);
    expect(decoded.label).toBe(rec.label);
    expect(decoded.drip).toBe(rec.drip);
    expect(decoded.pot).toBe(rec.pot);
    expect(decoded.mode).toBe(SPLIT);
    expect(decoded.gated).toBe(true);
  });
});

describe('rainsRemaining', () => {
  test('SPLIT is pot divided by the paid slice', () => {
    expect(
      rainsRemaining({ pot: 1_000_000n, drip: 100_000n, tickets: 2n, mode: SPLIT, waveCount: 0n }),
    ).toBe(10n);
  });

  test('is zero when a share cannot be paid', () => {
    expect(
      rainsRemaining({ pot: 50n, drip: 100n, tickets: 3n, mode: SPLIT, waveCount: 0n }),
    ).toBe(0n);
  });

  test('ONE is pot divided by drip', () => {
    expect(
      rainsRemaining({ pot: 500_000n, drip: 100_000n, tickets: 9n, mode: ONE, waveCount: 0n }),
    ).toBe(5n);
  });
});

describe('allocationOf', () => {
  const split = {
    id: 1n,
    creator: ZERO_ADDRESS,
    gateCreator: ZERO_ADDRESS,
    label: '',
    prizeAsset: 0n,
    drip: 100n,
    intervalRounds: 10n,
    lastRainRound: 0n,
    pot: 0n,
    tickets: 1n,
    drawId: 1n,
    cumulative: 50n,
    mode: SPLIT,
    waveCap: 0n,
    waveCount: 0n,
    lastShare: 0n,
    lastWaveId: 0n,
    waveUnclaimed: 0n,
    commitRound: 0n,
    prizeLocked: 0n,
    gated: false,
  };

  test('SPLIT is cumulative minus debt', () => {
    expect(allocationOf(split, { credit: 20n, waveId: 0n, settledId: 0n })).toBe(30n);
  });

  test('WAVE adds last_share when this ticket was in that drop', () => {
    const wave = { ...split, mode: WAVE, lastWaveId: 2n, lastShare: 40n };
    expect(allocationOf(wave, { credit: 5n, waveId: 2n, settledId: 0n })).toBe(45n);
    expect(allocationOf(wave, { credit: 5n, waveId: 2n, settledId: 2n })).toBe(5n);
  });
});

describe('the Corvid collection pin', () => {
  test('the sample NFT is a live TestNet asset, not a logo', () => {
    expect(CORVID_TESTNET_NFT).toBe(746_557_513);
    expect(CORVID_TESTNET_NFT_NAME).toBe('Corvid #0001');
  });
});

describe('qualifies', () => {
  const gated = {
    gated: true,
    gateCreator: CORVID_TESTNET_MINTER,
    prizeAsset: 0n,
  };

  test('a Corvid NFT from the minter counts', () => {
    expect(
      qualifies(
        gated,
        { creator: gated.gateCreator, unitName: 'corvid8', id: 746557618, amount: 1n },
        'corvid',
      ),
    ).toBe(true);
  });

  test('the same minter with a non-corvid unit name does not when prefixed', () => {
    expect(
      qualifies(
        gated,
        { creator: gated.gateCreator, unitName: 'Test', id: 1, amount: 1n },
        'corvid',
      ),
    ).toBe(false);
  });

  test('someone else minting corvid does not', () => {
    expect(
      qualifies(gated, { creator: ZERO_ADDRESS, unitName: 'corvid1', id: 1, amount: 1n }, 'corvid'),
    ).toBe(false);
  });

  test('an empty prefix falls back to creator only', () => {
    expect(
      qualifies(gated, { creator: gated.gateCreator, unitName: 'Test', id: 1, amount: 1n }, ''),
    ).toBe(true);
  });
});

describe('prizeLabel', () => {
  test('ALGO uses the algo formatter', () => {
    expect(prizeLabel(1_000_000n, 0n)).toBe('1 ALGO');
  });

  test('an unnamed ASA stays ASA, a named one uses its unit', () => {
    expect(prizeLabel(1_000n, 770_131_837n, '', 0)).toBe('1,000 ASA');
    expect(prizeLabel(1_000n, 770_131_837n, 'DROP', 0)).toBe('1,000 DROP');
  });

  test('an ASA scales by its own decimals, not by base units', () => {
    // Unscaled, this pot would read as 1,000,000 DROP: a millionfold lure
    // on a permissionless hub.
    expect(prizeLabel(1_000_000n, 770_131_837n, 'DROP', 6)).toBe('1 DROP');
    expect(prizeLabel(1_500_000n, 770_131_837n, 'DROP', 6)).toBe('1.5 DROP');
    expect(prizeLabel(50_000n, 770_131_837n, 'DROP', 6)).toBe('0.05 DROP');
  });

  test('unknown decimals say base units rather than a wrong number', () => {
    expect(prizeLabel(1_000n, 770_131_837n)).toBe('1,000 base units of ASA');
    expect(prizeLabel(1_000n, 770_131_837n, 'DROP')).toBe('1,000 base units of DROP');
  });
});

describe('prizeAssetId', () => {
  test('ALGO is nothing, an ASA is the id from the box', () => {
    expect(prizeAssetId({ prizeAsset: 0n })).toBeNull();
    expect(prizeAssetId({ prizeAsset: 770_131_837n })).toBe('770131837');
  });
});

describe('waitingReason', () => {
  const base = { mode: SPLIT, tickets: 1n, waveCount: 0n, pot: 1_000_000n, drip: 50_000n };

  test('an empty pot is why, not the interval', () => {
    expect(waitingReason({ ...base, pot: 0n })).toBe('pot is empty');
  });

  test('WAVE with nobody checked in says so', () => {
    expect(waitingReason({ ...base, mode: WAVE, waveCount: 0n })).toBe('nobody checked in');
  });

  test('no tickets is the SPLIT/ONE wait', () => {
    expect(waitingReason({ ...base, tickets: 0n })).toBe('no tickets yet');
  });

  test('a funded rain with tickets is not waiting for a reason', () => {
    expect(waitingReason(base)).toBeNull();
  });
});

describe('decodeHubState', () => {
  test('missing keys are zeros, not throws', () => {
    const state = decodeHubState(1, []);
    expect(state.nextRainId).toBe(0n);
    expect(state.bootstrapped).toBe(false);
  });
});

/**
 * Every method the contract exposes either has a way to call it, or an
 * explicit reason it does not.
 *
 * `abandon` was declared in the contract on day one and in
 * `RAIN_METHOD_SIGNATURES` beside it, and had no builder anywhere until
 * 2026-08-31. The ABI conformance tests above all passed the whole time,
 * because a signature matching the artifact says nothing about whether
 * anything can send it. On an immutable hub that gap is not cosmetic: a ONE
 * draw left unresolved past `SEED_WINDOW` can only be freed by `abandon`, so
 * for as long as no builder existed, a stalled rain was stalled for good.
 *
 * The exemptions are the point. A method may be absent from the UI, but
 * somebody has to say so here and why.
 */
describe('every ABI method is reachable', () => {
  const NOT_SENT_FROM_A_UI: Record<string, string> = {
    bootstrap: 'deploy-time, once, from scripts/rain_testnet_deploy.py',
    draw: 'the Arcron hook. A keeper sends it on a schedule; a person never does.',
    allocationOf: 'readonly. The console reads the ticket box directly instead.',
    rainOf: 'readonly. The console reads the rain box directly instead.',
    setRain:
      'creator-only retune of drip and interval, and the one real gap this ' +
      'test found. Every rain with a creator who would use it is ours and is ' +
      'retuned from a script, so nothing has needed it yet. That stops being ' +
      'true the moment somebody else creates a rain.',
  };

  const BUILDER_FOR: Record<string, string> = {};

  test.each(Object.keys(RAIN_METHOD_SIGNATURES))('%s can be sent, or says why not', async (name) => {
    if (name in NOT_SENT_FROM_A_UI) {
      expect(NOT_SENT_FROM_A_UI[name]!.length).toBeGreaterThan(20);
      return;
    }
    const txns = await import('../src/rain-txns');
    const expected = BUILDER_FOR[name] ?? name;
    expect(typeof (txns as Record<string, unknown>)[expected]).toBe('function');
  });

  test('the exemption list names only methods that exist', () => {
    const known = new Set(Object.keys(RAIN_METHOD_SIGNATURES));
    for (const name of Object.keys(NOT_SENT_FROM_A_UI)) expect(known).toContain(name);
  });
});
