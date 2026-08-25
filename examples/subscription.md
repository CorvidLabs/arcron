# Recurring subscriptions

Billing that does not need a server. A provider prices a period, subscribers
deposit, and a keeper advances the billing period on a cadence nobody controls.

`smart_contracts/subscription/` with `scripts/subscription_demo.py`.
`fledge run smoke-subscription`.

## What it demonstrates

This is the shape [`docs/integrating.md`](../docs/integrating.md) argues for,
built as a worked argument rather than a rule.

The obvious design bills everybody inside the scheduled call: walk the
subscriber boxes, debit each one, pay the provider. It does not work, and the
two reasons are independent.

An Arcron inner call reaches only what the keeper's own transaction made
available. Nothing on-chain tells a keeper which subscriber boxes exist, so the
hook cannot open them. And one closed or hostile account would fail the whole
execution, which stops billing for every other subscriber at once.

So `charge` does the smallest thing that can be done without naming anybody:

```python
@abimethod()
def charge(self) -> UInt64:
    assert Txn.sender == Application(self.keeper_app.value).address, "..."
    self.period.value += 1
    self.last_charged_round.value = Global.round
    return self.period.value
```

No boxes. No payments. No branch that can reject once authorization passes.
Everything else is arithmetic against that counter, done in transactions the
interested party sends themselves, where their own box is available by
construction.

## The two halves have different failure tolerances

Advancing the period must never fail, because a failing hook trips keeper
backoff and the schedule is the one part nobody can reconstruct afterwards.

Moving money is allowed to fail. It can be retried by whoever cares, and a
failure concerns one subscriber rather than all of them.

Splitting them puts each half where its failure is survivable. It also means
the provider does not depend on subscribers doing anything: `settle` names its
subject, so the provider can run it for anybody.

## A subscriber who runs out

The case worth watching in the demo. Ada deposits two periods' worth, four
periods pass, and settlement bills her for two:

```
Grace billed for 4 of 4 periods
Ada billed for 2 of 4 periods
```

She still owes the other two. Partial payment does not forgive a period, so
`paid_through_period` advances only by what was actually paid for. Then the
schedule carries on:

```
periods billed = 5
Ada is out of funds and the schedule did not notice.
```

That is the whole point. One subscriber's balance is not able to affect
anybody else's billing, or the schedule itself.

## What it does not do

No refunds for partial periods, no proration, no plan changes, no trial. A
price and a period, fixed at creation. Those are product decisions, and adding
them here would obscure the one structural idea worth copying.
