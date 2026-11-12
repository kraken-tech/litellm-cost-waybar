# LiteLLM Cost Panel

A lightweight LiteLLM spend indicator for a GNOME top panel.

It queries LiteLLM's `/user/daily/activity` endpoint and displays today's and
month-to-date spend with their configured percentages:

```text
󰅐 $7.37 (7%)  󰃭 $379.31 (25%)
```

## Install

```bash
uv tool install .
```

For development:

```bash
uv tool install --editable .
```

## Configure

Configuration is stored outside the repository at
`~/.config/litellm-cost/config.yaml` with permissions `0600`:

```bash
litellm-cost config \
  --url https://litellm.example.com \
  --key \
  --daily-limit 10 \
  --monthly-limit 200 \
  --remaining-budget
```

With `--key`, the configuration command asks for the API key in a hidden
prompt. It is sent as `Authorization: Bearer ...` and stored in the config file
with permissions `0600`. Configured API keys are only sent to HTTPS URLs.

`--remaining-budget` is optional and adds a tooltip line showing how much can
be spent per business day without exceeding the monthly limit. It subtracts
month-to-date spend and divides the remainder by weekdays (Monday through
Friday) from today through the end of the month, including today when it is a
weekday. Use `--no-remaining-budget` to disable it.

## Commands

```bash
litellm-cost --help
litellm-cost waybar   # Waybar-compatible JSON
litellm-cost panel    # Plain text for GNOME Executor
```

## GNOME top panel

GNOME Shell 46 does not provide the layer-shell protocol required by Waybar.
Install the GNOME Shell **Executor** extension, then configure its right-side
command using the path to the installed executable:

```text
/path/to/litellm-cost panel
```

Set its interval to 60 seconds. Log out and back in after installing a new
GNOME Shell extension on Wayland.

GNOME Executor uses the `panel` command and does not read Waybar's JSON
tooltip field. When `--remaining-budget` is enabled, the per-workday amount is
therefore included directly in the panel text. The panel command also uses
Executor's markup support to color normal, warning, critical, and error states.

### Waybar colors

The command already emits a status class. Copy the rules from
[waybar.css](waybar.css) into your Waybar stylesheet to color the indicator
green, amber, or red according to its spend level.

## macOS (SwiftBar / xbar)

The `swiftbar` command prints SwiftBar/xbar menu bar output. Install the
tool, then create `~/Library/Application Support/SwiftBar/Plugins/litellm-cost.1m.sh`
(or the xbar equivalent) and make it executable:

```bash
#!/bin/bash
exec "$HOME/.local/bin/litellm-cost" swiftbar
```

The `1m` in the file name refreshes it every minute. The title is colored by
spend level and the tooltip lines appear in the dropdown. macOS support has
not been tested on a real Mac.
