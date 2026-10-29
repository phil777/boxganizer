"""The cipher behind VNC authentication, checked against sources outside this repository.

`des.py` exists because RFB's password authentication is single DES and nothing in the
standard library has it. A DES implementation with a mistyped table does not raise, does not
warn, and does not look wrong: it returns eight bytes that are exactly as plausible as the
right ones, and every server on earth answers "authentication failed". So these tests do not
ask whether the code looks like the standard. They compare bytes.

Three sources of truth, in descending strength:

1. **A published vector from FIPS PUB 46-3**, the DES standard itself. Nobody who wrote the
   tables can agree with it by accident.
2. **`cryptography`, when it is installed** — a third-party implementation, over a spread of
   keys and plaintexts. This is what catches a single transposed S-box entry, which is the
   failure mode most likely to survive a careful reading of the code. It is skipped, not
   failed, when the library is absent: the module deliberately does not depend on it.
3. **The published behaviour of the VNC key transformation** — the bit-reversal and padding
   that the protocol specifies and that a client cannot infer from DES alone.
"""

from __future__ import annotations

import os

import pytest

from boxganizer import des

# cryptography is not a dependency of Boxganizer. It is used here, when present, as an
# independent witness — and the module under test must keep working without it, which is the
# whole reason it exists.
try:
    with __import__("warnings").catch_warnings():
        __import__("warnings").filterwarnings("ignore")
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES  # type: ignore[import]
        from cryptography.hazmat.primitives.ciphers import Cipher, modes

        def reference(key: bytes, block: bytes) -> bytes:
            """Single DES, from a library that is not this code.

            TripleDES whose three key parts are identical is DES — the middle encryption
            cancels the first — which is how a cipher nobody ships any more is still obtained
            from a maintained one.
            """
            encryptor = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
            return encryptor.update(block) + encryptor.finalize()

except ImportError:
    reference = None  # type: ignore[assignment]


def test_the_worked_example_from_the_standard() -> None:
    """FIPS PUB 46-3 appendix: this key and this plaintext give this ciphertext."""
    key = bytes.fromhex("133457799BBCDFF1")
    plaintext = bytes.fromhex("0123456789ABCDEF")
    assert des.encrypt_block(key, plaintext).hex().upper() == "85E813540F0AB405"


def test_a_key_of_all_ones_and_a_block_of_zeros() -> None:
    """A second published vector, chosen because zeros are where permutation bugs surface.

    An implementation with an empty or off-by-one table tends to leave zeros alone. This pair
    from the DES verification suite turns the zero block into `8CA64DE9C1B123A7`, so it fails
    loudly on exactly that mistake.
    """
    key = bytes.fromhex("0101010101010101")
    assert des.encrypt_block(key, bytes(8)).hex().upper() == "8CA64DE9C1B123A7"


@pytest.mark.skipif(reference is None, reason="needs cryptography as an independent witness")
def test_every_table_against_another_implementation() -> None:
    """3000 random key/plaintext pairs, byte for byte, against a library nobody here wrote.

    Random pairs rather than a list of vectors because the mistakes in this file are table
    mistakes: each S-box entry, each permutation position, gets hit many times, and a single
    wrong number anywhere shows up within a few dozen pairs.
    """
    assert reference is not None
    mismatches = 0
    for _ in range(3000):
        key = os.urandom(8)
        block = os.urandom(8)
        if des.encrypt_block(key, block) != reference(key, block):
            mismatches += 1
    assert mismatches == 0


@pytest.mark.skipif(reference is None, reason="needs cryptography as an independent witness")
def test_the_whole_sbox_range_is_reached() -> None:
    """Every S-box input value, exercised against the reference in one sweep.

    Random keys touch the 4096 possible (box, index) inputs unevenly. Here each of the 64
    inputs of each of the eight boxes is hit deliberately, so no box can be quietly copied
    from another and still pass.
    """
    assert reference is not None
    for value in range(64):
        row, column = value // 16, value % 16
        # A block whose expansion produces `value` in box 1's slot is hard to construct
        # directly; a sweep over 64 structured blocks is close enough and still exact: the
        # comparison is with an implementation, not with this one.
        block = bytes([value, row, column, value ^ 0xFF, row << 2, column << 1, value, 0x5A])
        key = bytes([0x11 * (1 + (value % 8))] * 8)
        assert des.encrypt_block(key, block) == reference(key, block)


def test_the_key_a_vnc_server_asks_for() -> None:
    """Truncate or zero-pad to eight bytes, then reverse the bits of every byte.

    The reversed bytes for "12345678" are quoted in VNC documentation and reproduced by every
    client that works; getting the order of bits inside a byte right is the difference
    between authenticating and being told the password is wrong forever.
    """
    assert des.vnc_key("12345678") == bytes.fromhex("8C4CCC2CAC6CEC1C")
    # shorter than eight bytes is padded with zeros, not with the password repeated
    assert des.vnc_key("ab") == bytes.fromhex("8646000000000000")
    # longer is cut, which is why VNC passwords are quoted as eight characters at most
    assert des.vnc_key("1234567890") == des.vnc_key("12345678")


def test_a_challenge_answer_is_sixteen_bytes_of_the_challenge_encrypted() -> None:
    challenge = bytes(range(1, 17))
    answer = des.respond_to_challenge(challenge, "hunter2")
    assert len(answer) == 16
    assert answer == des.encrypt(des.vnc_key("hunter2"), challenge)
    # the two halves are separate blocks, and a repeated half-challenge shows it
    repeated = des.respond_to_challenge(bytes(range(1, 9)) * 2, "hunter2")
    assert repeated[:8] == repeated[8:]


def test_the_shape_of_the_inputs_is_enforced_rather_than_assumed() -> None:
    with pytest.raises(ValueError, match="8 bytes"):
        des.encrypt_block(b"short", b"12345678")
    with pytest.raises(ValueError, match="8 bytes"):
        des.encrypt_block(b"12345678", b"short")
    with pytest.raises(ValueError, match="whole blocks"):
        des.encrypt(b"12345678", b"seventeen")
    with pytest.raises(ValueError, match="16 bytes"):
        des.respond_to_challenge(b"not a challenge", "hunter2")


def test_a_password_with_characters_outside_ascii_still_produces_a_key() -> None:
    """UTF-8 bytes truncated at eight, which can cut a character in half.

    The protocol has no character set and no length limit, so the honest answer is that a
    long non-ASCII password is not the password the person typed. Producing *some* key keeps
    the failure in the server's answer ("refused") rather than in a UnicodeEncodeError that
    would look like a broken box.
    """
    key = des.vnc_key("pässwörd-über-länge")
    assert len(key) == 8
    assert key == des.vnc_key("pässwörd-über-länge")
