"""WalletPort — abstract interface for non-custodial wallet operations.

WHY: ADR-021 (WalletPort Isolation), ADR-014 (Composable Stack).
All seed generation, address derivation, transaction signing, and
encryption operations are routed through WalletPort so that the
custody-critical cryptographic adapter is replaceable without touching
business logic.  RISK_REGISTER R-SEC-NEW-01: seed material must never
cross a service boundary in plaintext and must never be logged.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Literal


class ChainId(Enum):
    BTC = "BTC"
    LTC = "LTC"
    ETH = "ETH"
    BSC = "BSC"
    TRX = "TRX"
    XRP = "XRP"


# BIP-44 derivation path, e.g. m/44'/0'/0'/0/0
DerivationPath = str


@dataclass
class WalletAddress:
    chain: ChainId
    address: str
    derivation_path: DerivationPath
    public_key: str  # hex


@dataclass
class SignedTx:
    chain: ChainId
    raw_tx: str  # hex / chain-native serialized
    tx_hash: str  # deterministic for given input


@dataclass
class SeedMaterial:
    entropy: bytes  # never logged, never persisted in plaintext, never crosses a network boundary


class WalletPort(ABC):
    """Abstract port for non-custodial wallet operations (ADR-021, R-SEC-NEW-01).

    Primary implementation: HSM-backed or software adapter injected at runtime.
    """

    @abstractmethod
    async def generate_seed_phrase(self, word_count: Literal[12, 24]) -> str:
        """Generate a BIP-39 mnemonic of the requested word count."""

    @abstractmethod
    async def seed_phrase_to_entropy(self, phrase: str) -> SeedMaterial:
        """Derive raw entropy bytes from a BIP-39 mnemonic."""

    @abstractmethod
    async def derive_address(
        self,
        seed: SeedMaterial,
        chain: ChainId,
        path: DerivationPath,
    ) -> WalletAddress:
        """Derive a chain address from seed material at the given BIP-44 path."""

    @abstractmethod
    async def sign_tx(
        self,
        seed: SeedMaterial,
        chain: ChainId,
        path: DerivationPath,
        tx_payload: object,
    ) -> SignedTx:
        """Sign a chain-specific transaction payload. Returns raw signed bytes and hash."""

    @abstractmethod
    async def verify_signature(
        self,
        chain: ChainId,
        address: str,
        message: str,
        signature: str,
    ) -> bool:
        """Verify that signature over message was produced by the key at address."""

    @abstractmethod
    async def validate_address(self, chain: ChainId, address: str) -> bool:
        """Return True iff address is a valid on-chain address for the given chain."""

    @abstractmethod
    async def encrypt(self, plaintext: str, password: str) -> str:
        """Encrypt plaintext with password. Returns opaque ciphertext string."""

    @abstractmethod
    async def decrypt(self, ciphertext: str, password: str) -> str:
        """Decrypt ciphertext previously produced by encrypt. Returns plaintext."""
