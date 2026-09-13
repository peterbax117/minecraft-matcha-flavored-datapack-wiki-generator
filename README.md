# Minecraft Datapack Wiki Generator

Builds an offline-capable, searchable local wiki from a combined Minecraft
datapack and resource-pack ZIP.

## Current coverage

- Pack metadata and source SHA-256
- Recipes from every namespace
- Custom item names, models, lore, and textures
- Forward and reverse recipe lookup
- Food properties and consume effects
- Custom enchantment metadata
- Recursive loot-table acquisition sources
- Villager trades and bundle contents
- Mechanics and vanilla override summaries
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
  --fetch-wiki-icons
```

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
