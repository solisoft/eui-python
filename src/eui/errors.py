"""What this library raises on its own."""


class EUIError(Exception):
    """Anything this library raises."""


class DecodeError(EUIError):
    """Bytes that are not a legal encoding of what they claim to be.

    The decoder refuses rather than repairs: a non-minimal varint, a value
    outside an enumeration, a length that does not account for every byte.
    "Ignore what you don't understand" is how one implementation's frame
    becomes another's smuggling channel.
    """


class ViewError(EUIError):
    """A view the protocol has no way to carry.

    An unknown style key, a colour role nobody defined, a tree deeper than
    the client accepts. Raised while encoding, which is the only moment at
    which the author can still fix it.
    """
