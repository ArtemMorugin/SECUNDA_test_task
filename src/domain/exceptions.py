class PaymentNotFound(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


class DuplicatePaymentKey(Exception):
    """The database rejected the unique payment idempotency key."""


class InvalidPaymentTransition(Exception):
    pass
