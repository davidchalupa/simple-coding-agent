import unittest
import json
import tempfile
import os
from ecommerce_order_processor import ECommerceOrderProcessor


class TestECommerceOrderProcessor(unittest.TestCase):
    def setUp(self):
        # Reset the processor before each test
        self.processor = ECommerceOrderProcessor("mock://db:1234/test")

    def test_validate_order_valid(self):
        data = {"customer_id": "CUST-123", "items": [{"id": 1, "price": 10, "qty": 1}]}
        self.assertTrue(self.processor.validate_order(data))

    def test_validate_order_invalid(self):
        data_missing_items = {"customer_id": "CUST-123", "items": []}
        data_missing_customer = {"items": [{"id": 1, "price": 10}]}

        self.assertFalse(self.processor.validate_order(data_missing_items))
        self.assertFalse(self.processor.validate_order(data_missing_customer))

    def test_calculate_total_no_discount(self):
        data = {"items": [{"price": 10.0, "qty": 2}]}
        total = self.processor.calculate_total(data)
        # Subtotal: $20.00. Tax (8%): $1.60. Expected Total: $21.60
        self.assertAlmostEqual(total, 21.60)

    def test_calculate_total_with_discount(self):
        data = {
            "items": [{"price": 10.0, "qty": 2}],
            "discount_code": "SAVE10"
        }
        total = self.processor.calculate_total(data)
        # Subtotal: $20.00. Discount (10%): -$2.00 = $18.00
        # Tax (8% of $18): $1.44. Expected Total: $19.44
        self.assertAlmostEqual(total, 19.44)

    def test_process_order_integration(self):
        order_data = {
            "order_id": "ORD-999",
            "customer_id": "CUST-1",
            "items": [{"price": 50.0, "qty": 2}],
            "discount_code": "HALFOFF"
        }

        # Write to a temporary file to test the load_order_data pipeline natively
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
            json.dump(order_data, tmp)
            tmp_path = tmp.name

        try:
            result = self.processor.process_order(tmp_path)

            self.assertTrue(result)
            self.assertEqual(len(self.processor.executed_queries), 1)

            # Subtotal: $100. Discount 50%: $50. Tax (8%): $4. Total: $54.0
            last_query = self.processor.executed_queries[0]
            self.assertIn("ORD-999", last_query)
            self.assertIn("CUST-1", last_query)
            self.assertIn("54.0", last_query)
        finally:
            os.remove(tmp_path)


if __name__ == '__main__':
    unittest.main()
