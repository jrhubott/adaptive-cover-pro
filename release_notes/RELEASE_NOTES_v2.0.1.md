# Adaptive Cover Pro v2.0.1

## 🐛 Bug Fix Release

This patch release fixes repository URL references to ensure HACS displays the correct repository information.

## What's Fixed

### Repository References

- **manifest.json**: Updated `codeowners`, `documentation`, and `issue_tracker` URLs from basbruss to jrhubott
- **pyproject.toml**: Updated all repository, homepage, and bug tracker URLs
- **README.md**: Updated version badge and all documentation links

### HACS Display

- HACS will now correctly show **jrhubott/adaptive-cover** as the repository
- Documentation and issue tracker links now point to the correct fork

## Installation

### HACS

1. Add `https://github.com/jrhubott/adaptive-cover` as a custom repository to HACS
2. Search for "Adaptive Cover Pro" and install
3. Restart Home Assistant

### Upgrade from v2.0.0

If you installed v2.0.0:

1. In HACS, find "Adaptive Cover Pro"
2. Click update to v2.0.1
3. Restart Home Assistant (optional, but recommended to refresh cache)

### Manual

Download the latest release and copy the `custom_components/adaptive_cover_pro` folder to your Home Assistant `custom_components` directory.

## Changes Since v2.0.0

- Fix repository references in manifest.json
- Update all GitHub URLs to jrhubott fork
- Update README badges and documentation links

## Requirements

- Home Assistant 2024.5.0 or later
- Python 3.11+

---

**Full Changelog**: https://github.com/jrhubott/adaptive-cover/compare/v2.0.0...v2.0.1

For issues and support, please visit: https://github.com/jrhubott/adaptive-cover/issues
