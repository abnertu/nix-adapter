"""Standard exceptions for nix-adapter."""


class Conflict(Exception):
    """A state that needs an operator decision; deployment stops.

    The message names the path, unit, command or key involved. It never holds
    a secret: values from environment-style files stay out, and command
    output passes through `Native` redaction.
    """


class NotReady(Exception):
    """Required operator-owned configuration has never been prepared."""
