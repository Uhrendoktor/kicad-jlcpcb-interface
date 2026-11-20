from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-jlcpcb-interface"
sys.path.insert(0, str(PLUGIN))

from kicad_jlcpcb import plugin, provider  # noqa: E402

ComponentBrowser = plugin.ComponentBrowser
JlcPartDetails = provider.JlcPartDetails
PriceTier = provider.PriceTier

class AlternativeListingDisplayTests(unittest.TestCase):
    def test_basic_and_extended_rows_use_distinct_class_tags(self):
        basic = JlcPartDetails(lcsc="C1", is_basic=True)
        extended = JlcPartDetails(lcsc="C2", is_basic=False)

        self.assertEqual(ComponentBrowser._alternative_row_tags(basic, 0), ("basic",))
        self.assertEqual(
            ComponentBrowser._alternative_row_tags(extended, 1),
            ("extended", "stripe-alt"),
        )

    def test_match_indicators_explain_exact_related_and_passive_matches(self):
        exact = JlcPartDetails(lcsc="C1", mpn_match=True)
        related = JlcPartDetails(lcsc="C2", mpn_match=False)
        passive = JlcPartDetails(lcsc="C3")

        self.assertEqual(
            ComponentBrowser._alternative_match_indicator(exact),
            ("✓", "Exact manufacturer part-number match."),
        )
        related_icon, related_tooltip = ComponentBrowser._alternative_match_indicator(
            related
        )
        self.assertEqual(related_icon, "≈")
        self.assertIn("Related manufacturer part number", related_tooltip)
        passive_icon, passive_tooltip = ComponentBrowser._alternative_match_indicator(
            passive
        )
        self.assertEqual(passive_icon, "◇")
        self.assertIn("component value and footprint/package", passive_tooltip)

    def test_price_column_uses_the_cheapest_quantity_break(self):
        details = JlcPartDetails(
            lcsc="C1",
            unit_price_usd=0.01,
            buy_price_tiers=(
                PriceTier(quantity=1, unit_price_usd=0.01),
                PriceTier(quantity=1000, unit_price_usd=0.007),
                PriceTier(quantity=100, unit_price_usd=0.008),
            ),
        )

        self.assertEqual(
            ComponentBrowser._alternative_price(details),
            "USD 0.007000 @ 1,000+",
        )

    def test_price_column_falls_back_to_unit_price_when_no_breaks_are_returned(self):
        details = JlcPartDetails(lcsc="C1", unit_price_usd=0.0024)
        missing = JlcPartDetails(lcsc="C2")

        self.assertEqual(
            ComponentBrowser._alternative_price(details),
            "USD 0.002400",
        )
        self.assertEqual(ComponentBrowser._alternative_price(missing), "—")

    def test_missing_price_ladders_are_loaded_from_part_details(self):
        primary = JlcPartDetails(
            lcsc="C1",
            manufacturer_part_number="EXACT-MPN",
            buy_price_tiers=(PriceTier(quantity=1, unit_price_usd=0.01),),
        )
        alternative = JlcPartDetails(
            lcsc="C2",
            manufacturer_part_number="RELATED-MPN",
            mpn_match=False,
            unit_price_usd=0.02,
        )
        detailed_alternative = JlcPartDetails(
            lcsc="C2",
            buy_price_tiers=(
                PriceTier(quantity=1, unit_price_usd=0.02),
                PriceTier(quantity=500, unit_price_usd=0.012),
            ),
        )

        class Provider:
            def lookup(self, lcsc, *, force_refresh=False):
                self.assert_force_refresh = force_refresh
                return primary if lcsc == "C1" else detailed_alternative

            def search_by_mpn(self, _mpn):
                return [replace(primary, lcsc="C1"), alternative]

        component = type(
            "Component",
            (),
            {
                "part_code": "C1",
                "manufacturer_part_number": "EXACT-MPN",
                "reference": "U1",
                "value": "controller",
                "footprint": "",
            },
        )()
        details, _image, alternatives, _error = ComponentBrowser._load_catalogue_details(
            Provider(), component
        )

        self.assertIs(details, primary)
        self.assertEqual(
            ComponentBrowser._alternative_price(alternatives[1]),
            "USD 0.012000 @ 500+",
        )

    def test_stock_column_shows_only_the_count_for_available_parts(self):
        available = JlcPartDetails(lcsc="C1", stock=12500)
        preorder = JlcPartDetails(
            lcsc="C2", stock=0, is_buyable=True, can_presale_number=300
        )
        unavailable = JlcPartDetails(lcsc="C3", stock=0)

        self.assertEqual(ComponentBrowser._alternative_stock(available), "12,500")
        self.assertEqual(ComponentBrowser._alternative_stock(preorder), "Pre-order")
        self.assertEqual(ComponentBrowser._alternative_stock(unavailable), "Out of stock")


if __name__ == "__main__":
    unittest.main()
