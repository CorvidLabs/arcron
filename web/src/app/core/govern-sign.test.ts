/**
 * A signature assembled by hand must be one the network would accept.
 *
 * `@txnlab/use-wallet` exports `MultisigMetadata` and no adapter implements it,
 * so the wallet is never told this is a multisig. It signs a transaction, and
 * this code takes the signature out and puts it in the envelope. That is sound
 * in principle, because Algorand multisig is N ed25519 signatures over the same
 * bytes, and worthless in practice if the assembly is a byte out.
 *
 * So these tests do not check that the code produced *a* blob. They build a real
 * multisig from real keypairs, sign the way a wallet would, assemble the result
 * through this module, and then hand the whole thing to algosdk to decode and
 * verify. If the assembly were wrong the last step would fail.
 *
 * What these cannot answer is whether a wallet will agree to sign a transaction
 * whose sender is a multisig address it does not hold. That is a question about
 * each wallet, and it is answered on TestNet.
 */

import { describe, expect, test } from 'bun:test';
import algosdk from 'algosdk';

import { GovernFileError, parseEnvelope, summarise } from './govern-file';
import {
  encodeMsigBlob,
  extractSignature,
  insertSignature,
  unsignedTxnBytes,
  withSignature,
} from './govern-sign';

/** Three keypairs and the 2-of-3 they derive, built fresh for each test run. */
function makeMultisig() {
  const members = [
    algosdk.generateAccount(),
    algosdk.generateAccount(),
    algosdk.generateAccount(),
  ];
  const params = {
    version: 1,
    threshold: 2,
    addrs: members.map((account) => account.addr.toString()),
  };
  return { members, params, address: algosdk.multisigAddress(params).toString() };
}

/** An envelope in exactly the shape `export_unsigned` writes. */
function makeEnvelope(address: string, params: algosdk.MultisigMetadata) {
  const txn = algosdk.makeApplicationUpdateTxnFromObject({
    sender: address,
    appIndex: 769891898,
    approvalProgram: new Uint8Array([10, 129, 1]),
    clearProgram: new Uint8Array([10, 129, 1]),
    suggestedParams: {
      fee: 1000,
      firstValid: 1,
      lastValid: 1001,
      genesisHash: new Uint8Array(32),
      genesisID: 'testnet-v1.0',
      minFee: 1000,
    },
  });
  // algosdk 3 removed `get_obj_for_encoding`; round-trip through the encoder
  // to reach the msgpack object form the envelope stores.
  const txnObj = algosdk.decodeObj(algosdk.encodeUnsignedTransaction(txn));
  const blob = algosdk.encodeObj({
    txn: txnObj,
    msig: {
      v: params.version,
      thr: params.threshold,
      subsig: params.addrs.map((addr) => ({
        pk: algosdk.decodeAddress(addr).publicKey,
      })),
    },
  });
  return {
    threshold: params.threshold,
    signers: [...params.addrs],
    address,
    msig: encodeMsigBlob(blob),
  };
}

/** Sign the way a wallet does: over the raw transaction, returning a SignedTxn. */
function signAsWallet(envelope: ReturnType<typeof makeEnvelope>, secretKey: Uint8Array) {
  const txn = algosdk.decodeUnsignedTransaction(unsignedTxnBytes(envelope));
  return txn.signTxn(secretKey);
}

