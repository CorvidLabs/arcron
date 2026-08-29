import { describe, expect, test } from 'bun:test';

import { CORVID_TESTNET_NFT } from '@corvidlabs/arcron/rain';
import {
  cidFromArc19Reserve,
  CORVID_TESTNET_NFT_IMAGE,
  encodeCidV1RawSha256,
  ipfsToHttp,
  knownNftImage,
  looksLikeImage,
} from './nft-media';

describe('ARC-19 reserve → CID', () => {
  test('the live Corvid TestNet token round-trips to its published CID', () => {
    // ASA 746557513 Corvid #0001, reserve from algod, CID from ipfs.io metadata.
    expect(
      cidFromArc19Reserve('PAKJX6IMST6EQ74THHF6PRSW6UBVYADUQQNV6MWDIJX5SSBW5TBBXZQLSA'),
    ).toBe('bafkreidycsn7sdeu7reh7ezzzpt4mvxvanoaa5eednptfq2cn7muqnxmyi');
  });

  test('a garbage reserve is nothing, not a throw', () => {
    expect(cidFromArc19Reserve('not-an-address')).toBeNull();
  });

  test('the CID prefix is CIDv1 + raw + sha2-256', () => {
    const digest = new Uint8Array(32);
    digest[0] = 0xaa;
    expect(encodeCidV1RawSha256(digest).startsWith('bafkrei')).toBe(true);
  });
});

describe('ipfsToHttp', () => {
  test('rewrites ipfs:// onto the public gateway', () => {
    expect(ipfsToHttp('ipfs://bafkreiabc')).toBe('https://ipfs.io/ipfs/bafkreiabc');
  });

  test('leaves https alone', () => {
    expect(ipfsToHttp('https://example.test/x.png')).toBe('https://example.test/x.png');
  });
});

describe('looksLikeImage', () => {
  test('a png path is an image, a json path is not', () => {
    expect(looksLikeImage('https://x.test/a.png')).toBe(true);
    expect(looksLikeImage('https://x.test/meta.json')).toBe(false);
  });
});

describe('the collection sample image', () => {
  test('is the live Nevermore PNG, not the mascot', () => {
    expect(knownNftImage(CORVID_TESTNET_NFT)).toBe(CORVID_TESTNET_NFT_IMAGE);
    expect(CORVID_TESTNET_NFT_IMAGE).toBe('brand/corvid-0001.png');
    expect(CORVID_TESTNET_NFT_IMAGE).not.toContain('mascot');
    expect(knownNftImage(1)).toBeNull();
  });
});
