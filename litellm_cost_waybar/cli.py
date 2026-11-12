import json
import calendar
import html
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import click


CONFIG_DIR = Path.home() / ".config" / "litellm-cost"
CONFIG_FILE = CONFIG_DIR / "config.yaml"


def _yaml_scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return json.loads(value)
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    if value.lower() in {"null", "~"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def _load_yaml(path):
    config = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        config[key.strip()] = _yaml_scalar(value)
    return config


def load_config():
    config = {
        "url": "https://litellm.example.com",
        "api_key": "",
        "daily_limit": 5.0,
        "monthly_limit": 100.0,
        "remaining_budget": False,
    }
    if CONFIG_FILE.exists():
        config.update(_load_yaml(CONFIG_FILE))
    return config


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in config.items():
        if isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, str):
            value = json.dumps(value)
        elif value is None:
            value = "null"
        lines.append(f"{key}: {value}")
    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(CONFIG_FILE, 0o600)


def configure(url, key, daily_limit, monthly_limit, remaining_budget):
    config = load_config()

    if url is not None:
        config["url"] = url
    if key is not None:
        config["api_key"] = key
    if daily_limit is not None:
        config["daily_limit"] = daily_limit
    if monthly_limit is not None:
        config["monthly_limit"] = monthly_limit
    if remaining_budget is not None:
        config["remaining_budget"] = remaining_budget

    save_config(config)
    click.echo(f"Saved config to {CONFIG_FILE}")


def _request_json(config, start, end):
    base_url = config["url"].rstrip("/")
    parsed_url = urllib.parse.urlsplit(base_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise RuntimeError("LiteLLM URL must use HTTPS")
    query = urllib.parse.urlencode(
        {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "page_size": "1000",
        }
    )
    url = base_url + "/user/daily/activity?" + query
    headers = {"Accept": "application/json"}
    if config.get("api_key"):
        headers["Authorization"] = f"Bearer {config['api_key']}"
    request = urllib.request.Request(url, headers=headers)
    try:
        # Bandit cannot infer the HTTPS scheme check above.
        with urllib.request.urlopen(request, timeout=8) as response:  # nosec B310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"LiteLLM returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach LiteLLM: {exc.reason}") from exc


def _spend_value(row):
    if isinstance(row, (int, float)):
        return float(row)
    if not isinstance(row, dict):
        return 0.0
    if isinstance(row.get("metrics"), dict):
        return _spend_value(row["metrics"])
    for key in ("spend", "total_spend", "cost", "total_cost", "daily_spend"):
        if key in row:
            try:
                return float(row[key] or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _row_date(row):
    if not isinstance(row, dict):
        return None
    for key in ("date", "day", "start_date", "request_date", "timestamp"):
        value = row.get(key)
        if not value:
            continue
        text = str(value)
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return datetime.strptime(text[:10], "%Y-%m-%d").date()
            except ValueError:
                pass
    return None


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "results", "report", "spend", "daily_spend"):
        if isinstance(payload.get(key), list):
            return payload[key]
    result = []
    for key, value in payload.items():
        if isinstance(key, str):
            try:
                datetime.strptime(key[:10], "%Y-%m-%d")
            except ValueError:
                continue
            result.append({"date": key[:10], "spend": value})
    return result


def _fetch_period(config, start, end):
    payload = _request_json(config, start, end)
    by_day = {}
    for row in _rows(payload):
        day = _row_date(row)
        if day is not None:
            key = day.isoformat()
            by_day[key] = by_day.get(key, 0.0) + _spend_value(row)
    if by_day:
        return sum(by_day.values()), by_day
    if isinstance(payload, dict):
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and "total_spend" in metadata:
            return _spend_value(metadata["total_spend"]), by_day
        for key in ("total_spend", "spend", "total_cost", "cost"):
            if key in payload:
                return _spend_value(payload[key]), by_day
    return sum(_spend_value(row) for row in _rows(payload)), by_day


def _percent(value, limit):
    if limit <= 0:
        return 0
    return max(0, round(value / limit * 100))


def _business_days_remaining(day):
    """Return the number of weekdays from ``day`` through month end."""
    last_day = calendar.monthrange(day.year, day.month)[1]
    month_end = day.replace(day=last_day)
    current = day
    count = 0

    while current <= month_end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)

    return count


