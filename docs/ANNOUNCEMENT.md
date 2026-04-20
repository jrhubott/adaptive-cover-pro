# 🌞 Adaptive Cover Pro - Intelligent Sun-Tracking Blind Control

Automatically control your blinds, awnings, and shutters based on the sun's position to block direct sunlight while maximizing natural light!

**Adaptive Cover Pro** calculates optimal cover positions throughout the day by tracking the sun's azimuth and elevation, keeping your home comfortable and energy-efficient.

## ✨ Key Features

- **Three Cover Types**: Vertical blinds, horizontal awnings, and tilted/venetian blinds
- **Climate-Aware**: Adjusts strategy based on temperature, weather, and occupancy
  - Winter: Opens to gain solar heat
  - Summer: Closes to prevent overheating
  - Intermediate: Tracks sun position for optimal shading
- **Smart Control**:
  - Automatic manual override detection
  - Configurable automation timing and thresholds
  - Support for both position-capable and open/close-only covers
  - **Position verification and automatic repositioning** 🆕
- **Advanced Options**:
  - Blind spot configuration for obstacles (trees, buildings)
  - Min/max position limits (with optional direct-sun-only enforcement)
  - Sunrise/sunset offset handling
  - Comprehensive diagnostic sensors for troubleshooting

## 🆕 Recent Improvements (v2.6.8 - Latest)

### Position Verification & Reliability (v2.6.7-2.6.8)
- ✅ **Periodic position verification** - Automatically detects when covers drift from target position
- ✅ **Smart repositioning** - Retries with exponential backoff if position doesn't match
- ✅ **Position mismatch detection** - Binary sensor alerts when covers drift
- ✅ **Retry tracking** - Monitor verification attempts with statistics
- ✅ **Fixed critical bugs** - Position verification sensors now work correctly with full statistics support

### Diagnostic & Testing (v2.6.0-2.6.1)
- ✅ **Comprehensive diagnostic sensors** - 8 diagnostic entities (4 enabled by default, 4 optional)
  - Last Cover Action, Sun Position, Control Status, Calculated Position
  - Active Temperature, Climate Conditions, Time Window, Sun Validity
- ✅ **Expanded test coverage** - 91% coverage for core calculation logic
- ✅ **Enhanced testing infrastructure** - 178 automated tests passing
- ✅ **Developer documentation** - 1000+ line comprehensive developer guide

### Configuration & Usability (v2.6.2-2.6.6)
- ✅ **Enhanced config flow UI** - Clear field descriptions in all supported languages
- ✅ **Min/max position documentation** - Complete docs for position limit features
- ✅ **Automated release tooling** - Streamlined release process for faster updates

## 🚀 Planned Enhancements

- **Manual Override Improvements**: Wait until next manual change option
- **Unit System Support**: Automatic °F/°C and meter/feet conversions
- Enhanced climate strategies and automation options
- Additional diagnostic sensors for advanced troubleshooting

## 📦 Installation

### HACS (Recommended)

1. Open HACS → Integrations
2. Click the three dots (⋮) → Custom repositories
3. Add repository: `https://github.com/jrhubott/adaptive-cover`
4. Category: Integration
5. Click "Download" on the Adaptive Cover Pro card
6. Restart Home Assistant
7. Add via Settings → Devices & Services → Add Integration

### Manual

Download the latest release and copy the `custom_components/adaptive_cover_pro` folder to your Home Assistant `custom_components` directory.

## 📖 Documentation

Full documentation, configuration guide, and examples available in the repository:

**https://github.com/jrhubott/adaptive-cover**

## 🙏 Credits

This integration is a fork of the excellent [Adaptive Cover](https://github.com/basbruss/adaptive-cover) by **[Bas Brussee (@basbruss)](https://github.com/basbruss)**. The core functionality and architecture are based on his outstanding work.

## 💬 Support & Discussion

Found a bug or have a feature request? Please open an issue on [GitHub](https://github.com/jrhubott/adaptive-cover/issues).

Have questions or want to share your setup? Join the discussion in this thread!

---

**Latest Version: v2.6.8** | *Compatible with Home Assistant 2024.5.0+ | Python 3.11+*
