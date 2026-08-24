# pyright: reportMissingModuleSource=false
"""A stand-in for Algorand's randomness beacon, for LocalNet only.

LocalNet has no beacon, so anything that reads one cannot be tested there
without a stand-in. This implements the same `must_get(uint64,byte[])byte[]`
interface as the real thing (verified against the deployed programs — TestNet
`600011887`, MainNet `1615566206`).

**It is not random.** The value is a hash of the round, so it is deterministic
and anyone can compute it in advance. That is fine for proving the plumbing —
that a caller can supply the beacon reference, that the return decodes, that a
winner falls out — and useless for anything else. Never point a real
deployment at this.
"""

from algopy import ARC4Contract, Bytes, Global, UInt64, arc4, op
from algopy.arc4 import abimethod


class BeaconStub(ARC4Contract):
    """Deterministic bytes, shaped like the beacon's."""

    @abimethod()
    def must_get(self, round_number: UInt64, user_data: arc4.DynamicBytes) -> arc4.DynamicBytes:
        """32 bytes for a past round, as the real beacon would return.

        The past-round check is real, because it is the property that makes a
        commit-then-resolve scheme sound: a value nobody can know yet.
        """
        assert round_number < Global.round, "Round has not passed yet"
        return arc4.DynamicBytes(op.sha256(op.concat(op.itob(round_number), user_data.native)))
