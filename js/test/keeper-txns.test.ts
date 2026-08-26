/**
 * `foldUnnamedResources` is the part of #103's fix that has no network in it:
 * folding a simulate response's `unnamedResourcesAccessed` into the resource
 * arrays `execute()` attaches. `discoverResources` (the half that actually
 * calls algod) is proved against a real LocalNet chain instead, in
 * `scripts/spike_js_execute_resources.py` via `js/scripts/execute-probe.ts`,
 * because folding logic is exactly the kind of thing worth pinning here and
 * a chain round trip is not.
 */

import { describe, expect, test } from 'bun:test';
import algosdk from 'algosdk';

import { foldUnnamedResources, type ResourceRefs } from '../src/keeper-txns';

const CALLING_APP_ID = 111;

const ACCOUNT_A = algosdk.generateAccount().addr.toString();
const ACCOUNT_B = algosdk.generateAccount().addr.toString();

function known(overrides: Partial<ResourceRefs> = {}): ResourceRefs {
  return {
    appAccounts: [],
    appForeignApps: [222],
    appForeignAssets: [],
    boxes: [{ appIndex: 0, name: new Uint8Array([1]) }],
    ...overrides,
  };
}

describe('foldUnnamedResources', () => {
  test('returns the known set unchanged when nothing was discovered', () => {
    const base = known();
    expect(foldUnnamedResources(base, undefined, CALLING_APP_ID)).toEqual(base);
  });

  test('adds a discovered account', () => {
    const unnamed = new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
      accounts: [ACCOUNT_A],
    });
    const result = foldUnnamedResources(known(), unnamed, CALLING_APP_ID);
    expect(result.appAccounts).toEqual([ACCOUNT_A]);
  });

  test('drops the zero address rather than declaring it', () => {
    const unnamed = new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
      accounts: [algosdk.Address.zeroAddress().toString()],
    });
    const result = foldUnnamedResources(known(), unnamed, CALLING_APP_ID);
    expect(result.appAccounts).toEqual([]);
  });

  test('adds a discovered app, but not the calling app itself', () => {
    const unnamed = new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
      apps: [333, CALLING_APP_ID],
    });
    const result = foldUnnamedResources(known(), unnamed, CALLING_APP_ID);
    expect(result.appForeignApps).toEqual([222, 333]);
  });

  test('adds a discovered asset', () => {
    const unnamed = new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
      assets: [444],
    });
    const result = foldUnnamedResources(known(), unnamed, CALLING_APP_ID);
    expect(result.appForeignAssets).toEqual([444]);
  });

  test('a box under the calling app collapses to appIndex 0', () => {
    const unnamed = new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
      boxes: [new algosdk.modelsv2.BoxReference({ app: CALLING_APP_ID, name: new Uint8Array([9]) })],
    });
    const result = foldUnnamedResources(known(), unnamed, CALLING_APP_ID);
    expect(result.boxes.at(-1)).toEqual({ appIndex: 0, name: new Uint8Array([9]) });
  });

  test('a box under a foreign app keeps its app id, and that app is declared too', () => {
    const unnamed = new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
      boxes: [new algosdk.modelsv2.BoxReference({ app: 555, name: new Uint8Array([9]) })],
    });
    const result = foldUnnamedResources(known(), unnamed, CALLING_APP_ID);
    expect(result.boxes.at(-1)).toEqual({ appIndex: 555, name: new Uint8Array([9]) });
    expect(result.appForeignApps).toContain(555);
  });

  test('an asset holding declares both the account and the asset', () => {
    const unnamed = new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
      assetHoldings: [new algosdk.modelsv2.AssetHoldingReference({ account: ACCOUNT_A, asset: 444 })],
    });
    const result = foldUnnamedResources(known(), unnamed, CALLING_APP_ID);
    expect(result.appAccounts).toEqual([ACCOUNT_A]);
    expect(result.appForeignAssets).toEqual([444]);
  });

  test('a local state reference declares both the account and the app', () => {
    const unnamed = new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
      appLocals: [new algosdk.modelsv2.ApplicationLocalReference({ account: ACCOUNT_B, app: 333 })],
    });
    const result = foldUnnamedResources(known(), unnamed, CALLING_APP_ID);
    expect(result.appAccounts).toEqual([ACCOUNT_B]);
    expect(result.appForeignApps).toEqual([222, 333]);
  });

  test('extra box references become that many empty box references', () => {
    const unnamed = new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
      extraBoxRefs: 2,
    });
    const result = foldUnnamedResources(known(), unnamed, CALLING_APP_ID);
    const added = result.boxes.slice(1);
    expect(added).toHaveLength(2);
    for (const box of added) {
      expect(box).toEqual({ appIndex: 0, name: new Uint8Array(0) });
    }
  });

  test('does not add the same account or app twice', () => {
    const base = known({ appAccounts: [ACCOUNT_A] });
    const unnamed = new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
      accounts: [ACCOUNT_A],
      apps: [222],
    });
    const result = foldUnnamedResources(base, unnamed, CALLING_APP_ID);
    expect(result.appAccounts).toEqual([ACCOUNT_A]);
    expect(result.appForeignApps).toEqual([222]);
  });

  test('folds every category from one response at once', () => {
    const unnamed = new algosdk.modelsv2.SimulateUnnamedResourcesAccessed({
      accounts: [ACCOUNT_A],
      apps: [333],
      assets: [444],
      boxes: [new algosdk.modelsv2.BoxReference({ app: CALLING_APP_ID, name: new Uint8Array([9]) })],
      assetHoldings: [new algosdk.modelsv2.AssetHoldingReference({ account: ACCOUNT_B, asset: 666 })],
      extraBoxRefs: 1,
    });
    const result = foldUnnamedResources(known(), unnamed, CALLING_APP_ID);
    expect(result.appAccounts).toEqual([ACCOUNT_A, ACCOUNT_B]);
    expect(result.appForeignApps).toEqual([222, 333]);
    expect(result.appForeignAssets).toEqual([444, 666]);
    // The known box, the calling-app box (collapsed to 0), one extra ref.
    expect(result.boxes).toHaveLength(3);
  });
});
