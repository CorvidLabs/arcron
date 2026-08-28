/**
 * Everything this app knows, read from a public node.
 *
 * No backend, no indexer, and no connection to the bot. That last one is a
 * deliberate limit rather than an omission: the keeper process holds a hot key,
 * and giving it a listening socket so a web page could ask how it feels would
 * add an attack surface to the one component that must not have one. So this
 * infers what it can from what the keeper *did* on chain, and
 * `keeper-view.ts` is careful about what that does and does not prove.
 */

import { Injectable, signal } from '@angular/core';
import algosdk from 'algosdk';

import { toEntry, type BoardEntry } from '@corvidlabs/arcron/board';
import { NETWORKS, type NetworkKey } from '@corvidlabs/arcron/networks';
import { decodeUpkeep, upkeepIdFromBoxName } from '@corvidlabs/arcron/upkeep';

export interface KeeperAccount {
    readonly address: string;
    readonly balance: bigint;
    /** What it can actually spend: the minimum balance is not a constant. */
    readonly spendable: bigint;
}

export interface Snapshot {
    readonly round: bigint;
    readonly entries: readonly BoardEntry[];
    readonly account: KeeperAccount | null;
    readonly lastSeenRound: bigint | null;
    readonly secondsPerRound: number;
    readonly error: string | null;
}

/** Rounds sampled to measure block time rather than assuming it. */
const BLOCK_TIME_SAMPLE = 1_000n;

@Injectable({ providedIn: 'root' })
export class ChainService {
    readonly snapshot = signal<Snapshot | null>(null);
    readonly loading = signal(false);

    private client(network: NetworkKey): algosdk.Algodv2 {
        const { algod } = NETWORKS[network];
        return new algosdk.Algodv2(algod.token ?? '', algod.server, algod.port ?? '');
    }

    /**
     * Read the registry, the keeper's account, and the block time.
     *
     * A failure here sets `error` on the snapshot rather than throwing. A
     * dashboard that goes blank when a node hiccups is worse than one showing
     * the last good figures beside a clear note that they are stale.
     */
    async refresh(network: NetworkKey, appId: bigint, keeperAddress: string | null): Promise<void> {
        this.loading.set(true);
        try {
            const algod = this.client(network);
            const status = await algod.status().do();
            const round = BigInt(status.lastRound);

            const entries = await this.readRegistry(algod, appId, round);
            const account = keeperAddress ? await this.readAccount(algod, keeperAddress) : null;
            const secondsPerRound = await this.measureBlockTime(algod, round);

            this.snapshot.set({
                round,
                entries,
                account,
                // Attribution of an execution to a keeper is not in box state,
                // so without an indexer the honest answer is that this is not
                // known. `keeper-view.liveness` reports `unknown` rather than
                // inventing a verdict from it.
                lastSeenRound: null,
                secondsPerRound,
                error: null,
            });
        } catch (cause) {
            const previous = this.snapshot();
            const message = cause instanceof Error ? cause.message : String(cause);
            this.snapshot.set(
                previous
                    ? { ...previous, error: message }
                    : {
                          round: 0n,
                          entries: [],
                          account: null,
                          lastSeenRound: null,
                          secondsPerRound: 2.7,
                          error: message,
                      },
            );
        } finally {
            this.loading.set(false);
        }
    }

    private async readRegistry(
        algod: algosdk.Algodv2,
        appId: bigint,
        round: bigint,
    ): Promise<BoardEntry[]> {
        const { boxes } = await algod.getApplicationBoxes(appId).do();
        const found = await Promise.all(
            boxes.map(async (box) => {
                const id = upkeepIdFromBoxName(box.name);
                if (id === null) return null;
                const value = await algod.getApplicationBoxByName(appId, box.name).do();
                return toEntry(decodeUpkeep(id, value.value), round);
            }),
        );
        return found.filter((entry): entry is BoardEntry => entry !== null);
    }

    private async readAccount(algod: algosdk.Algodv2, address: string): Promise<KeeperAccount> {
        const info = await algod.accountInformation(address).do();
        const balance = BigInt(info.amount);
        // Spendable, not total. An account's minimum balance rises with every
        // asset opt-in and every app or asset it created, so a keeper holding
        // bonus assets can hold far more than it can spend.
        const spendable = balance - BigInt(info.minBalance);
        return { address, balance, spendable: spendable > 0n ? spendable : 0n };
    }

    /**
     * Measure block time rather than assuming it.
     *
     * The repository carries three different constants for this (2.8 in the
     * scripts, 2.66 in `docs/why.md`, and about 2.70 when actually measured),
     * and a dashboard that picked one would quietly disagree with whichever
     * page the operator read last.
     */
    private async measureBlockTime(algod: algosdk.Algodv2, round: bigint): Promise<number> {
        if (round <= BLOCK_TIME_SAMPLE) return 2.7;
        try {
            const [older, newer] = await Promise.all([
                algod.block(round - BLOCK_TIME_SAMPLE).do(),
                algod.block(round).do(),
            ]);
            const seconds = Number(newer.block.header.timestamp) - Number(older.block.header.timestamp);
            const measured = seconds / Number(BLOCK_TIME_SAMPLE);
            // A node serving nonsense must not produce a nonsense dashboard.
            return measured > 0.5 && measured < 30 ? measured : 2.7;
        } catch {
            return 2.7;
        }
    }
}
