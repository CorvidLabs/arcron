import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';

import { algos, shortAddress } from '@corvidlabs/arcron/format';
import { DEFAULT_NETWORK, NETWORKS, type NetworkKey } from '@corvidlabs/arcron/networks';

import { ChainService } from './core/chain.service';
import {
    claimableNow,
    humanRounds,
    liveness,
    livenessReason,
    nextBest,
    unprofitable,
} from './core/keeper-view';

@Component({
    selector: 'keeper-dashboard',
    changeDetection: ChangeDetectionStrategy.OnPush,
    template: `
        <header>
            <h1>Arcron keeper</h1>
            <p class="sub">
                Local dashboard. Reads a public node and nothing else, so it never sees your key.
            </p>
        </header>

        <form class="controls" (submit)="$event.preventDefault(); refresh()">
            <label>
                <span>Network</span>
                <select [value]="network()" (change)="setNetwork($any($event.target).value)">
                    @for (key of networkKeys; track key) {
                        <option [value]="key">{{ key }}</option>
                    }
                </select>
            </label>
            <label>
                <span>Keeper address</span>
                <input
                    type="text"
                    spellcheck="false"
                    placeholder="optional, to see what it holds"
                    [value]="address()"
                    (input)="address.set($any($event.target).value.trim())"
                />
            </label>
            <button type="submit" [disabled]="chain.loading()">
                {{ chain.loading() ? 'Reading…' : 'Refresh' }}
            </button>
        </form>

        @if (snapshot(); as snap) {
            @if (snap.error) {
                <p class="error" role="alert">
                    Could not read the node: {{ snap.error }}. Figures below are the last good ones.
                </p>
            }

            <section class="tiles">
                <div class="tile">
                    <span class="label">Round</span>
                    <strong>{{ snap.round }}</strong>
                    <span class="hint">{{ snap.secondsPerRound.toFixed(2) }}s per round, measured</span>
                </div>
                <div class="tile">
                    <span class="label">Due now</span>
                    <strong>{{ dueCount() }}</strong>
                    <span class="hint">of {{ snap.entries.length }} upkeep(s)</span>
                </div>
                <div class="tile">
                    <span class="label">Claimable</span>
                    <strong>{{ claimable() }}</strong>
                    <span class="hint">net of what executing costs</span>
                </div>
                <div class="tile" [class.warn]="health() === 'stopped'">
                    <span class="label">This keeper</span>
                    <strong>{{ health() }}</strong>
                    <span class="hint">{{ healthReason() }}</span>
                </div>
            </section>

            @if (account(); as acct) {
                <p class="account">
                    {{ short(acct.address) }} holds {{ algo(acct.balance) }},
                    <strong>{{ algo(acct.spendable) }} spendable</strong>. An account's minimum
                    balance rises with every asset opt-in, so the two differ.
                </p>
            }

            @if (best(); as pick) {
                <p class="pick">
                    Best right now: <strong>#{{ pick.upkeep.id }}</strong>, paying
                    {{ algo(pick.netReward) }} net@if (pick.escalated) {, on an escalated fee}.
                </p>
            }

            <h2>Registry</h2>
            <div class="scroller">
                <table>
                    <thead>
                        <tr>
                            <th>id</th><th>state</th><th>net reward</th>
                            <th>due</th><th>runs left</th>
                        </tr>
                    </thead>
                    <tbody>
                        @for (item of snap.entries; track item.upkeep.id) {
                            <tr [class.due]="item.availability === 'due'">
                                <td>#{{ item.upkeep.id }}</td>
                                <td>{{ item.availability }}</td>
                                <td [class.loss]="isLoss(item)">{{ algo(item.netReward) }}</td>
                                <td>
                                    @if (item.availability === 'due') {
                                        {{ overdue(item.overdueRounds) }} late
                                    } @else {
                                        &mdash;
                                    }
                                </td>
                                <td>{{ item.runsRemaining }}</td>
                            </tr>
                        }
                    </tbody>
                </table>
            </div>

            @if (losses().length) {
                <p class="note">
                    {{ losses().length }} upkeep(s) pay nothing after execution costs. A keeper that
                    takes them is paying to do somebody else's work.
                </p>
            }
        } @else {
            <p class="note">Press Refresh to read the registry.</p>
        }
    `,
    styles: `
        :host { display: block; max-width: 70rem; margin: 0 auto; padding: 1.5rem 1rem 4rem; }
        header h1 { margin: 0; font-size: 1.5rem; }
        .sub { margin: 0.25rem 0 1.5rem; opacity: 0.75; font-size: 0.9rem; }
        .controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: end; margin-bottom: 1.5rem; }
        .controls label { display: grid; gap: 0.25rem; font-size: 0.8rem; }
        .controls span { text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.75; }
        .controls input { min-width: 22rem; }
        .controls input, .controls select, .controls button { padding: 0.5rem 0.7rem; font: inherit; min-height: 44px; }
        .tiles { display: grid; gap: 0.75rem; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); margin-bottom: 1.25rem; }
        .tile { display: grid; gap: 0.2rem; padding: 0.85rem; border: 1px solid currentColor; border-radius: 0.5rem; }
        .tile .label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.75; }
        .tile strong { font-size: 1.35rem; }
        .tile .hint { font-size: 0.75rem; opacity: 0.75; }
        .tile.warn { outline: 2px solid currentColor; }
        .account, .pick, .note, .error { font-size: 0.9rem; margin: 0.5rem 0 1rem; }
        .error { font-weight: 600; }
        h2 { font-size: 1rem; text-transform: uppercase; letter-spacing: 0.06em; margin: 1.5rem 0 0.5rem; }
        .scroller { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
        th, td { text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid currentColor; }
        th { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.75; }
        tr.due td { font-weight: 600; }
        td.loss { text-decoration: underline; }
    `,
})
export class Dashboard {
    protected readonly chain = inject(ChainService);
    protected readonly networkKeys = Object.keys(NETWORKS) as NetworkKey[];

    protected readonly network = signal<NetworkKey>(DEFAULT_NETWORK);
    protected readonly address = signal('');

    protected readonly snapshot = this.chain.snapshot;
    protected readonly account = computed(() => this.snapshot()?.account ?? null);

    protected readonly dueCount = computed(
        () => (this.snapshot()?.entries ?? []).filter((e) => e.availability === 'due').length,
    );
    protected readonly best = computed(() => nextBest(this.snapshot()?.entries ?? []));
    protected readonly losses = computed(() => unprofitable(this.snapshot()?.entries ?? []));

    protected readonly health = computed(() => {
        const snap = this.snapshot();
        if (!snap) return 'unknown' as const;
        return liveness(snap.lastSeenRound, snap.round, this.dueCount());
    });
    protected readonly healthReason = computed(() =>
        livenessReason(this.health(), this.dueCount()),
    );

    protected claimable(): string {
        return algos(claimableNow(this.snapshot()?.entries ?? []));
    }

    protected setNetwork(key: string): void {
        this.network.set(key as NetworkKey);
    }

    protected isLoss(item: { netReward: bigint }): boolean {
        return item.netReward <= 0n;
    }

    protected algo(micro: bigint): string {
        return algos(micro);
    }

    protected short(address: string): string {
        return shortAddress(address);
    }

    protected overdue(rounds: bigint): string {
        return humanRounds(rounds, this.snapshot()?.secondsPerRound ?? 2.7);
    }

    protected refresh(): void {
        const key = this.network();
        const appId = NETWORKS[key].defaultAppId;
        if (appId === undefined) return;
        void this.chain.refresh(key, BigInt(appId), this.address() || null);
    }
}
