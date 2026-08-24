# pyright: reportMissingModuleSource=false
from algopy import (
    ARC4Contract,
    Asset,
    Box,
    Bytes,
    Global,
    GlobalState,
    LocalState,
    Txn,
    UInt64,
    arc4,
    gtxn,
    itxn,
    op,
)
from algopy.arc4 import abimethod

# MicroAlgos needed to cover the app account MBR (0.1 ALGO)
# plus one ASA opt-in MBR (0.1 ALGO).
BOOTSTRAP_MBR = 200_000
# CORVID fee (in base units, 6 decimals) charged per relayed message.
MESSAGE_FEE = 100_000
# Maximum sealed-envelope size accepted by `post`, in bytes.
MAX_MESSAGE_SIZE = 1024


class CorvidVault(ARC4Contract):
    """Vault and message operator for the CORVID ASA.

    Vault: users opt in, deposit CORVID via a grouped asset transfer, and
    withdraw up to their recorded balance at any time.

    Operator: staked members relay sealed envelopes (ciphertext encrypted
    off-chain, e.g. AlgoChat) to any recipient's on-chain inbox. The contract
    never reads message contents; it enforces membership, charges a CORVID
    fee per message, and stores envelopes in boxes keyed by recipient.
    """

    def __init__(self) -> None:
        # Global state: the CORVID ASA this vault accepts (0 until bootstrap).
        self.asset_id = GlobalState(UInt64(0))
        # Global state: total CORVID currently staked (surplus above this is fees).
        self.total_staked = GlobalState(UInt64(0))
        # Local state: deposited balance per opted-in account.
        self.balance = LocalState(UInt64)

    @abimethod()
    def bootstrap(self, mbr_payment: gtxn.PaymentTransaction, asset: Asset) -> None:
        """One-time setup: fund the app account MBR and opt the app into the CORVID ASA."""
        assert self.asset_id.value == 0, "Already bootstrapped"
        assert (
            mbr_payment.receiver == Global.current_application_address
        ), "Payment must fund the app account"
        assert mbr_payment.amount >= BOOTSTRAP_MBR, "Payment must cover the app MBR"
        assert mbr_payment.sender == Txn.sender, "Payment sender must be the caller"

        self.asset_id.value = asset.id

        # Opt the app account into the ASA (0-amount transfer to self).
        itxn.AssetTransfer(
            xfer_asset=asset,
            asset_receiver=Global.current_application_address,
            asset_amount=0,
        ).submit()

    @abimethod(allow_actions=["OptIn"])
    def opt_in_to_application(self) -> None:
        """Opt the caller into the app, initializing their vault balance."""
        self.balance[Txn.sender] = UInt64(0)

    @abimethod()
    def deposit(self, axfer: gtxn.AssetTransferTransaction) -> UInt64:
        """Deposit CORVID via a grouped asset transfer to the app account."""
        assert self.asset_id.value != 0, "Not bootstrapped"
        assert axfer.xfer_asset.id == self.asset_id.value, "Wrong asset"
        assert (
            axfer.asset_receiver == Global.current_application_address
        ), "Deposit must go to the app account"
        assert axfer.asset_amount > 0, "Amount must be positive"

        new_balance = self.balance[Txn.sender] + axfer.asset_amount
        self.balance[Txn.sender] = new_balance
        self.total_staked.value += axfer.asset_amount
        return new_balance

    @abimethod()
    def withdraw(self, amount: UInt64) -> UInt64:
        """Withdraw CORVID; the app sends the ASA back to the caller."""
        current = self.balance[Txn.sender]
        assert amount > 0, "Amount must be positive"
        assert current >= amount, "Insufficient balance"

        new_balance = current - amount
        itxn.AssetTransfer(
            xfer_asset=Asset(self.asset_id.value),
            asset_receiver=Txn.sender,
            asset_amount=amount,
        ).submit()
        self.balance[Txn.sender] = new_balance
        self.total_staked.value -= amount
        return new_balance

    @abimethod(readonly=True)
    def vault_balance(self) -> UInt64:
        """Caller's current vault balance (0 if not opted in)."""
        return self.balance.get(Txn.sender, default=UInt64(0))

    @abimethod()
    def post(
        self,
        mbr_payment: gtxn.PaymentTransaction,
        fee_payment: gtxn.AssetTransferTransaction,
        recipient: arc4.Address,
        ciphertext: arc4.DynamicBytes,
    ) -> UInt64:
        """Relay a sealed envelope to a recipient's inbox.

        The caller must be a staked member. `fee_payment` is a CORVID transfer
        of at least MESSAGE_FEE to the app; `mbr_payment` funds the box MBR.
        Returns the inbox index the envelope was stored at.
        """
        assert self.asset_id.value != 0, "Not bootstrapped"
        assert self.balance[Txn.sender] > 0, "Only staked members can post"

        assert (
            fee_payment.xfer_asset.id == self.asset_id.value
        ), "Fee must be paid in the vault asset"
        assert (
            fee_payment.asset_receiver == Global.current_application_address
        ), "Fee must go to the app account"
        assert fee_payment.asset_amount >= MESSAGE_FEE, "Fee below minimum"

        body = ciphertext.native
        size = body.length
        assert UInt64(0) < size <= MAX_MESSAGE_SIZE, "Ciphertext size out of bounds"

        counter_box = Box(UInt64, key=op.concat(b"i", recipient.bytes))
        index = counter_box.get(default=UInt64(0))
        message_box = Box(
            Bytes,
            key=op.concat(op.concat(b"m", recipient.bytes), op.itob(index)),
        )

        # Box MBR: the recipient's counter box on their first message
        # (2500 + 400 * (33-byte name + 8-byte value)) plus the message box
        # (2500 + 400 * (41-byte name + content size)).
        required_mbr = 2_500 + 400 * (41 + size)
        if not counter_box:
            required_mbr += UInt64(2_500 + 400 * 41)
        assert (
            mbr_payment.receiver == Global.current_application_address
        ), "MBR payment must fund the app account"
        assert mbr_payment.amount >= required_mbr, "MBR payment too small"

        message_box.value = body
        counter_box.value = index + 1
        return index

    @abimethod(readonly=True)
    def message_count(self, recipient: arc4.Address) -> UInt64:
        """Number of envelopes relayed to a recipient's inbox so far."""
        counter_box = Box(UInt64, key=op.concat(b"i", recipient.bytes))
        return counter_box.get(default=UInt64(0))

    @abimethod()
    def delete_message(self, index: UInt64) -> None:
        """Delete an envelope from the caller's own inbox, freeing its box MBR."""
        message_box = Box(
            Bytes,
            key=op.concat(op.concat(b"m", Txn.sender.bytes), op.itob(index)),
        )
        assert message_box, "Message not found"
        del message_box.value