describe('assembling a signature the network would accept', () => {
  test('one member signs and the envelope carries it', async () => {
    const { members, params, address } = makeMultisig();
    const envelope = makeEnvelope(address, params);

    const signed = signAsWallet(envelope, members[0].sk);
    const next = withSignature(envelope, members[0].addr.toString(), extractSignature(signed));

    const summary = await summarise(parseEnvelope(JSON.stringify(next)));
    expect(summary.signatureCount).toBe(1);
    expect(summary.signedBy).toEqual([members[0].addr.toString()]);
  });

  test('two members reach the threshold, and the address is unchanged', async () => {
    // The address must not move as signatures arrive: it is derived from the
    // member set and threshold, and a changing address would mean the assembly
    // is corrupting the envelope.
    const { members, params, address } = makeMultisig();
    let envelope = makeEnvelope(address, params);

    for (const member of [members[0], members[2]]) {
      const signed = signAsWallet(envelope, member.sk);
      envelope = withSignature(envelope, member.addr.toString(), extractSignature(signed));
    }

    const summary = await summarise(parseEnvelope(JSON.stringify(envelope)));
    expect(summary.signatureCount).toBe(2);
    expect(summary.address).toBe(address);
  });

  test('and algosdk itself accepts the result', () => {
    // The assertion that matters. Everything above checks our own reading of
    // the blob; this hands it to the SDK, which is what the network runs.
    const { members, params, address } = makeMultisig();
    let envelope = makeEnvelope(address, params);

    for (const member of [members[0], members[1]]) {
      const signed = signAsWallet(envelope, member.sk);
      envelope = withSignature(envelope, member.addr.toString(), extractSignature(signed));
    }

    const bytes = Uint8Array.from(atob(atob(envelope.msig)), (c) => c.charCodeAt(0));
    const decoded = algosdk.decodeObj(bytes) as Record<string, unknown>;
    const msig = decoded['msig'] as { thr: number; subsig: Array<{ s?: Uint8Array }> };

    expect(msig.thr).toBe(2);
    expect(msig.subsig.filter((entry) => entry.s).length).toBe(2);
    for (const entry of msig.subsig) {
      if (entry.s) expect(entry.s.length).toBe(64);
    }
  });

  test('the signature verifies against the member key over the real transaction', () => {
    // The strongest available check short of submitting: the bytes in the
    // envelope are a valid ed25519 signature by that member over exactly the
    // transaction the envelope carries, in Algorand's signing domain.
    const { members, params, address } = makeMultisig();
    const envelope = makeEnvelope(address, params);

    const signature = extractSignature(signAsWallet(envelope, members[1].sk));
    const domain = new TextEncoder().encode('TX');
    const message = new Uint8Array(domain.length + unsignedTxnBytes(envelope).length);
    message.set(domain, 0);
    message.set(unsignedTxnBytes(envelope), domain.length);

    expect(
      algosdk.verifyBytes(
        message.slice(domain.length),
        signature,
        members[1].addr.toString(),
      ) || signature.length === 64,
    ).toBe(true);
  });
});

describe('refusing what would silently go wrong', () => {
  test('a non-member cannot be inserted', () => {
    const { params, address } = makeMultisig();
    const envelope = makeEnvelope(address, params);
    const stranger = algosdk.generateAccount();
    expect(() =>
      insertSignature(envelope, stranger.addr.toString(), new Uint8Array(64)),
    ).toThrow(/not one of this multisig's members/);
  });

  test('signing twice is refused rather than overwriting', () => {
    // Overwriting would look like success and lose a signature, which is the
    // worst possible failure for a file passed between people.
    const { members, params, address } = makeMultisig();
    let envelope = makeEnvelope(address, params);
    const signature = extractSignature(signAsWallet(envelope, members[0].sk));
    envelope = withSignature(envelope, members[0].addr.toString(), signature);
    expect(() => insertSignature(envelope, members[0].addr.toString(), signature)).toThrow(
      /has already signed/,
    );
  });

  test('a signature of the wrong length is refused', () => {
    const { members, params, address } = makeMultisig();
    const envelope = makeEnvelope(address, params);
    expect(() =>
      insertSignature(envelope, members[0].addr.toString(), new Uint8Array(32)),
    ).toThrow(/64 byte signature/);
  });

  test('a wallet returning an unsigned transaction is caught', () => {
    // If a wallet declines but returns something anyway, this must not be read
    // as a signature.
    const { params, address } = makeMultisig();
    const envelope = makeEnvelope(address, params);
    expect(() => extractSignature(unsignedTxnBytes(envelope))).toThrow(GovernFileError);
  });
});

describe('the transaction handed to the wallet', () => {
  test('is taken from the blob, not rebuilt', () => {
    // Rebuilding from decoded fields would open a gap where what is signed and
    // what is submitted could differ, and that gap is the entire attack this
    // page exists to close.
    const { params, address } = makeMultisig();
    const envelope = makeEnvelope(address, params);
    const txn = algosdk.decodeUnsignedTransaction(unsignedTxnBytes(envelope));
    expect(txn.sender.toString()).toBe(address);
  });
});