def _remaining_budget(month_total, monthly_limit):
    return max(0.0, monthly_limit - month_total)


def _remaining_daily_budget(month_total, monthly_limit, today):
    remaining_days = _business_days_remaining(today)
    if remaining_days == 0:
        return 0.0, remaining_days
    return _remaining_budget(month_total, monthly_limit) / remaining_days, remaining_days


def waybar_output():
    config = load_config()

    try:
        today = date.today()
        month_total, month_days = _fetch_period(config, today.replace(day=1), today)
        today_total = month_days.get(today.isoformat())
        if today_total is None:
            today_total, _ = _fetch_period(config, today, today)
        daily = _percent(today_total, float(config["daily_limit"]))
        monthly = _percent(month_total, float(config["monthly_limit"]))
        peak = max(daily, monthly)
        status = "critical" if peak >= 100 else "warning" if peak >= 80 else "normal"
        tooltip_lines = [
            "LiteLLM spend",
            f"Today: ${today_total:.0f} / ${float(config['daily_limit']):.0f} ({daily}%)",
            f"Month: ${month_total:.0f} / ${float(config['monthly_limit']):.0f} ({monthly}%)",
        ]
        if config.get("remaining_budget"):
            daily_budget, remaining_days = _remaining_daily_budget(
                month_total, float(config["monthly_limit"]), today
            )
            tooltip_lines.append(
                f"Available per business day: ${daily_budget:.0f} "
                f"({remaining_days} business days remaining)"
            )
        if config.get("remaining_budget"):
            daily_text = f"${today_total:.0f} / ${daily_budget:.0f}"
        else:
            daily_text = f"${today_total:.0f} ({daily}%)"
        text = f"󰅐  {daily_text}  󰃭  ${month_total:.0f} ({monthly}%)"
        output = {
            "text": text,
            "tooltip": "\n".join(tooltip_lines),
            "class": status,
            "percentage": peak,
        }
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        output = {
            "text": "󰇁 --  󰃭 --",
            "tooltip": f"LiteLLM cost error: {exc}",
            "class": "error",
        }

    return output


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """LiteLLM cost monitor for Waybar."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command()
def waybar():
    """Print Waybar-compatible JSON."""
    click.echo(json.dumps(waybar_output(), ensure_ascii=False))


@cli.command()
def panel():
    """Print the compact panel value."""
    output = waybar_output()
    colors = {
        "normal": "#a6e3a1",
        "warning": "#f9e2af",
        "critical": "#f38ba8",
        "error": "#eba0ac",
    }
    color = colors.get(output.get("class"), "#cdd6f4")
    text = html.escape(output["text"])
    click.echo(
        f'<executor.markup.enabled><span foreground="{color}">{text}</span>'
    )


@cli.command()
def swiftbar():
    """Print SwiftBar/xbar menu bar output (macOS)."""
    output = waybar_output()
    colors = {
        "normal": "#2da44e",
        "warning": "#bf8700",
        "critical": "#cf222e",
        "error": "#cf222e",
    }
    color = colors.get(output.get("class"), "#808080")
    title = " ".join(re.sub(r"[^\x00-\x7f]", "", output["text"]).split())
    lines = [f"{title} | color={color}", "---"]
    lines.extend(output["tooltip"].splitlines())
    click.echo("\n".join(lines))


@cli.command(name="config")
@click.option("--url")
@click.option("--key", "set_key", is_flag=True, help="prompt for the API key")
@click.option("--daily-limit", type=float)
@click.option("--monthly-limit", type=float)
@click.option(
    "--remaining-budget/--no-remaining-budget",
    default=None,
    help="show the available amount per remaining business day in the tooltip",
)
def config_command(url, set_key, daily_limit, monthly_limit, remaining_budget):
    """Save LiteLLM cost monitor configuration."""
    key = click.prompt("LiteLLM API key", hide_input=True) if set_key else None
    configure(url, key, daily_limit, monthly_limit, remaining_budget)


def main():
    cli()


if __name__ == "__main__":
    main()
