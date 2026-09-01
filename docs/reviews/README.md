# Independent reviews

Three independent passes over the whole repo on 2026-08-25, by Grok 4.6,
Fable 5 and Kimi 3. Each got the same prompt, none saw the others' answers,
and none was told what the others had found.

They are kept unedited, including the parts that are wrong, because a review
that gets quietly corrected after the fact stops being evidence of anything.

## What they agreed on

All three led with the same finding: `SECURITY.md` said the contracts have no
update path, which was false and had been since governance shipped. All three
declined to escrow their own money on MainNet today, and all three gave the
same two reasons: the deployment is unfrozen, and the document a careful
person reads first was wrong about exactly that.

Confidence that the system is safe for real money today: **4/10, 3/10, and
5/10.** Each rated the keeper contract itself considerably higher than the
system around it, and each said the same thing in different words: the core
is the strongest part of the repository, and the risk lives in the docs, the
example contracts, and the off-chain tooling.

## Where they disagreed

- **Embargo's `schedule`.** Grok and Fable found it permissionless and
  front-runnable. Kimi filed it as a false alarm, having found the creator
  check. Kimi is right about the code as it stands: the fix landed between
  the commit the first two read and the one Kimi read.
- **Treasury's MBR handling.** Grok rated it High and exploitable. Fable
  investigated the same code, concluded the AVM's own minimum-balance
  enforcement makes `configure` revert rather than strand, and downgraded it.
  Both are worth reading; the accounting gap Grok describes is real, and
  Fable's point about which paths can actually reach it is also real.

## What was fixed as a result

See the commits following this date, and issues #95 through #102.

## Follow-ups

Follow-ups are separate files, so the three passes above stay what they were
on the day. They are not independent reviews: each one re-reads a fix by the
reviewer who found the thing it fixes, and knows what the fix was trying to do.

- [`2026-08-26-fable-5-console.md`](2026-08-26-fable-5-console.md) ,  does the
  trust banner close M1? **Partial.** It closes the plain case; a hostile app
  can still switch it off for the price of one box.
- [`2026-09-01-opus-5-keeper-audit.md`](2026-09-01-opus-5-keeper-audit.md) ,
  the keeper contract alone, on a real AVM. One Low finding with a test (the
  app account's own solvency was assumed by the contract and watched by
  nothing; `health` watches it now), the sandwich and reentrancy claims
  measured rather than argued, and a plain answer to the MainNet question.
- [`2026-09-01-opus-5-audit-verification.md`](2026-09-01-opus-5-audit-verification.md) ,
  the same model trying to break its own audit a day later. Everything
  reproduced, two of its sentences are wrong, and the thing it never looked
  for is the biggest: an upkeep's lateness can be bought for one application
  call, and whoever buys it collects the escalated fee.

Then three independent passes over that branch, on the reasoning that two
sessions of one model family grading each other is not independence. Same
prompt, no shared answers, each asked to refute rather than confirm:
[Grok 4.6](2026-09-01-grok-4.6-branch-review.md) **52 → 76**,
[Fable 5.1](2026-09-01-fable-5.1-branch-review.md) **62 → 68**,
[Kimi 3](2026-09-01-kimi-3-branch-review.md) **78 → 88**, each read twice with
its own report handed back the second time. None refuted the finding; all three
made it worse, and between them they found a spike that passed without
measuring anything, a test whose last assertion could not fail, a solvency
check that guessed in the direction that hides a shortfall, six documents
still arguing from a premise the repository had already retired, and — on the
second pass, by checking a test's fake against the client the production path
uses — a pagination bug in `keeper_bot.scan_upkeeps` that would have raised
`TypeError` on any registry over one page. The verification's section 6 lists
what each changed.

Nobody reached 95, and the gap is worth reading rather than closing. What is
left is a liveness question the branch opened and did not answer: one inner
transaction failure sends the reference keeper away from an upkeep for up to
an hour, escalation or not.

---

## The 2026-08-26 re-score

Three fresh full-system passes after every finding above was closed. Each was
asked for **two** numbers rather than one, because every earlier score anchored
on `freeze` and freeze is a deliberate trade here rather than an unfinished
step.

| | Unfrozen, id unpublished | Frozen at deploy | Was | Would escrow their own money |
|---|---|---|---|---|
| Kimi 3 | **8** | 7 | 5 | yes, capped |
| Grok 4.6 | **7** | 5 | 4 | yes, as operator |
| Fable 5 | **5** | 3 | 3 | **no** |

All three said the trade is right. Grok: "The trade is not wrong. Freezing at
create would be." Fable, who scored lowest, agreed on the reasoning and
disagreed on one of its premises: the app id cannot stay unpublished, because
the multisig address is in six files and an app id is one indexer query from a
creator address.

**Every one of them scored the frozen case lower.** That is the opposite of
what the same three said a day earlier, and the reason they gave is the same:
this codebase has produced roughly one previously undiscovered bug per review
round, and freezing converts the next one from a patch into a migration.

## What the outlier found

Fable's 5 is not a disagreement about the trade. It is one finding the other
two missed and Fable proved on a live chain: `register` never bound the payer
to the upkeep's creator, so a victim could sign both payments while an
attacker signed the app call and owned the resulting upkeep. Every check the
project teaches a user to make passed, because the receiver and the app id
really were the right ones.

Two asserts, no struct change, and `subscription` had had the same assert
since it was written. Five review rounds and four audits had not found it.

Fable also named the pattern behind it: fixes that are correct at the site
where the bug was found and absent at the siblings with the identical shape,
with the reasoning written down at the fixed site and not carried across.
[#105](../../issues/105) is the open instance of that pattern.
