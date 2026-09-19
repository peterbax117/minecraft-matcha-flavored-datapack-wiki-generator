# Minecraft Datapack Wiki Generator

Builds an offline-capable, searchable local wiki from a combined Minecraft
datapack and resource-pack ZIP.

## Current coverage

- Pack metadata and source SHA-256
- Recipes from every namespace
- Custom item names, models, lore, and textures
- Forward and reverse recipe lookup
- Food properties and consume effects
- Custom enchantment behavior, triggers, scaling, equipment, and source commands
- Blessing contents and crafting relationships
- Recursive loot-table acquisition sources
- Dedicated villager trade catalogue with profession, level, cost, output, bundle contents, and source filters
- Places and structures catalogue with generation settings, biome selectors, placement data, and location loot
- Archaeology catalogue with direct rewards, nested loot-table contents, weights, base chances, and source paths
- Fishing catalogue with routing conditions, biome groups, habitat pools, custom species, junk, and treasure
- Optional Modrinth release history with stable-default policy and hash-verified ZIP comparison
- Persistent Complete, Progression-safe, and Minimal spoiler modes with curated source-backed gates
- Keyboard-visible focus, labeled controls, semantic landmarks, skip navigation, and reduced-motion support
- Mechanics and vanilla override summaries
- Paginated Item Catalogue with component-distinct custom variants
- Paginated static site with a localhost-only Python server

## Build

Use the repository's ignored `build\` directory for development and acceptance
builds. Publish only the finished site to OneDrive. Do not create temporary
build trees in OneDrive because deleting their many image files triggers sync
warnings.

```powershell
uv run --project C:\code\minecraft-datapack-wiki-generator datapack-wiki build `
  --pack "C:\Users\peterbax\Downloads\Matcha_Flavoured_1_12.zip" `
  --output "C:\code\minecraft-datapack-wiki-generator\build\matcha" `
  --fetch-wiki-icons `
  --modrinth-project matcha-flavoured
```

When Modrinth metadata is enabled, the builder identifies the source ZIP by its
published SHA-512 hash, shows stable and prerelease history, downloads and
verifies the previous stable ZIP, and generates a categorized comparison.
Use `--compare-version VERSION` to choose a different baseline. Omit
`--modrinth-project` for a fully offline build.

## Spoiler modes

- **Complete** is the default and shows every generated record.
- **Progression-safe** reveals classified content through a selected progression
  stage. Future stage titles, gated search results, autocomplete entries,
  cross-links, changelogs, and comparisons remain hidden.
- **Minimal** reveals stage 1 and unclassified reference content.

The classification file is
`src/datapack_wiki/curation/matcha_spoilers.json`. It contains only
source-backed progression gates. Content without an explicit classification
remains visible rather than being guessed.

After validation, publish the same build to the final OneDrive location:

```powershell
uv run --project C:\code\minecraft-datapack-wiki-generator datapack-wiki build `
  --pack "C:\Users\peterbax\Downloads\Matcha_Flavoured_1_12.zip" `
  --output "$env:USERPROFILE\OneDrive\Copilot\Matcha Flavoured Wiki" `
  --reuse-site "$env:USERPROFILE\OneDrive\Copilot\Matcha Flavoured Wiki"
```

## Serve

```powershell
python -m datapack_wiki serve `
  --site "$env:USERPROFILE\OneDrive\Copilot\Matcha Flavoured Wiki" `
  --timeout-minutes 30
```

The server binds only to `127.0.0.1`, chooses an available port, opens the
default browser, and shuts down after the configured timeout.

## Attribution

Generated sites retain pack credits and identify external image sources.
Minecraft names and vanilla assets remain property of Mojang Studios and
Microsoft. Third-party prose or code must not be copied without compatible
licensing and attribution.
