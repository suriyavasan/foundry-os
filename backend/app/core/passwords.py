"""Password hashing (Argon2id) and the password policy.

Hashes are Argon2id in the standard encoded form, so the salt (16 random bytes, per hash) and the
cost parameters travel with the hash: ``needs_rehash`` compares them to the current settings and
the login path upgrades a hash that is weaker than today's policy.

The policy is a small zxcvbn-style estimator rather than a dependency: minimum length, a list of
the most common passwords (also matched after "leet" normalisation and with digits/symbols
stripped), personal information (username, name, email), keyboard and alphabet runs, repeated
blocks and a character-diversity estimate. It never echoes the password back.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2 import Type as Argon2Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256  # bounds Argon2 work per request


def build_hasher(settings: Settings) -> PasswordHasher:
    return PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_kib,
        parallelism=settings.argon2_parallelism,
        hash_len=32,
        salt_len=16,
        type=Argon2Type.ID,
    )


def hash_password(hasher: PasswordHasher, password: str) -> str:
    return hasher.hash(password)


def verify_password(hasher: PasswordHasher, encoded: str | None, password: str) -> bool:
    if not encoded or len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        return hasher.verify(encoded, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hasher: PasswordHasher, encoded: str) -> bool:
    try:
        return hasher.check_needs_rehash(encoded)
    except InvalidHashError:
        return True


# --------------------------------------------------------------------------- policy

# The most-used passwords and the words people build theirs from. Compared after normalisation.
_COMMON = frozenset(
    """
         password passw0rd passwort pass word admin administrator root toor login welcome
    letmein     secret changeme default master dragon monkey football baseball soccer hockey
    basketball     qwerty qwertyuiop qwertz azerty asdf asdfgh asdfghjkl zxcv zxcvbn zxcvbnm
    iloveyou love     sunshine princess shadow superman batman spiderman starwars pokemon
    trustno1 whatever     freedom computer internet google facebook twitter youtube amazon apple
    microsoft linux     ubuntu windows hello hello123 abc123 abcdef abcdefg abcd1234 test
    testing test123 guest user     username owner company companyos founder cofounder buildo
    startup business money summer     winter spring autumn december january september monday
    tuesday friday sunday killer hunter     ranger jordan michael jessica ashley nicole daniel
    andrew thomas robert charlie cheese     cookie chocolate banana orange purple yellow silver
    golden diamond mustang ferrari porsche     camaro harley matrix pepper maverick hannah
    tigger butterfly
    """.split()  # noqa: SIM905
)
_SEQUENCES = (
    "abcdefghijklmnopqrstuvwxyz",
    "01234567890",
    "qwertyuiopasdfghjklzxcvbnm",
    "qazwsxedcrfvtgbyhnujmikolp",
    "1qaz2wsx3edc4rfv5tgb",
)
_LEET = str.maketrans(
    {"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i"}
)


@dataclass(frozen=True)
class PasswordVerdict:
    ok: bool
    message: str | None = None
    score: float = 0.0  # estimated bits of entropy


def _has_run(text: str, length: int = 5) -> bool:
    low = text.lower()
    for seq in _SEQUENCES:
        for s in (seq, seq[::-1]):
            for i in range(len(s) - length + 1):
                if s[i : i + length] in low:
                    return True
    return False


def _repeated_block(text: str) -> bool:
    """The whole password is one short block repeated ("abab...", "hunter2hunter2")."""
    n = len(text)
    return any(n % size == 0 and text[:size] * (n // size) == text for size in range(1, n // 2 + 1))


def _entropy_bits(password: str) -> float:
    pool = 0
    if re.search(r"[a-z]", password):
        pool += 26
    if re.search(r"[A-Z]", password):
        pool += 26
    if re.search(r"\d", password):
        pool += 10
    if re.search(r"[^A-Za-z0-9]", password):
        pool += 33
    unique = len(set(password))
    # Repeats add little: count distinct characters fully, the rest at a fraction.
    effective = unique + 0.25 * (len(password) - unique)
    return effective * math.log2(max(pool, 2))


def check_password_strength(
    password: str, *, personal: tuple[str | None, ...] = ()
) -> PasswordVerdict:
    """Policy for new passwords. ``personal``: username, display name, email (and local part)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return PasswordVerdict(False, f"Use at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        return PasswordVerdict(False, f"Use at most {MAX_PASSWORD_LENGTH} characters")
    if password.strip() != password or not password.strip():
        return PasswordVerdict(False, "Remove leading and trailing spaces")
    low = password.lower()
    norm = low.translate(_LEET)
    letters = re.sub(r"[^a-z]", "", norm)
    for source in (low, norm, letters):
        if source in _COMMON:
            return PasswordVerdict(False, "That is a very common password")
    # a common word padded with digits/symbols ("Password123456!") is still that word
    stripped = re.sub(r"[^a-z]", "", norm)
    for word in _COMMON:
        if len(word) >= 5 and word in stripped and len(stripped) <= len(word) + 5:
            return PasswordVerdict(False, "That is too close to a common password")
    if len(set(password)) < 5:
        return PasswordVerdict(False, "Use a greater variety of characters")
    if _has_run(password, 6):
        return PasswordVerdict(False, "Avoid keyboard and alphabet sequences")
    if _repeated_block(password):
        return PasswordVerdict(False, "Avoid repeated patterns")
    squashed = re.sub(r"[^a-z0-9]", "", low)
    for item in personal:
        if not item:
            continue
        for part in {item.lower(), item.lower().split("@")[0]}:
            part = re.sub(r"[^a-z0-9]", "", part)
            if len(part) >= 4 and part in squashed:
                return PasswordVerdict(False, "Do not include your name, username or email")
    bits = _entropy_bits(password)
    if bits < 50:
        return PasswordVerdict(False, "Make it longer or mix in more different characters", bits)
    return PasswordVerdict(True, None, bits)
