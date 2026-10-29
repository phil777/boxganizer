"""Single DES, in plain Python: the one cipher RFB's password authentication needs.

VNC's "VNC authentication" (security type 2) is not a scheme invented by the protocol. The
server sends 16 bytes of challenge; the client encrypts them with DES-ECB under a key made
from the password; and it sends the 16 bytes back. That is the whole exchange. So the one
primitive Boxganizer needs, in order to let a person look at a screen that asks for a
password, is single DES on an eight-byte block.

Nothing in the standard library has it. `cryptography` still exposes TripleDES at all — and
TripleDES under a 24-byte key of three identical thirds is single DES — but that symbol has
been announced for removal, and putting the ability to authenticate a screen behind someone
else's deprecation calendar is not a dependency worth taking for 16 bytes. So it is here:
the published tables, the published Feistel structure, no key schedule beyond what the
standard describes, and a comment on every line that could be got wrong silently.

Why this is safe to ship rather than clever:

* **It is checked against a reference, not against itself.** `tests/test_vnc_auth.py`
  compares this implementation, byte for byte, against `cryptography`'s TripleDES whenever
  that library is installed, over a spread of keys and plaintexts — which is what catches a
  transposed entry in an S-box, the failure mode that produces plausible garbage. It also
  checks the published weak-key vector, so the test still means something on a machine
  without `cryptography`.
* **It is used for one thing.** No padding, no modes, no IV handling: ECB on 8-byte blocks,
  because that is exactly what the protocol asks for. A general-purpose cipher module is a
  module someone will later use for something that matters.
* **The password never comes from here.** This module takes bytes and returns bytes; where
  they came from is `vnc.py`'s business, and the rule there is that a secret is held for one
  session and never written to the library.
"""

from __future__ import annotations

#: The tables below are transcribed from FIPS PUB 46-3 (the DES standard) as reproduced in
#: the Wikipedia article "DES supplementary material", which carries the NIST tables. They
#: are checked against a reference implementation in tests/test_vnc_auth.py rather than
#: trusted: the first draft of this module had the initial permutation written column-major
#: and three of the S-boxes half-remembered, and it produced 8 bytes of quiet nonsense every
#: single time without raising anything.

#: The initial permutation: the first output bit is taken from bit 58 of the input, the
#: second from bit 50, and so on. Rows below are rows of the printed table, read left to
#: right — the table is a vector, and writing it the other way round is the easy mistake.
_IP = [
    58, 50, 42, 34, 26, 18, 10, 2,
    60, 52, 44, 36, 28, 20, 12, 4,
    62, 54, 46, 38, 30, 22, 14, 6,
    64, 56, 48, 40, 32, 24, 16, 8,
    57, 49, 41, 33, 25, 17, 9, 1,
    59, 51, 43, 35, 27, 19, 11, 3,
    61, 53, 45, 37, 29, 21, 13, 5,
    63, 55, 47, 39, 31, 23, 15, 7,
]

_FP = [
    40, 8, 48, 16, 56, 24, 64, 32,
    39, 7, 47, 15, 55, 23, 63, 31,
    38, 6, 46, 14, 54, 22, 62, 30,
    37, 5, 45, 13, 53, 21, 61, 29,
    36, 4, 44, 12, 52, 20, 60, 28,
    35, 3, 43, 11, 51, 19, 59, 27,
    34, 2, 42, 10, 50, 18, 58, 26,
    33, 1, 41, 9, 49, 17, 57, 25,
]

#: Choice 1: 64 key bits down to 56, dropping the parity bits (8, 16, 24, ... 64). The
#: printed table is two halves of four rows each; the left half comes first.
_PC1 = [
    57, 49, 41, 33, 25, 17, 9,
    1, 58, 50, 42, 34, 26, 18,
    10, 2, 59, 51, 43, 35, 27,
    19, 11, 3, 60, 52, 44, 36,
    63, 55, 47, 39, 31, 23, 15,
    7, 62, 54, 46, 38, 30, 22,
    14, 6, 61, 53, 45, 37, 29,
    21, 13, 5, 28, 20, 12, 4,
]

#: Choice 2: the 56-bit key state down to this round's 48-bit subkey. It ignores bits
#: 9, 18, 22, 25, 35, 38, 43 and 54 of that state.
_PC2 = [
    14, 17, 11, 24, 1, 5, 3, 28,
    15, 6, 21, 10, 23, 19, 12, 4,
    26, 8, 16, 7, 27, 20, 13, 2,
    41, 52, 31, 37, 47, 55, 30, 40,
    51, 45, 33, 48, 44, 49, 39, 56,
    34, 53, 46, 42, 50, 36, 29, 32,
]

#: The expansion: 32 bits of data to 48, with the outer bits duplicated so neighbouring
#: S-boxes share them.
_EXPANSION = [
    32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9,
    8, 9, 10, 11, 12, 13, 12, 13, 14, 15, 16, 17,
    16, 17, 18, 19, 20, 21, 20, 21, 22, 23, 24, 25,
    24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1,
]

