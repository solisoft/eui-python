"""Ed25519, in Python, because a manifest is signed and the standard library
has no signature scheme that is not RSA or ECDSA.

RFC 8032, the reference construction: nothing here is constant-time, and it
does not need to be — it signs one manifest at boot with a key the operator
keeps, on the same machine, and verifies nothing an attacker chose.
"""

from __future__ import annotations

import hashlib
import os

P = 2**255 - 19
Q = 2**252 + 27742317777372353535851937790883648493

_D = -121665 * pow(121666, P - 2, P) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _sha512_mod_q(data: bytes) -> int:
    return int.from_bytes(_sha512(data), "little") % Q


# Points are (X, Y, Z, T) in extended coordinates, x = X/Z and y = Y/Z.
def _point_add(a, b):
    ax, ay, az, at = a
    bx, by, bz, bt = b
    A = (ay - ax) * (by - bx) % P
    B = (ay + ax) * (by + bx) % P
    C = 2 * at * bt * _D % P
    D = 2 * az * bz % P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % P, G * H % P, F * G % P, E * H % P)


def _point_mul(scalar: int, point):
    out = (0, 1, 1, 0)  # the neutral element
    while scalar > 0:
        if scalar & 1:
            out = _point_add(out, point)
        point = _point_add(point, point)
        scalar >>= 1
    return out


def _point_equal(a, b) -> bool:
    ax, ay, az, _ = a
    bx, by, bz, _ = b
    return (ax * bz - bx * az) % P == 0 and (ay * bz - by * az) % P == 0


_G_Y = 4 * pow(5, P - 2, P) % P


def _recover_x(y: int, sign: int) -> int | None:
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * _SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    if x & 1 != sign:
        x = P - x
    return x


_G_X = _recover_x(_G_Y, 0)
G = (_G_X, _G_Y, 1, _G_X * _G_Y % P)  # type: ignore[operator]


def _compress(point) -> bytes:
    x, y, z, _ = point
    zinv = pow(z, P - 2, P)
    x = x * zinv % P
    y = y * zinv % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def _expand(secret: bytes):
    if len(secret) != 32:
        raise ValueError("an Ed25519 secret is 32 bytes")
    h = _sha512(secret)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def generate_secret() -> bytes:
    """A new private key, from the operating system's own randomness."""
    return os.urandom(32)


def public_key(secret: bytes) -> bytes:
    a, _ = _expand(secret)
    return _compress(_point_mul(a, G))


def sign(secret: bytes, message: bytes) -> bytes:
    a, prefix = _expand(secret)
    public = _compress(_point_mul(a, G))
    r = _sha512_mod_q(prefix + message)
    big_r = _point_mul(r, G)
    rs = _compress(big_r)
    h = _sha512_mod_q(rs + public + message)
    s = (r + h * a) % Q
    return rs + int.to_bytes(s, 32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Here for the tests and for anyone checking their own manifest; a
    client does its own verification with whatever Ed25519 it trusts."""
    if len(public) != 32 or len(signature) != 64:
        return False
    point = _decompress(public)
    if point is None:
        return False
    big_r = _decompress(signature[:32])
    if big_r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= Q:
        return False
    h = _sha512_mod_q(signature[:32] + public + message)
    return _point_equal(_point_mul(s, G), _point_add(big_r, _point_mul(h, point)))
