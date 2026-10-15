from functools import partial

from src.application.interfaces.payments import TransactionFactory
from src.application.use_cases.payments import (
    CreatePayment,
    GetPayment,
    ProcessPayment,
    simulate_payment,
)
from src.config.database import AsyncSessionFactory
from src.config.settings import get_settings
from src.infrastructures.db.repositories.transaction import transaction_factory


def get_transactions() -> TransactionFactory:
    return transaction_factory(AsyncSessionFactory)


def get_create_payment() -> CreatePayment:
    return CreatePayment(get_transactions())


def get_payment() -> GetPayment:
    return GetPayment(get_transactions())


def get_process_payment() -> ProcessPayment:
    settings = get_settings()
    return ProcessPayment(
        get_transactions(),
        partial(
            simulate_payment,
            settings.payment_processing_min_delay,
            settings.payment_processing_max_delay,
        ),
    )
