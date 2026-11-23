import sqlite3
from setup_db import initialize
from repository import StoreRepository
from payments import FakePaymentGateway
from cart import ShoppingCart
from checkout import CheckoutService

"""Case 1"""
def test_name(tmp_path):
    db = tmp_path / 'test.db'
    initialize(str(db))
    assert StoreRepository(str(db)).get_product('MOU-01').name == 'Wireless mouse'
