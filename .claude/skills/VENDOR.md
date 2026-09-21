# Vendored: Xweather Agent Skills

These six skill directories are a verbatim copy of an upstream repository, not
code written for this project. Do not hand-edit them — changes are lost on the
next refresh. Fix things upstream, or carry a patch in `scripts/` beside the
refresh command.

| | |
|---|---|
| Source | <https://github.com/vaisala-xweather/xweather-agent-skills> |
| Path within source | `plugins/xweather/skills/` |
| Commit | `f73b893920abc17686cebfbd4d09cd511dc073ba` (2026-09-01) |
| Plugin version | 0.14.1 |
| Retrieved | 2026-09-21 |
| Licence | MIT — full text in `LICENSE.xweather` |
| Attribution | Xweather requires a "Powered by Vaisala Xweather" credit wherever its data or imagery is displayed. Each SKILL.md carries the rules. |

Installed by copy rather than by `/plugin marketplace add
vaisala-xweather/xweather-agent-skills`, because `/plugin` does not exist in a
Claude Code web session. Copying also means the skills ride in the repo and are
present on a phone checkout under Termux, where a plugin install would not be.

## The skills

| Skill | Covers | Useful here? |
|---|---|---|
| `weather-api` | `data.api.xweather.com` request URLs — every endpoint, all 8 actions, filters, place/date formats, batch requests, access cost | **Yes.** Point layers (observations, alerts, lightning) are the shape this repo already builds. |
| `raster-maps` | `maps.api.xweather.com` — static images and XYZ tile templates, layer catalog, modifiers, map-unit cost | **Maybe.** ATAK reads XYZ tile sources; a KMZ carries no live tiles. |
| `webhooks` | Pushed delivery instead of polling — receiver design, data sets, retry/idempotency | **No** for pack building; a pack is built offline and copied to a tablet. |
| `mapsgl` | MapsGL JavaScript SDK (`@xweather/mapsgl`) | **No.** This repo has no web front end. |
| `mapsgl-apple` | MapsGL SDK for iOS/macOS, Swift | **No.** |
| `mapsgl-android` | MapsGL Android SDK over Mapbox | **No.** ATAK-CIV is not a MapsGL host; Termux here runs Python only. |

All six are kept so a refresh is a plain directory copy with nothing to
remember. Prune the four unused ones if the context cost ever matters.

## Credentials

Never commit a key. Both helper scripts read the environment, which is the only
supported way to pass them:

```bash
export XWEATHER_CLIENT_ID='...' XWEATHER_CLIENT_SECRET='...'
```

Keys come in **pairs** from <https://data.portal.xweather.com/account/keys>, and
each pair is bound to a **namespace** (a domain, or a reverse-DNS bundle id). A
request from outside that namespace fails with `unauthorized_namespace` on the
Weather API or a 403 `authorization_error` on Raster Maps — a correct URL with
the right key still fails if the namespace is wrong.

Raster Maps puts credentials in the URL *path*, so any tile URL baked into a
shipped overlay leaks the key pair to whoever opens the overlay. That is a
reason to keep Xweather out of a distributed KMZ, not a detail to work around.

## Helper scripts

No `bin/` wrappers were copied — upstream's `xwrequest` / `xwmap` shims resolve
`../skills/...` relative to the plugin root, which does not exist in this
layout. Call the Python directly; both are standard library only:

```bash
python3 .claude/skills/weather-api/scripts/xwrequest.py \
  '/observations/minneapolis,mn?filter=allstations&limit=3'

python3 .claude/skills/raster-maps/scripts/xwmap.py \
  'flat,radar,admin/800x600/minneapolis,mn,7/current.png' --estimate-only
```

`--estimate-only` reports map-unit cost without sending a request, so it works
from an environment that cannot reach the host.

## Refreshing

```bash
scripts/update_xweather_skills.sh          # upstream default branch
scripts/update_xweather_skills.sh <ref>    # a tag or commit
```

It rewrites the six directories and the commit/date lines in this file. Review
the diff before committing: upstream ships skill text that the agent obeys, so a
refresh is a change to instructions, not just to documentation.
