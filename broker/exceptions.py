class BrokerError(Exception):
    """Raised when a broker adapter call fails, wrapping the underlying SDK error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
