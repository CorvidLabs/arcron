import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  ElementRef,
  inject,
  viewChild,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';

import { ActivityLog } from './components/activity-log';
import { NetworkBar } from './components/network-bar';
import { QuarantinePanel } from './components/quarantine-panel';
import { SignerBar } from './components/signer-bar';
import { StatTiles } from './components/stat-tiles';
import { TrustBanner } from './components/trust-banner';
import { ArcronService } from './core/arcron.service';
import { entryParams } from './core/entry';
import { shortAddress } from '@corvidlabs/arcron/format';

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ActivityLog,
    NetworkBar,
    QuarantinePanel,
    RouterOutlet,
    SignerBar,
    StatTiles,
    TrustBanner,
  ],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  protected readonly arcron = inject(ArcronService);
  private readonly router = inject(Router);

  /** The routed region: everything that changes when the URL changes. */
  private readonly routed = viewChild<ElementRef<HTMLElement>>('routed');

  /** The last URL the router settled on, empty until the first navigation ends. */
  private readonly settledUrl = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map((event) => event.urlAfterRedirects),
    ),
    { initialValue: '' },
  );

  /** The path of the destination on screen, without the query string. */
  private readonly path = computed(() => this.settledUrl().split('?')[0]);

  /** The path focus was last moved to, so a query-only rewrite does not move it again. */
  private focusedPath: string | null = null;

  constructor() {
    // A single-page router swaps the content without moving the caret, so a
    // screen reader stays where it was and a keyboard user tabs on from a
    // link that no longer exists. Moving focus to the top of the new content
    // is what makes a navigation a navigation.
    //
    // Keyed on the path rather than on a navigation count, because the effect
    // below replaces the URL to keep the query string current, and that is a
    // navigation the visitor did not ask for. Focus must not follow it.
    effect(() => {
      const path = this.path();
      if (path === '') return;
      const previous = this.focusedPath;
      this.focusedPath = path;
      // The first settled path is the page opening. Focus belongs at the top
      // of the document then, not stolen from it.
      if (previous === null || previous === path) return;
      // preventScroll: default focus() scrolls `.routed` (below the banner and
      // tiles) to the top of the viewport, so Registry / Upkeep / Register
      // landed a screen-height of chrome down instead of at the top. The
      // router already restores or resets scroll; this only moves the caret.
      this.routed()?.nativeElement.focus({ preventScroll: true });
    });

    // Keep the address bar describing what is on screen, so the URL is always
    // the shareable link with no copy button to find, and a reload comes back
    // to the same registry rather than to whatever was last remembered.
    //
    // Through the router, not `history.replaceState`. Writing the address bar
    // behind the router's back leaves its own copy of the URL stale, and the
    // next routerLink rebuilds the address from that stale copy, dropping the
    // two parameters this exists to keep.
    //
    // Built from the settled URL rather than from `navigate([])`, because
    // empty commands with no `relativeTo` resolve against the root and would
    // rewrite `/register` to `/`. It lives here rather than in `ArcronService`
    // so that service stays free of the router, which its tests import it
    // without.
    effect(() => {
      const queryParams = entryParams(this.arcron.network(), this.arcron.appId());
      const url = this.settledUrl();
      if (url === '') return;
      const tree = this.router.parseUrl(url);
      tree.queryParams = { ...tree.queryParams, ...queryParams };
      const next = tree.toString();
      // Already saying the right thing. Navigating anyway would end this
      // effect where it started, once per navigation, for ever.
      if (next === url) return;
      void this.router.navigateByUrl(tree, { replaceUrl: true });
    });
  }

  protected readonly appAddress = computed(() => {
    const account = this.arcron.appAccount();
    return account === null ? null : shortAddress(account.address);
  });

  protected readonly nodeError = computed(() => {
    if (this.arcron.genesisMatches() === false) {
      return `The node answering for ${this.arcron.config().label} reports genesis ${this.arcron.genesisId()}. Check the endpoint before trusting anything on this page.`;
    }
    return this.arcron.error();
  });
}
