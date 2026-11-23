from decimal import Decimal
import sqlite3

from cart import ShoppingCart
from checkout import CheckoutService
from models import Product
from payments import FakePaymentGateway
from repository import StoreRepository
from setup_db import initialize


class FakeRepository:
    """In-memory product repository for isolated cart unit tests."""

    def __init__(self, products):
        self._products = {p.sku: p for p in products}

    def get_product(self, sku):
        return self._products.get(sku)


class FailingPaymentGateway:
    """Payment gateway double that simulates an unexpected runtime failure."""

    def __init__(self):
        self.calls = []

    def charge(self, amount, payment_token):
        self.calls.append((amount, payment_token))
        raise RuntimeError('Payment gateway timeout')


def _make_repo(tmp_path):
    db = tmp_path / 'test.db'
    initialize(str(db))
    return StoreRepository(str(db)), str(db)


"""Case 1: Valid/invalid quantities, duplicate items, and cumulative stock limits."""
def test_add_item_validates_quantity_and_enforces_cumulative_stock():
    repo = FakeRepository([Product('LAP-01', 'Laptop', 999.99, 5)])
    cart = ShoppingCart('user-1', repo)

    assert cart.add_item('LAP-01', 0) is False
    assert cart.add_item('LAP-01', -2) is False
    assert cart.add_item('LAP-01', 1.5) is False
    assert cart.lines == []

    assert cart.add_item('LAP-01', 3) is True
    assert cart.add_item('LAP-01', 2) is True
    assert len(cart.lines) == 1
    assert cart.lines[0].quantity == 5

    # Cumulative quantity (5 + 1 = 6) exceeds stock (5).
    assert cart.add_item('LAP-01', 1) is False
    assert cart.lines[0].quantity == 5


"""Case 2: Item removal rejects invalid quantities and handles partial/full removal."""
def test_remove_item_validates_quantity_and_updates_lines():
    repo = FakeRepository([Product('MOU-01', 'Wireless mouse', 29.95, 20)])
    cart = ShoppingCart('user-1', repo)
    assert cart.add_item('MOU-01', 5) is True

    assert cart.remove_item('MOU-01', 0) is False
    assert cart.remove_item('MOU-01', -2) is False
    assert cart.lines[0].quantity == 5

    assert cart.remove_item('MOU-01', 2) is True
    assert cart.lines[0].quantity == 3

    assert cart.remove_item('MOU-01', 10) is True
    assert cart.lines == []

    assert cart.add_item('MOU-01', 2) is True
    assert cart.remove_item('MOU-01') is True
    assert cart.lines == []
    assert cart.remove_item('UNKNOWN') is False


"""Case 3: Exact monetary arithmetic avoids binary floating-point drift and rounds to cents."""
def test_subtotal_and_discount_monetary_precision():
    repo = FakeRepository([
        Product('PEN-01', 'Pen', 0.10, 10),
        Product('PAD-01', 'Notepad', 0.20, 10),
        Product('USB-01', 'USB cable', 9.99, 50),
    ])

    cart = ShoppingCart('user-1', repo)
    cart.add_item('PEN-01', 1)
    cart.add_item('PAD-01', 1)
    assert Decimal(str(cart.subtotal())) == Decimal('0.30')

    discounted_cart = ShoppingCart('user-2', repo)
    discounted_cart.add_item('USB-01', 1)
    assert discounted_cart.apply_discount('WELCOME10') is True
    assert Decimal(str(discounted_cart.total())) == Decimal('8.99')


"""Case 4: Discounts recalculate after edits, do not stack, and floor at zero."""
def test_discounts_recalculate_after_edits_prevent_stacking_and_floor_at_zero():
    repo = FakeRepository([
        Product('MOU-01', 'Wireless mouse', 29.95, 20),
        Product('USB-01', 'USB cable', 9.99, 50),
    ])
    cart = ShoppingCart('user-1', repo)
    cart.add_item('MOU-01', 1)
    assert cart.apply_discount('WELCOME10') is True

    # Editing cart after applying discount must recalculate total from updated subtotal (59.90 * 0.9 = 53.91).
    cart.add_item('MOU-01', 1)
    assert Decimal(str(cart.total())) == Decimal('53.91')

    # Applying a second discount replaces the previous one rather than stacking (59.90 * 0.8 = 47.92).
    assert cart.apply_discount('STUDENT20') is True
    assert Decimal(str(cart.total())) == Decimal('47.92')

    # Invalid coupon does not alter the active discount.
    assert cart.apply_discount('NOTACOUPON') is False
    assert Decimal(str(cart.total())) == Decimal('47.92')

    # Boundary case: fixed discount larger than subtotal floors total at 0.
    cart.remove_item('MOU-01')
    cart.add_item('USB-01', 1)
    assert cart.apply_discount('SAVE15') is True
    assert Decimal(str(cart.total())) == Decimal('0')


"""Case 5: Saving and repeatedly loading a cart is idempotent."""
def test_saved_cart_and_repeated_loads(tmp_path):
    repo, _ = _make_repo(tmp_path)

    cart = ShoppingCart('user-save', repo)
    cart.add_item('MOU-01', 2)
    cart.add_item('USB-01', 3)
    cart.save()

    reloaded = ShoppingCart('user-save', repo)
    reloaded.load()
    reloaded.load()
    assert {line.sku: line.quantity for line in reloaded.lines} == {
        'MOU-01': 2,
        'USB-01': 3,
    }

    reloaded.remove_item('USB-01')
    reloaded.save()

    reloaded_again = ShoppingCart('user-save', repo)
    reloaded_again.load()
    assert {line.sku: line.quantity for line in reloaded_again.lines} == {'MOU-01': 2}


