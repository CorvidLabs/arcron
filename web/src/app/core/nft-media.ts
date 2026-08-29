/**
 * Pictures for Algorand NFTs, from asset params the console already reads.
 *
 * Corvid TestNet tokens are ARC-19: the URL is a template and the CID lives
 * in the reserve address. The console still has no indexer; this fetches the
 * metadata JSON (and then the image) from a public IPFS gateway.
 *
 * The collection sample is Corvid #0001 (ASA 746557513), the first numbered
 * TestNet Nevermore from the minter — not a later junk mint, and not the
 * brand mascot.
 */

import algosdk from 'algosdk';

import { CORVID_TESTNET_NFT } from '@corvidlabs/arcron/rain';

const GATEWAYS = [
  'https://ipfs.io/ipfs/',
  'https://dweb.link/ipfs/',
  'https://nftstorage.link/ipfs/',
] as const;

const ARC19_RAW_SHA256 = /template-ipfs:\/\/\{ipfscid:1:raw:reserve:sha2-256\}/i;

/**
 * Live image of ASA 746557513, “Corvid #0001”.
 *
 * Copied into the console so the hub does not depend on a public gateway.
 * IPFS: bafkreid7k45bewwpoo4ws7azdndqbf2qxumnv7jgz5ihmwxmzwgwvxxppe
 */
export const CORVID_TESTNET_NFT_IMAGE = 'brand/corvid-0001.png';

export function knownNftImage(id: number): string | null {
  return id === CORVID_TESTNET_NFT ? CORVID_TESTNET_NFT_IMAGE : null;
}

export function encodeCidV1RawSha256(digest: Uint8Array): string {
  const cid = new Uint8Array(36);
  cid.set([0x01, 0x55, 0x12, 0x20]);
  cid.set(digest, 4);
  return `b${base32(cid)}`;
}

function base32(data: Uint8Array): string {
  const alphabet = 'abcdefghijklmnopqrstuvwxyz234567';
  let bits = 0;
  let value = 0;
  let out = '';
  for (const byte of data) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      out += alphabet[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += alphabet[(value << (5 - bits)) & 31];
  return out;
}

export function cidFromArc19Reserve(reserve: string): string | null {
  try {
    const digest = algosdk.decodeAddress(reserve).publicKey;
    if (digest.length !== 32) return null;
    return encodeCidV1RawSha256(digest);
  } catch {
    return null;
  }
}

export function ipfsToHttp(uri: string, gateway: string = GATEWAYS[0]): string {
  const trimmed = uri.trim();
  if (trimmed.startsWith('ipfs://')) {
    return `${gateway}${trimmed.slice('ipfs://'.length).replace(/^ipfs\//, '')}`;
  }
  if (/^bafk|^bafy|^Qm[1-9A-HJ-NP-Za-km-z]{44,}/.test(trimmed)) return `${gateway}${trimmed}`;
  return trimmed;
}

export function looksLikeImage(url: string): boolean {
  return /\.(png|jpe?g|gif|webp|svg)(\?|$)/i.test(url) || url.startsWith('data:image/');
}

export async function resolveNftImage(asset: {
  url: string;
  reserve: string;
  id?: number;
}): Promise<string | null> {
  const url = asset.url.trim();
  if (url.length === 0) return knownNftImage(asset.id ?? 0);
  if (ARC19_RAW_SHA256.test(url) && asset.reserve.length > 0) {
    const cid = cidFromArc19Reserve(asset.reserve);
    if (cid === null) return knownNftImage(asset.id ?? 0);
    const resolved = await imageFromCid(cid);
    return resolved ?? knownNftImage(asset.id ?? 0);
  }
  if (looksLikeImage(url)) return ipfsToHttp(url);
  const http = ipfsToHttp(url);
  if (http.startsWith('http://') || http.startsWith('https://')) {
    const resolved = await imageFromMetadata(http);
    if (resolved !== null) return resolved;
  }
  return knownNftImage(asset.id ?? 0);
}

async function imageFromCid(cid: string): Promise<string | null> {
  for (const gateway of GATEWAYS) {
    const found = await imageFromMetadata(`${gateway}${cid}`);
    if (found !== null) return found;
  }
  return null;
}

async function imageFromMetadata(url: string): Promise<string | null> {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(6_000) });
    if (!response.ok) return null;
    const type = response.headers.get('content-type') ?? '';
    if (type.startsWith('image/')) return url;
    const body: unknown = await response.json();
    if (body === null || typeof body !== 'object') return null;
    const image = (body as { image?: unknown }).image;
    if (typeof image !== 'string' || image.length === 0) return null;
    return ipfsToHttp(image);
  } catch {
    return null;
  }
}
