/**
 * Whether the connected account can cover a total, and by how much it cannot.
 *
 * Three states rather than a boolean, because "not read yet" is not "not
 * enough" and must not be reported as it. The register form blocks on both,
 * and says something different about each.
 *
 * Its own module, with nothing imported into it, so its test binds to the
 * predicate the console runs rather than to a second copy of the arithmetic.
 * A test that redeclares this stays green with the check deleted, which is how
 * an unguarded submit button ships.
 */

export type Affordability =
  | { readonly state: 'unknown' }
  | { readonly state: 'enough'; readonly spendable: bigint; readonly left: bigint }
  | { readonly state: 'short'; readonly spendable: bigint; readonly shortfall: bigint };

export function affordability(total: bigint, spendable: bigint | null): Affordability {
  if (spendable === null) return { state: 'unknown' };
  if (spendable >= total) return { state: 'enough', spendable, left: spendable - total };
  return { state: 'short', spendable, shortfall: total - spendable };
}

