"""Domain errors - BudgetError with code and safe message."""


class BudgetError(ValueError):
    """Domain errors with machine-readable code and human-safe message.
    
    Subclass of ValueError as per CONTRACTS.md.
    """
    
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