"""Case 6: Parameterized repository queries prevent SQL injection."""
def test_repository_queries_block_sql_injection(tmp_path):
    repo, _ = _make_repo(tmp_path)

    assert repo.get_product('MOU-01').name == 'Wireless mouse'
    assert repo.get_product("' OR '1'='1") is None

    cart = ShoppingCart('victim', repo)
    cart.add_item('MOU-01', 1)
    cart.save()

    assert repo.get_saved_cart("' OR '1'='1") == []


"""Case 7: Order history listing filters strictly by user_id."""
def test_list_orders_filters_by_user_id(tmp_path):
    repo, _ = _make_repo(tmp_path)

    repo.save_order('alice', 29.95, 'paid', 'req-alice-1')
    repo.save_order('bob', 999.99, 'paid', 'req-bob-1')

    alice_orders = repo.list_orders('alice')
    assert len(alice_orders) == 1
    assert alice_orders[0][1] == 'alice'


"""Case 8: Checking out an empty cart does not charge or create an order."""
def test_checkout_empty_cart(tmp_path):
    repo, _ = _make_repo(tmp_path)
    gateway = FakePaymentGateway(should_approve=True)
    cart = ShoppingCart('user-empty', repo)

    result = CheckoutService(repo, gateway).checkout(cart, 'TEST_TOKEN', 'req-empty-1')

    assert result.order_id is None
    assert result.status == 'empty'
    assert Decimal(str(result.total)) == Decimal('0')
    assert gateway.calls == []
    assert repo.list_orders('user-empty') == []


"""Case 9: Declined payment preserves inventory and the user's cart."""
def test_checkout_declined_payment_preserves_stock_and_cart(tmp_path):
    repo, _ = _make_repo(tmp_path)
    gateway = FakePaymentGateway(should_approve=False)

    cart = ShoppingCart('user-declined', repo)
    cart.add_item('LAP-01', 2)
    cart.save()

    result = CheckoutService(repo, gateway).checkout(cart, 'DECLINED_TOKEN', 'req-declined-1')

    assert result.status == 'declined'
    assert repo.get_product('LAP-01').stock == 5
    assert len(cart.lines) == 1
    assert cart.lines[0].quantity == 2
    assert repo.get_saved_cart('user-declined') == [('LAP-01', 2)]


"""Case 10: Unexpected payment gateway exceptions preserve inventory and cart."""
def test_checkout_payment_exception_preserves_stock_and_cart(tmp_path):
    repo, _ = _make_repo(tmp_path)
    gateway = FailingPaymentGateway()

    cart = ShoppingCart('user-error', repo)
    cart.add_item('MOU-01', 3)
    cart.save()

    result = CheckoutService(repo, gateway).checkout(cart, 'ERROR_TOKEN', 'req-error-1')

    assert result.status == 'payment_error'
    assert repo.get_product('MOU-01').stock == 20
    assert len(cart.lines) == 1
    assert cart.lines[0].quantity == 3
    assert repo.get_saved_cart('user-error') == [('MOU-01', 3)]


"""Case 11: Checkout re-verifies live stock before charging and prevents overselling."""
def test_checkout_rejects_unavailable_stock_without_charging(tmp_path):
    repo, db_path = _make_repo(tmp_path)
    gateway = FakePaymentGateway(should_approve=True)

    cart = ShoppingCart('user-oos', repo)
    cart.add_item('LAP-01', 4)
    cart.save()

    # Simulate concurrent stock reduction after item was added to cart.
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE products SET stock = 2 WHERE sku = 'LAP-01'")

    result = CheckoutService(repo, gateway).checkout(cart, 'TEST_TOKEN', 'req-oos-1')

    assert result.status != 'paid'
    assert gateway.calls == []
    assert repo.get_product('LAP-01').stock == 2
    assert len(cart.lines) == 1


"""Case 12: Repeated checkout requests are idempotent and do not log raw payment tokens."""
def test_checkout_idempotency_and_no_plaintext_token_logging(tmp_path, capsys):
    repo, _ = _make_repo(tmp_path)
    gateway = FakePaymentGateway(should_approve=True)
    service = CheckoutService(repo, gateway)

    cart = ShoppingCart('user-idem', repo)
    cart.add_item('MOU-01', 2)
    cart.apply_discount('WELCOME10')
    cart.save()

    secret_token = 'TOK_SECRET_9999'
    first = service.checkout(cart, secret_token, 'req-idem-1')

    assert first.status == 'paid'
    assert first.order_id is not None
    assert Decimal(str(first.total)) == Decimal('53.91')
    assert repo.get_product('MOU-01').stock == 18
    assert cart.lines == []
    assert repo.get_saved_cart('user-idem') == []
    assert secret_token not in capsys.readouterr().out

    # Repeating the same request_id must return the existing order without charging again.
    retry_cart = ShoppingCart('user-idem', repo)
    retry_cart.add_item('MOU-01', 2)
    retry_cart.apply_discount('WELCOME10')
    second = service.checkout(retry_cart, secret_token, 'req-idem-1')

    assert second.order_id == first.order_id
    assert second.status == 'paid'
    assert len(gateway.calls) == 1
    assert repo.get_product('MOU-01').stock == 18
    assert len(repo.list_orders('user-idem')) == 1