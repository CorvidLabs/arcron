/**
 * Rain left this console. These three stubs are what is left of it.
 *
 * Rain used to be three routes here, on the reasoning that a scheduled draw is
 * a different contract with a holder-facing surface and so deserved its own
 * route rather than a tab. That reasoning still holds; it just landed on a
 * different answer. The two products have different readers — this console
 * shows selectors, reference grades, catch-up policy and escrow runway to a
 * developer, while a rain's reader holds an NFT and wants to know whether they
 * are in and what they are owed — so Rain moved to its own repository and its
 * own address, https://corvidlabs.xyz/rain/.
 *
 * Links are how this console spreads, so the old paths cannot simply stop
 * existing: the console routes on the path with an index.html fallback, which
 * means a shared /rain/3 would render the registry rather than 404, silently
 * showing the wrong page to somebody who was sent a rain. Each old path
 * therefore redirects to the new address, and each renders a plain link as
 * well so a visitor whose browser never runs the replace is not stranded.
 *
 * `/rain/:id` forwards to the rain list rather than to a rain. It carried the
 * id across when it was written, on the reasoning that a shared link names one
 * rain and a list of five cannot say which. That reasoning held only while
 * both addresses pointed at one hub, and they no longer do: this console read
 * hub 770130162, which is immutable and predates the fix that stops a ONE draw
 * being aimed by tickets bought after the seed is public, so rain redeployed
 * as 770746178 and does not adopt the old id. A rain's id is its box id on one
 * hub, so the same number is a different rain on the other, or no rain at all.
 * Forwarding the id would have turned a 404 into a plausible wrong page, which
 * is a worse version of the failure these stubs exist to prevent. The id is
 * still shown to the visitor, because it is the only thing they can use to
 * find the counterpart.
 *
 * These are temporary by design. Retire them after the announced window (30
 * days from the split) and delete the three routes with them; the window is
 * the only thing keeping two addresses pointed at one hub.
 */

import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { ActivatedRoute } from '@angular/router';

/** Where Rain lives now. The trailing slash is part of the base href. */
const RAIN = 'https://corvidlabs.xyz/rain/';

/**
 * Leave for the new address without adding a history entry.
 *
 * `replace` rather than `assign`: an entry for a page that only exists to
 * forward would make the browser's back button bounce the visitor straight
 * back out again.
 */
function leave(destination: string): void {
  if (typeof window === 'undefined') return;
  window.location.replace(destination);
}

@Component({
  selector: 'arcron-rain-moved',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section>
      <h1>Rain has moved</h1>
      <p>
        Rain now lives at its own address. If this page does not forward on its
        own, follow the link.
      </p>
      <p><a [href]="destination">{{ destination }}</a></p>
    </section>
  `,
  styles: `
    :host { display: grid; gap: 0.75rem; align-content: start; }
  `,
})
export class RainMoved {
  protected readonly destination = RAIN;

  constructor() {
    leave(this.destination);
  }
}

@Component({
  selector: 'arcron-rain-create-moved',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section>
      <h1>Rain has moved</h1>
      <p>
        Opening a rain now happens at Rain's own address. If this page does not
        forward on its own, follow the link.
      </p>
      <p><a [href]="destination">{{ destination }}</a></p>
    </section>
  `,
  styles: `
    :host { display: grid; gap: 0.75rem; align-content: start; }
  `,
})
export class RainCreateMoved {
  protected readonly destination = RAIN + 'new';

  constructor() {
    leave(this.destination);
  }
}

@Component({
  selector: 'arcron-rain-detail-moved',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section>
      <h1>Rain has moved</h1>
      <p>
        Rain now lives at its own address, on a new hub. The rain this link
        named was number <strong>{{ id }}</strong> on the hub this console
        served. Numbers do not carry across, so pick the rain by name rather
        than by number.
      </p>
      <p><a [href]="destination">{{ destination }}</a></p>
    </section>
  `,
  styles: `
    :host { display: grid; gap: 0.75rem; align-content: start; }
  `,
})
export class RainDetailMoved {
  private readonly route = inject(ActivatedRoute);

  protected readonly id = this.route.snapshot.paramMap.get('id') ?? '';

  /**
   * The list, not `r/<id>`.
   *
   * This forwarded to the rain's own detail path with the id appended, until
   * the hub changed. That was right while one hub sat behind both addresses.
   * It is not right now: ids
   * are box ids on a particular hub, this console read 770130162, and rain
   * redeployed as 770746178 because the old hub is immutable and cannot take
   * the ONE-draw seed fix. Carrying the number over would send a visitor to a
   * rain that either does not exist or is a different draw wearing the same
   * number — the silently-wrong-page failure this file was written to stop,
   * one hop further along. The number stays in the copy above so they can find
   * the counterpart themselves.
   *
   * **And this is the one stub that does not leave on its own.** It carried a
   * `leave()` for a day, which made the paragraph above dead code: any browser
   * running JavaScript replaced the location before painting, so the id the
   * component exists to show was shown to nobody, and a shared `/rain/3`
   * dropped its visitor on a list with no hint which draw they had been sent.
   * `/rain` and `/rain/new` still bounce, because neither names anything a
   * redirect could lose. This one has something to say first.
   */
  protected readonly destination = RAIN;
}
