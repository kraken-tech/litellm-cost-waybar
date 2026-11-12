import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from litellm_cost_waybar import cli


class CliTests(unittest.TestCase):
    def test_business_days_are_counted_through_month_end(self):
        self.assertEqual(cli._business_days_remaining(date(2026, 8, 12)), 14)
        self.assertEqual(cli._business_days_remaining(date(2026, 8, 29)), 1)
        self.assertEqual(cli._business_days_remaining(date(2026, 8, 30)), 1)
        self.assertEqual(cli._business_days_remaining(date(2026, 8, 31)), 1)
        self.assertEqual(cli._business_days_remaining(date(2026, 9, 1)), 22)

    def test_remaining_budget_subtracts_month_to_date_spend(self):
        self.assertEqual(cli._remaining_budget(900.0, 1000.0), 100.0)

    def test_remaining_daily_budget_divides_by_business_days(self):
        daily_budget, remaining_days = cli._remaining_daily_budget(
            month_total=50.0,
            monthly_limit=200.0,
            today=date(2026, 8, 12),
        )
        self.assertEqual(remaining_days, 14)
        self.assertAlmostEqual(daily_budget, 150.0 / 14)
        self.assertEqual(
            cli._remaining_daily_budget(900.0, 1000.0, date(2026, 8, 31)),
            (100.0, 1),
        )
        daily_budget, remaining_days = cli._remaining_daily_budget(
            month_total=381.8,
            monthly_limit=1500.0,
            today=date(2026, 8, 12),
        )
        self.assertEqual(remaining_days, 14)
        self.assertAlmostEqual(daily_budget, 79.871428571)

    def test_remaining_budget_is_optional_and_configurable_with_click(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_dir = Path(temporary_dir)
            config_file = config_dir / "config.yaml"
            with patch.object(cli, "CONFIG_DIR", config_dir), patch.object(
                cli, "CONFIG_FILE", config_file
            ):
                result = runner.invoke(cli.cli, ["config", "--remaining-budget"])

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertTrue(cli.load_config()["remaining_budget"])

    def test_remaining_budget_is_only_added_to_tooltip_when_enabled(self):
        class FixedDate:
            @classmethod
            def today(cls):
                return date(2026, 8, 12)

        config = {
            "daily_limit": 20.0,
            "monthly_limit": 200.0,
            "remaining_budget": True,
        }
        with patch.object(cli, "load_config", return_value=config), patch.object(
            cli,
            "date",
            FixedDate,
        ), patch.object(
            cli,
            "_fetch_period",
            return_value=(157.46, {"2026-08-12": 0.55}),
        ):
            output = cli.waybar_output()

        self.assertIn(
            "Today: $1 / $20 (3%)",
            output["tooltip"],
        )
        self.assertIn(
            "Month: $157 / $200 (79%)",
            output["tooltip"],
        )
        self.assertIn(
            "Available per business day: $3 (14 business days remaining)",
            output["tooltip"],
        )
        self.assertIn("$1 / $3", output["text"])

    def test_panel_includes_daily_budget_for_gnome_executor(self):
        with patch.object(
            cli,
            "waybar_output",
            return_value={"text": "spend ($80.04/day)", "class": "normal"},
        ):
            result = CliRunner().invoke(cli.cli, ["panel"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            result.output,
            '<executor.markup.enabled><span foreground="#a6e3a1">'
            "spend ($80.04/day)</span>\n",
        )

    def test_swiftbar_output_has_title_separator_and_tooltip(self):
        output = {"text": "\U000f0150  $7 (7%)  \U000f00ed  $379 (25%)", "tooltip": "LiteLLM spend\nToday: $7", "class": "warning"}
        with patch.object(cli, "waybar_output", return_value=output):
            result = CliRunner().invoke(cli.cli, ["swiftbar"])
        self.assertEqual(
            result.output.splitlines(),
            ["$7 (7%) $379 (25%) | color=#bf8700", "---", "LiteLLM spend", "Today: $7"],
        )


if __name__ == "__main__":
    unittest.main()