#: The P permutation, applied to the S-box output before it is XORed into the other half.
_PERM = [
    16, 7, 20, 21, 29, 12, 28, 17,
    1, 15, 23, 26, 5, 18, 31, 10,
    2, 8, 24, 14, 32, 27, 3, 9,
    19, 13, 30, 6, 22, 11, 4, 25,
]

#: How far each half of the key rotates before this round's subkey is taken.
_SHIFTS = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]

#: The eight S-boxes: 6 bits in, 4 out. Row from the outer two bits, column from the inner
#: four. Written row by row in the order the standard prints them (S1 ... S8).
_SBOXES = [
    [
        14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
        0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
        4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
        15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13,
    ],
    [
        15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
        3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
        0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
        13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9,
    ],
    [
        10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
        13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
        13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
        1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12,
    ],
    [
        7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
        13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
        10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
        3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14,
    ],
    [
        2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
        14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
        4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
        11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3,
    ],
    [
        12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
        10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
        9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
        4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13,
    ],
    [
        4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
        13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
        1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
        6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12,
    ],
    [
        13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
        1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
        7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
        2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11,
    ],
]


def _bits(block: bytes) -> list[int]:
    """The 64 bits of a block, most significant first — the order the tables assume."""
    out: list[int] = []
    for byte in block:
        out.extend((byte >> shift) & 1 for shift in range(7, -1, -1))
    return out


def _bytes(bits: list[int]) -> bytes:
    value = 0
    out = bytearray()
    for index, bit in enumerate(bits):
        value = (value << 1) | bit
        if index % 8 == 7:
            out.append(value)
            value = 0
    return bytes(out)


def _permute(bits: list[int], table: list[int]) -> list[int]:
    # The tables are written 1-based ("bit 58 of the block"), the list is not.
    return [bits[position - 1] for position in table]


def _subkeys(key: bytes) -> list[list[int]]:
    """The 16 round keys, each 48 bits."""
    schedule = _permute(_bits(key), _PC1)
    left, right = schedule[:28], schedule[28:]
    rounds: list[list[int]] = []
    for shift in _SHIFTS:
        left, right = left[shift:] + left[:shift], right[shift:] + right[:shift]
        rounds.append(_permute(left + right, _PC2))
    return rounds


def _feistel(right: list[int], subkey: list[int]) -> list[int]:
    expanded = _permute(right, _EXPANSION)
    mixed = [bit ^ key for bit, key in zip(expanded, subkey, strict=True)]
    out: list[int] = []
    for index in range(8):
        six = mixed[index * 6 : (index + 1) * 6]
        row = (six[0] << 1) | six[5]
        column = (six[1] << 3) | (six[2] << 2) | (six[3] << 1) | six[4]
        value = _SBOXES[index][row * 16 + column]
        out.extend((value >> shift) & 1 for shift in range(3, -1, -1))
    return _permute(out, _PERM)


def encrypt_block(key: bytes, block: bytes) -> bytes:
    """One 8-byte block, DES-ECB.

    Shaped the way the standard describes it, on purpose: a Feistel network written as
    a loop over a table is a module someone can check line by line, and 16 rounds of it is
    all the protocol ever asks for.
    """
    if len(key) != 8:
        raise ValueError(f"a DES key is 8 bytes, not {len(key)}")
    if len(block) != 8:
        raise ValueError(f"a DES block is 8 bytes, not {len(block)}")
    data = _permute(_bits(block), _IP)
    left, right = data[:32], data[32:]
    for round_key in _subkeys(key):
        mixed = [a ^ b for a, b in zip(left, _feistel(right, round_key), strict=True)]
        left, right = right, mixed
    return _bytes(_permute(right + left, _FP))  # the pre-output is swapped: no final exchange


def encrypt(key: bytes, data: bytes) -> bytes:
    """ECB over a whole message. Only used with lengths that are multiples of 8."""
    if len(data) % 8:
        raise ValueError("this cipher is only used on whole blocks; RFB sends 16 bytes")
    out = bytearray()
    for start in range(0, len(data), 8):
        out += encrypt_block(key, data[start : start + 8])
    return bytes(out)


def vnc_key(password: str) -> bytes:
    """The DES key a VNC server expects from a password.

    Truncated or zero-padded to eight bytes, then the bits of each byte reversed. The
    reversal is not exoticism: it is what the protocol says, and a client that "knows
    better" than the spec fails authentication against every server on the first try.
    """
    raw = password.encode("utf-8")[:8]
    padded = raw + bytes(8 - len(raw))
    return bytes(int(f"{byte:08b}"[::-1], 2) for byte in padded)


def respond_to_challenge(challenge: bytes, password: str) -> bytes:
    """What to send back to a server that asked with `challenge`."""
    if len(challenge) != 16:
        raise ValueError(f"a VNC challenge is 16 bytes, not {len(challenge)}")
    return encrypt(vnc_key(password), challenge)
