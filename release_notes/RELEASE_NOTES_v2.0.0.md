# Adaptive Cover Pro v2.0.0

## 🎉 Major Release - Adaptive Cover Pro

This is a major release introducing **Adaptive Cover Pro** - a new, independent integration that can run side-by-side with the original Adaptive Cover.

## 🚨 Breaking Changes

### New Domain

- **Domain changed**: `adaptive_cover` → `adaptive_cover_pro`
- **Integration name**: "Adaptive Cover" → "Adaptive Cover Pro"
- **Entity IDs**: All entity IDs now use `adaptive_cover_pro.*` domain
- This is a **separate integration** - both original and Pro can be installed together

### Entity Renames

- **Automatic Control Switch**: Entity renamed from `switch.{type}_toggle_control_{name}` to `switch.{type}_automatic_control_{name}`
- Internal property `control_toggle` renamed to `automatic_control`

## ⚠️ Migration Required

Users upgrading will need to:

1. Uninstall the old integration (optional - can keep both)
2. Install Adaptive Cover Pro as a new integration
3. Reconfigure all settings
4. Update all automations and scripts to use new entity IDs
5. Update dashboards to reference new entities

## ✨ What's New

- Clearer entity naming: "Automatic Control" better describes the functionality
- Can coexist with original Adaptive Cover for gradual migration
- Fresh start with v2.0.0 versioning

## 📦 Installation

### HACS

Add `https://github.com/jrhubott/adaptive-cover` as a custom repository to HACS.
Search for "Adaptive Cover Pro" and install.

### Manual

Download the latest release and copy the `custom_components/adaptive_cover_pro` folder to your Home Assistant `custom_components` directory.

## 🔧 Requirements

- Home Assistant 2024.5.0 or later
- Python 3.11+

## 📝 Full Changelog

**Changes:**

- Rename integration to Adaptive Cover Pro with new domain
- Rename "Toggle Control" to "Automatic Control"
- Update version to 2.0.0
- Update all documentation and badges to point to jrhubott fork

**Co-Authored-By:** Claude Sonnet 4.5

---

For issues and support, please visit: https://github.com/jrhubott/adaptive-cover/issues
