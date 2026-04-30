![Version](https://img.shields.io/github/v/release/jrhubott/adaptive-cover-pro?style=for-the-badge)
![Tests](https://img.shields.io/github/actions/workflow/status/jrhubott/adaptive-cover-pro/tests.yml?branch=main&label=Tests&style=for-the-badge)
![Hassfest](https://img.shields.io/github/actions/workflow/status/jrhubott/adaptive-cover-pro/hassfest.yml?branch=main&label=Hassfest&style=for-the-badge)
![HACS](https://img.shields.io/github/actions/workflow/status/jrhubott/adaptive-cover-pro/hacs.yaml?branch=main&label=HACS&style=for-the-badge)
![Coverage](https://img.shields.io/codecov/c/github/jrhubott/adaptive-cover-pro?style=for-the-badge)

![logo](https://github.com/jrhubott/adaptive-cover-pro/blob/main/images/logo.png#gh-light-mode-only)
![logo](https://github.com/jrhubott/adaptive-cover-pro/blob/main/images/dark_logo.png#gh-dark-mode-only)

# Adaptive Cover Pro

Home Assistant custom integration that controls vertical blinds, horizontal awnings, and venetian (tilt) blinds based on sun position — filtering direct sunlight while maximizing natural light, with climate-aware operation.

> **📖 Full documentation lives on the [Wiki](https://github.com/jrhubott/adaptive-cover-pro/wiki).**

---

## What it does

- **Three cover types** — vertical blinds, horizontal awnings, venetian (tilt)
- **Basic & Climate modes** — geometric sun tracking, plus a temperature-aware strategy for winter / summer / intermediate
- **10-handler override pipeline** — force override → weather → manual → custom position → motion → cloud suppression → climate → glare zones → solar → default
- **Safety overrides** — force override (rain/wind/fire), weather safety (wind/rain), manual override that pauses on physical/app/voice moves
- **Always-on diagnostics** — decision trace, sun position, position verification; debug mode without touching YAML
- **15 runtime services** (v2.18.0+) — change any setting from automations and scripts without opening the Options UI

Dive deeper: **[How It Decides](https://github.com/jrhubott/adaptive-cover-pro/wiki/How-It-Decides)** · **[Climate Mode](https://github.com/jrhubott/adaptive-cover-pro/wiki/Climate-Mode)** · **[Enhanced Geometric Accuracy](https://github.com/jrhubott/adaptive-cover-pro/wiki/Enhanced-Geometric-Accuracy)**

## Companion Lovelace card

Pair with the **[Adaptive Cover Pro Card](https://github.com/jrhubott/adaptive-cover-pro-card)** — a custom Lovelace card that visualizes sun vs. window geometry, the full pipeline decision trace, and live cover positions, with inline override controls. HACS-installable as a Lovelace plugin. Requires no integration changes — it reads existing entities and calls existing services.

See the **[Lovelace Card](https://github.com/jrhubott/adaptive-cover-pro/wiki/Lovelace-Card)** wiki page for install, configuration, and usage. All card documentation — including developer setup — is maintained here in the integration wiki.

## Quick install

**HACS (recommended):** Add `https://github.com/jrhubott/adaptive-cover-pro` as a custom repository → search **Adaptive Cover Pro** → download → restart Home Assistant → add the integration.

**Manual:** Copy `custom_components/adaptive_cover_pro/` into `config/custom_components/`, restart Home Assistant, add the integration.

Full steps: **[Installation](https://github.com/jrhubott/adaptive-cover-pro/wiki/Installation)** · **[First-Time Setup](https://github.com/jrhubott/adaptive-cover-pro/wiki/First-Time-Setup)**

## Documentation jump-off

|                            |                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 🚀 **Getting started**     | [Installation](https://github.com/jrhubott/adaptive-cover-pro/wiki/Installation) · [First-Time Setup](https://github.com/jrhubott/adaptive-cover-pro/wiki/First-Time-Setup) · [Cover Types](https://github.com/jrhubott/adaptive-cover-pro/wiki/Cover-Types) · [Migrating from Adaptive Cover](https://github.com/jrhubott/adaptive-cover-pro/wiki/Migrating-from-Adaptive-Cover)                                                                                                  |
| 🧠 **How it works**        | [How It Decides](https://github.com/jrhubott/adaptive-cover-pro/wiki/How-It-Decides) · [Basic Mode](https://github.com/jrhubott/adaptive-cover-pro/wiki/Basic-Mode) · [Climate Mode](https://github.com/jrhubott/adaptive-cover-pro/wiki/Climate-Mode) · [Enhanced Geometric Accuracy](https://github.com/jrhubott/adaptive-cover-pro/wiki/Enhanced-Geometric-Accuracy)                                                                                                            |
| ⚙️ **Configuration**       | [Common](https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Common) · [Glare Zones](https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Glare-Zones) · [Weather Safety](https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Weather-Safety) · [Climate](https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Climate) · [Summary Screen](https://github.com/jrhubott/adaptive-cover-pro/wiki/Configuration-Summary-Screen) |
| 🔌 **Entities & services** | [Entities](https://github.com/jrhubott/adaptive-cover-pro/wiki/Entities) · [Runtime Configuration Services](https://github.com/jrhubott/adaptive-cover-pro/wiki/Runtime-Configuration-Services) · [Position Verification](https://github.com/jrhubott/adaptive-cover-pro/wiki/Position-Verification) · [Somfy RTS (My Position)](https://github.com/jrhubott/adaptive-cover-pro/wiki/My-Position-Support-Somfy-RTS)                                                                |
| 🎨 **Dashboard**           | [Lovelace Card](https://github.com/jrhubott/adaptive-cover-pro/wiki/Lovelace-Card) — companion card in a separate [repo](https://github.com/jrhubott/adaptive-cover-pro-card)                                                                                                                                                                                                                                                                                                      |
| 🛠️ **Operations**          | [Troubleshooting](https://github.com/jrhubott/adaptive-cover-pro/wiki/Troubleshooting) · [Known Limitations](https://github.com/jrhubott/adaptive-cover-pro/wiki/Known-Limitations)                                                                                                                                                                                                                                                                                                |
| 🧪 **Testing**             | [Testing the Algorithms](https://github.com/jrhubott/adaptive-cover-pro/wiki/Testing-the-Algorithms) · [Simulation Notebook](https://github.com/jrhubott/adaptive-cover-pro/wiki/Simulation-Notebook)                                                                                                                                                                                                                                                                              |

## Support

If Adaptive Cover Pro has saved you time or made your home a little smarter, you can fuel the next commit:

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20me%20a%20coffee-FFDD00?style=for-the-badge&logo=buy-me-a-coffee&logoColor=black)](https://www.buymeacoffee.com/jrhubott)

## Credits

Inspired by and originally forked from **[Adaptive Cover](https://github.com/basbruss/adaptive-cover)** by **[Bas Brussee (@basbruss)](https://github.com/basbruss)**, whose ideation and base implementation sparked this project. Adaptive Cover Pro has since grown into a substantially different codebase with a new architecture and feature set, but the original vision deserves real credit.

Original forum post that inspired both projects: [Automatic Blinds](https://community.home-assistant.io/t/automatic-blinds-sunscreen-control-based-on-sun-platform/).

## How this project is built

Adaptive Cover Pro is developed with substantial help from generative AI — specifically Anthropic's Claude, via the Claude Code CLI. AI assistance is used for code, tests, documentation, and release notes; I (the maintainer, 30+ years in software) own the architecture, vet every merge, and stay accountable for what ships. The test suite (over 2,500 tests) is the load-bearing safety net for AI-generated changes.

For the full workflow — model routing, code-review gates, and what stays in human hands — see **[AI-Assisted Development](https://github.com/jrhubott/adaptive-cover-pro/wiki/AI-Assisted-Development)** on the wiki.

## For developers

See the **[For Developers](https://github.com/jrhubott/adaptive-cover-pro/wiki/For-Developers)** wiki hub for setup, architecture, workflow, testing strategies, code standards, and the automated release process.
