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
