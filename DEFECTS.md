# Identified Defects
## Meaningful Reliability Problems

### 1. SQL Injection in Database Queries (`repository.py`)
`StoreRepository.get_product()` and `StoreRepository.get_saved_cart()` build SQL queries using f-string interpolation with `sku` and `user_id` instead of parameterized queries

### 2. Stock Decremented Before Payment Without Rollback or Cart Preservation (`checkout.py`)
`CheckoutService.checkout()` deducts inventory via `update_stock()` before calling `payment_gateway.charge()` and without a shared database transaction.

### 3. Stale Discount Totals, Coupon Stacking, and Negative Totals (`cart.py`)
`ShoppingCart.apply_discount()` computes discounts against `self.total()` and stores a static snapshot in `self.discounted_total.` Editing the cart afterward leaves `total()` returning the stale discounted amount, applying coupons multiple times stacks discounts, and applying SAVE15 to a small or repeated total can produce a negative order amount.

### 4. Missing Input Validation and Broken Cumulative Stock Checks (`cart.py`)
`ShoppingCart.add_item()` does not validate that quantity is a positive integer, and when adding to an existing cart line it checks `product.stock < quantity` instead of checking `existing.quantity + quantity` against available stock. Similarly, `remove_item()` does not validate that quantity is positive, so a negative value increases the item quantity in the cart.

### 5. Missing Repeating Operation Protection for Duplicate Checkouts (`checkout.py`, `repository.py`, `setup_db.py`)
The orders table defines `request_id` without a `UNIQUE` constraint, and `CheckoutService.checkout()` never verifies whether a `request_id` has already been processed (nor does it reject missing `request_id` values).

### 6. Non-Atomic Stock Updates and Unverified Inventory at Checkout (`repository.py`, `checkout.py`)
`StoreRepository.update_stock()` subtracts quantity from stock without verifying that sufficient stock remains, and `CheckoutService.checkout()` never re-checks live stock or current prices before charging.

### 7. Floating-Point Precision Errors in Monetary Calculations (`models.py`, `cart.py`, `setup_db.py`)
Prices, totals, and order amounts use binary floating-point values (`float` and SQLite `REAL`) rather than exact decimal arithmetic or integer minor units. Subtotal calculations and percentage discounts (`0.9`, `0.8`) can introduce rounding inaccuracies without currency normalization.

### 8. Missing User Filter in `list_orders` (`repository.py`)
`StoreRepository.list_orders(user_id)` accepts a `user_id` argument but queries all rows from orders without a `WHERE user_id = ?` clause. Calling this method leaks order records belonging to other users.
