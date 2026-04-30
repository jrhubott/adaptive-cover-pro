# Release Notes

This directory contains historical release notes for all versions of Adaptive Cover Pro.

## File Naming Convention

- **Production releases:** `vX.Y.Z.md` (e.g., `v2.6.11.md`)
- **Beta releases:** `vX.Y.Z-beta.N.md` (e.g., `v2.7.0-beta.1.md`)

## Purpose

- Provides historical tracking of all releases
- Documents features, bug fixes, and changes for each version
- Used by the release automation script (`./scripts/release`)
- Committed to git for version control

## Usage

When creating a release, the release notes file is:

1. Created in this directory with the version number as the filename
2. Passed to the release script via `--notes` parameter
3. Published to the GitHub release
4. Committed to git for historical reference

## Example

```bash
# Create release notes
cat > release_notes/v2.6.11.md << 'EOF'
## 🐛 Bug Fix Release

### 🐛 Bug Fixes
- Fixed issue with sensor appearing in Activity log
EOF

# Create release
./scripts/release patch --notes release_notes/v2.6.11.md --yes

# Commit release notes
git add release_notes/v2.6.11.md
git commit -m "docs: Add release notes for v2.6.11"
git push origin main
```
