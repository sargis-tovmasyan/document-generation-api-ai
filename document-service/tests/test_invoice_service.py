import re
import unittest
from decimal import Decimal

from app.services.invoice_service import (
    build_pdf_filename,
    calculate_item_amount,
    format_decimal,
)


class InvoiceServiceTests(unittest.TestCase):
    def test_calculates_and_rounds_item_amount(self) -> None:
        amount = calculate_item_amount(Decimal("1.2345"), Decimal("10.00"))

        self.assertEqual(amount, Decimal("12.35"))

    def test_formats_decimal_without_float_conversion(self) -> None:
        self.assertEqual(format_decimal(Decimal("350.00")), "350.00")

    def test_builds_safe_unique_pdf_filename(self) -> None:
        filename = build_pdf_filename("../../INV 001?customer=Alex")

        self.assertRegex(filename, r"^INV-001-customer-Alex-[a-f0-9]{12}\.pdf$")
        self.assertNotIn("/", filename)
        self.assertNotIn("..", filename)

    def test_uses_fallback_for_invoice_number_without_safe_characters(self) -> None:
        filename = build_pdf_filename("***")

        self.assertTrue(re.fullmatch(r"invoice-[a-f0-9]{12}\.pdf", filename))


if __name__ == "__main__":
    unittest.main()
