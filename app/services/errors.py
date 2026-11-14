class NotFound(Exception):
    pass


class Conflict(Exception):
    """Request is valid but not allowed in the current state (HTTP 409)."""
