---
hi: 1
families: [CONSOLE]
---

# The console

## Intent

The console is how most people will meet Arcron, so it has to be readable by a stranger before anybody connects a wallet and honest at the moment they sign. It should ask what somebody wants to happen and how often, in those words, and assemble the contract's fields behind that; it should quote a cost that matches what the wallet asks for to the microalgo; and it should turn every rejection the contract could give into a disabled control with a specific reason. It should serve the keeper looking for work as well as the person watching their own schedules, and show the upkeeps nobody can afford to run rather than hiding the network's failures. Because anyone can deploy a look-alike and shareable links are how this spreads, it must be loud about which deployment it is pointed at and refuse to spend money on one it cannot vouch for.

## Criteria

- **CONSOLE-1**  A stranger reaches a live registry without connecting a wallet, choosing a network or entering an app id.
- **CONSOLE-2**  The first screen tells me the registry is alive without my having to go looking for proof.
  - **CONSOLE-2.a**  I can see how often upkeeps are actually being executed.
  - **CONSOLE-2.b**  I can tell what the amounts on screen are worth on the chain the console is pointed at.
- **CONSOLE-3**  A link pointing the console at a deployment that is not the published one disables every money button until I explicitly accept it.
  - **CONSOLE-3.a**  An app id that is not this console's published deployment is never remembered, so a poisoned link cannot outlive the visit.
- **CONSOLE-4**  The console says whether the deployment it is showing can still have its programs replaced.
- **CONSOLE-5**  I can reach a block explorer for the app, its account and any execution, so nothing rests on trusting this page.
- **CONSOLE-6**  I am asked what I want to happen and how often, in those words.
  - **CONSOLE-6.a**  The fields the contract needs are assembled behind that rather than put in front of me.
- **CONSOLE-7**  I can test my call before connecting a wallet.
  - **CONSOLE-7.a**  The test makes the call the way a keeper actually would.
  - **CONSOLE-7.b**  The test grades what the call reaches for instead of returning a flat pass.
  - **CONSOLE-7.c**  A call the test has just said would fail cannot be registered.
- **CONSOLE-8**  Everything I am committing to is on the screen in front of me before I sign.
  - **CONSOLE-8.a**  I see which app I am paying and its account in full.
  - **CONSOLE-8.b**  I see what the whole thing costs, including the transaction fees.
  - **CONSOLE-8.c**  I see how long the funding I am about to send will last.
- **CONSOLE-9**  The cost shown is the cost charged, to the microalgo.
- **CONSOLE-10**  I cannot start a registration I cannot afford.
  - **CONSOLE-10.a**  I am told which part I cannot afford before the wallet opens.
- **CONSOLE-11**  Every rejection the contract could give me is a disabled control with a specific reason instead.
- **CONSOLE-12**  I tick a box saying I checked this call against my own app.
  - **CONSOLE-12.a**  The test passing is not what satisfies that box.
- **CONSOLE-13**  After signing I land on my upkeep's own page rather than back at a list.
  - **CONSOLE-13.a**  I can watch it run for the first time without hunting for it.
- **CONSOLE-14**  Every upkeep has a page of its own that I can send to somebody.
  - **CONSOLE-14.a**  The page says what the upkeep calls.
  - **CONSOLE-14.b**  It says how often the upkeep is meant to run.
  - **CONSOLE-14.c**  It says when the upkeep is next due.
  - **CONSOLE-14.d**  It says what the upkeep has paid keepers so far.
  - **CONSOLE-14.e**  It says how much runway is left.
- **CONSOLE-15**  A cadence is shown as elapsed time as well as in rounds.
- **CONSOLE-16**  An expected outcome reads as information rather than as an error.
  - **CONSOLE-16.a**  Only something nobody anticipated is allowed to look alarming.
- **CONSOLE-17**  An upkeep nobody is keeping is named as a different condition from one that has run out of money, because topping up only fixes one of them.
- **CONSOLE-18**  An upkeep no keeper can afford to execute is shown rather than hidden, because concealing the network's failures helps nobody.
- **CONSOLE-19**  I can see what is claimable right now and what each one would pay me net of what executing costs.
- **CONSOLE-20**  Anyone can execute a due upkeep from the console and be paid the fee for it.
  - **CONSOLE-20.a**  The page says plainly that is what the button does.
- **CONSOLE-21**  I can find my own upkeeps without reading the whole registry.
- **CONSOLE-22**  I can search the registry from wherever I am in the console.
- **CONSOLE-23**  The address bar always holds the link that would bring somebody back to exactly what I am looking at.
- **CONSOLE-24**  The console is usable at phone width, not only on a desktop.
  - **CONSOLE-24.a**  It is usable in both light and dark without my having to pick one.
- **CONSOLE-25**  I can use the console with a keyboard and a screen reader.
  - **CONSOLE-25.a**  Moving to a new page moves my focus with it.
  - **CONSOLE-25.b**  A disabled control is still legible rather than faded into its background.
  - **CONSOLE-25.c**  An accessibility defect that is knowingly still there is written down with the reason it stands.
- **CONSOLE-26**  The console has one canonical address.
  - **CONSOLE-26.a**  Everything that sends somebody to the console names that address.
- **CONSOLE-27**  I sign with the wallet I already use.
  - **CONSOLE-27.a**  No mnemonic is ever typed into the page.
