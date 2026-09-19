import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from datapack_wiki.builder import (
    Assets,
    Pack,
    build,
    build_optimum_builds,
    build_spoilers,
    compare_pack_zips,
)


class BuildTests(unittest.TestCase):
    def test_resolves_custom_namespace_item_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            item = root / "assets/example/items/widget.json"
            model = root / "assets/example/models/item/widget.json"
            texture = root / "assets/example/textures/item/widget.png"
            item.parent.mkdir(parents=True)
            model.parent.mkdir(parents=True)
            texture.parent.mkdir(parents=True)
            item.write_text(
                json.dumps(
                    {"model": {"type": "minecraft:model", "model": "example:item/widget"}}
                ),
                encoding="utf-8",
            )
            model.write_text(
                json.dumps({"textures": {"layer0": "example:item/widget"}}),
                encoding="utf-8",
            )
            texture.write_bytes(b"\x89PNG\r\n\x1a\ncustom")
            output = root / "site"
            assets = Assets(Pack(root), output, None, False)
            icon = assets.item_icon("minecraft:stick", "example:widget")
            self.assertIsNotNone(icon)
            self.assertTrue((output / icon).is_file())

    def test_builds_source_backed_spoiler_stages(self):
        spoilers = build_spoilers(
            recipes=[{"id": "blessings:test"}],
            items=[{"key": "variant:test", "id": "example:test", "outputOf": ["blessings:test"]}],
            blessings=[{"id": "blessings:test"}],
            advancements=[
                {"id": "main:hell/test", "hidden": False},
                {"id": "main:tutorial/secret", "hidden": True},
            ],
            progression=[{"advancements": ["main:tutorial/start"]}],
            places={"structures": [], "locations": []},
            differences=[],
            trades=[
                {
                    "id": "example:trade/staged",
                    "result": {"id": "example:test"},
                    "bundleContents": [],
                },
                {
                    "id": "example:trade/unstaged",
                    "result": {"id": "example:plain"},
                    "bundleContents": [],
                },
            ],
        )
        self.assertEqual(spoilers["stages"]["recipe"]["blessings:test"], 6)
        self.assertEqual(spoilers["stages"]["item"]["variant:test"], 6)
        self.assertEqual(spoilers["stages"]["blessing"]["blessings:test"], 6)
        self.assertEqual(spoilers["stages"]["advancement"]["main:hell/test"], 6)
        self.assertIn("main:tutorial/secret", spoilers["hidden"]["advancement"])
        self.assertEqual(spoilers["stages"]["trade"]["example:trade/staged"], 6)
        self.assertNotIn("example:trade/unstaged", spoilers["stages"]["trade"])

    def test_build_optimum_builds_drops_unmatched_curated_ids(self):
        result = build_optimum_builds(enchantments=[], blessings=[], foods=[])
        self.assertTrue(result["personas"])
        for persona in result["personas"]:
            self.assertEqual(persona["enchantments"], [])
            self.assertEqual(persona["blessings"], [])
            self.assertEqual(persona["consumables"], [])
        self.assertIsNotNone(result["allAround"])
        self.assertEqual(result["allAround"]["enchantments"], [])
        self.assertEqual(result["allAround"]["blessings"], [])

    def test_build_optimum_builds_resolves_matching_curated_ids(self):
        enchantments = [{"id": "main:conduit_power", "name": "Conduit Power"}]
        blessings = [
            {
                "id": "blessings:depth_strider_riptide_aqua_affinity_respiration",
                "name": "Prayer of Yamm",
            }
        ]
        foods = [
            {
                "key": "food:abc123",
                "name": "Golden Carrot Cupcake",
                "recipes": ["food:golden_carrot_cupcake"],
            }
        ]
        result = build_optimum_builds(enchantments, blessings, foods)
        exploring = next(persona for persona in result["personas"] if persona["id"] == "exploring")
        self.assertIn(
            {"id": "main:conduit_power", "name": "Conduit Power", "reason": exploring["enchantments"][0]["reason"]},
            exploring["enchantments"],
        )
        self.assertIn(
            {
                "id": "blessings:depth_strider_riptide_aqua_affinity_respiration",
                "name": "Prayer of Yamm",
                "reason": exploring["blessings"][0]["reason"],
            },
            exploring["blessings"],
        )
        self.assertIn(
            {"key": "food:abc123", "name": "Golden Carrot Cupcake", "reason": exploring["consumables"][0]["reason"]},
            exploring["consumables"],
        )

    def test_compare_version_requires_modrinth_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack_path = root / "sample.zip"
            with zipfile.ZipFile(pack_path, "w") as archive:
                archive.writestr("pack.mcmeta", '{"pack":{"description":"test"}}')
            with self.assertRaisesRegex(ValueError, "requires --modrinth-project"):
                build(pack_path, root / "site", compare_version="1.0")

    def test_compares_pack_zip_entities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "old.zip"
            new = root / "new.zip"
            with zipfile.ZipFile(old, "w") as archive:
                archive.writestr("data/example/recipe/kept.json", '{"value":1}')
                archive.writestr("data/example/recipe/removed.json", "{}")
            with zipfile.ZipFile(new, "w") as archive:
                archive.writestr("data/example/recipe/kept.json", '{"value":2}')
                archive.writestr("data/example/villager_trade/added.json", "{}")
            comparison = compare_pack_zips(new, old)
            self.assertEqual(comparison["summary"]["added"], 1)
            self.assertEqual(comparison["summary"]["removed"], 1)
            self.assertEqual(comparison["summary"]["changed"], 1)
            categories = {item["name"]: item for item in comparison["categories"]}
            self.assertEqual(categories["Recipes"]["changed"][0]["id"], "example:kept")
            self.assertEqual(
                categories["Villager trades"]["added"][0]["id"], "example:added"
            )

    def test_builds_minimal_pack(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack_path = root / "sample.zip"
            output = root / "site"
            files = {
                "pack.mcmeta": {
                    "pack": {
                        "min_format": 88,
                        "max_format": 107,
                        "description": {"text": "Sample Pack"},
                    }
                },
                "assets/minecraft/lang/en_us.json": {
                    "item.minecraft.beetroot": "Tomatoes",
                    "item.example.soup": "Test Soup",
                },
                "data/food/recipe/test_soup.json": {
                    "type": "minecraft:crafting_shapeless",
                    "ingredients": ["minecraft:beetroot"],
                    "result": {
                        "id": "minecraft:suspicious_stew",
                        "components": {
                            "minecraft:item_name": {"translate": "item.example.soup"},
                            "minecraft:food": {"nutrition": 4, "saturation": 2},
                            "minecraft:consumable": {
                                "on_consume_effects": [
                                    {
                                        "type": "minecraft:apply_effects",
                                        "effects": [
                                            {
                                                "id": "minecraft:regeneration",
                                                "duration": 40,
                                                "amplifier": 1,
                                            }
                                        ],
                                    }
                                ]
                            },
                        },
                    },
                },
                "data/example/villager_trade/farmer/1/tomatoes.json": {
                    "wants": {
                        "id": "minecraft:emerald",
                        "count": {"type": "minecraft:uniform", "min": 2, "max": 4},
                    },
                    "additional_wants": {"id": "minecraft:jungle_sapling", "count": 1},
                    "gives": {"id": "minecraft:beetroot", "count": 4},
                },
                "data/example/worldgen/structure/test_garden.json": {
                    "type": "minecraft:jigsaw",
                    "biomes": ["minecraft:plains"],
                    "step": "surface_structures",
                    "terrain_adaptation": "beard_thin",
                    "start_pool": "minecraft:test_garden/start",
                    "size": 3,
                },
                "data/example/worldgen/structure_set/test_gardens.json": {
                    "structures": [{"structure": "example:test_garden", "weight": 1}],
                    "placement": {
                        "type": "minecraft:random_spread",
                        "spacing": 32,
                        "separation": 8,
                        "salt": 42,
                    },
                },
                "data/minecraft/loot_table/chests/test_garden.json": {
                    "pools": [
                        {
                            "entries": [
                                {"type": "minecraft:item", "name": "minecraft:beetroot"}
                            ]
                        }
                    ]
                },
                "data/example/loot_table/archaeology/test_dig.json": {
                    "type": "minecraft:archaeology",
                    "pools": [
                        {
                            "rolls": 1,
                            "entries": [
                                {
                                    "type": "minecraft:item",
                                    "name": "minecraft:beetroot",
                                    "weight": 3,
                                    "functions": [
                                        {
                                            "function": "minecraft:set_components",
                                            "components": {
                                                "minecraft:item_name": "Relic Tomato",
                                                "minecraft:item_model": "example:relic_tomato",
                                            },
                                        }
                                    ],
                                },
                                {
                                    "type": "minecraft:item",
                                    "name": "minecraft:emerald",
                                    "weight": 1,
                                },
                            ],
                        }
                    ],
                },
                "data/example/loot_table/gameplay/fishing.json": {
                    "type": "minecraft:fishing",
                    "pools": [
                        {
                            "entries": [
                                {
                                    "type": "minecraft:loot_table",
                                    "value": "example:gameplay/fishing/freshwater_test",
                                    "weight": 10,
                                    "conditions": [
                                        {
                                            "condition": "minecraft:location_check",
                                            "predicate": {"biomes": "#example:freshwater_test"},
                                        }
                                    ],
                                }
                            ]
                        }
                    ],
                },
                "data/example/loot_table/gameplay/fishing/freshwater_test.json": {
                    "pools": [
                        {
                            "entries": [
                                {
                                    "type": "minecraft:loot_table",
                                    "value": "example:gameplay/fishing/fish/river_tomato",
                                    "weight": 3,
                                },
                                {
                                    "type": "minecraft:loot_table",
                                    "value": "example:rewards/buried_tomato",
                                    "weight": 1,
                                }
                            ]
                        }
                    ]
                },
                "data/example/loot_table/gameplay/fishing/fish/river_tomato.json": {
                    "pools": [
                        {
                            "entries": [
                                {
                                    "type": "minecraft:item",
                                    "name": "minecraft:beetroot",
                                    "functions": [
                                        {
                                            "function": "minecraft:set_components",
                                            "components": {
                                                "minecraft:item_name": "River Tomato"
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    ]
                },
                "data/example/loot_table/rewards/buried_tomato.json": {
                    "pools": [
                        {
                            "entries": [
                                {
                                    "type": "minecraft:item",
                                    "name": "minecraft:beetroot",
                                    "functions": [
                                        {
                                            "function": "minecraft:set_components",
                                            "components": {
                                                "minecraft:item_name": "Buried Tomato"
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    ]
                },
                "data/example/tags/worldgen/biome/freshwater_test.json": {
                    "values": ["minecraft:plains"]
                },
            }
            with zipfile.ZipFile(pack_path, "w") as archive:
                for name, content in files.items():
                    archive.writestr(name, json.dumps(content))

            build(pack_path, output)

            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "server.py").is_file())
            raw = (output / "data.js").read_text(encoding="utf-8")
            data = json.loads(raw.removeprefix("window.MATCHA_DATA=").strip().removesuffix(";"))
            self.assertEqual(data["pack"]["title"], "Sample Pack")
            self.assertEqual(len(data["recipes"]), 1)
            self.assertEqual(data["recipes"][0]["ingredients"][0]["name"], "Tomatoes")
            self.assertEqual(len(data["foodItems"]), 1)
            self.assertEqual(len(data["items"]), 4)
            self.assertEqual(data["recipes"][0]["resultKey"], data["foodItems"][0]["key"])
            self.assertTrue(any(item["name"] == "Test Soup" and item["foodKey"] for item in data["items"]))
            effect = data["foodItems"][0]["effects"][0]
            self.assertEqual(effect["level"], 2)
            self.assertEqual(effect["duration"], "2s")
            self.assertEqual(len(data["trades"]), 1)
            trade = data["trades"][0]
            self.assertEqual(trade["id"], "example:farmer/1/tomatoes")
            self.assertEqual(trade["profession"], "Farmer")
            self.assertEqual(trade["level"], "Novice")
            self.assertEqual([cost["name"] for cost in trade["costs"]], ["Emerald", "Jungle Sapling"])
            self.assertEqual(trade["costs"][0]["count"], "2 to 4 (uniform)")
            self.assertEqual(trade["result"]["name"], "Tomatoes")
            self.assertEqual(trade["result"]["count"], 4)
            self.assertEqual(trade["maxUses"], 4)
            self.assertEqual(trade["xp"], 1)
            self.assertEqual(trade["reputationDiscount"], 0)
            self.assertEqual(len(data["places"]["structures"]), 1)
            self.assertEqual(data["places"]["structures"][0]["id"], "example:test_garden")
            self.assertEqual(data["places"]["structures"][0]["name"], "Test Garden")
            self.assertEqual(data["places"]["structures"][0]["biomes"], ["minecraft:plains"])
            self.assertEqual(data["places"]["structures"][0]["placement"]["spacing"], 32)
            self.assertTrue(
                any(place["name"] == "Test Garden" for place in data["places"]["locations"])
            )
            self.assertEqual(len(data["archaeology"]), 1)
            dig = data["archaeology"][0]
            self.assertEqual(dig["id"], "example:archaeology/test_dig")
            self.assertEqual(dig["entries"][0]["baseChance"], 75.0)
            dig_place = next(
                place
                for place in data["places"]["locations"]
                if place["id"] == "example:archaeology/test_dig"
            )
            self.assertTrue(
                any(
                    item["name"] == "Relic Tomato"
                    and item["modelId"] == "example:relic_tomato"
                    for item in dig_place["items"]
                )
            )
            self.assertEqual(len(data["fishing"]["routes"]), 1)
            self.assertIsNone(data["fishing"]["routes"][0]["baseChance"])
            self.assertEqual(len(data["fishing"]["tables"]), 2)
            fishing_habitat = next(
                table for table in data["fishing"]["tables"] if table["role"] == "Habitat"
            )
            self.assertEqual(fishing_habitat["biomes"], ["minecraft:plains"])
            self.assertTrue(
                any(
                    item["name"] == "Buried Tomato"
                    for entry in fishing_habitat["entries"]
                    for item in entry["resolvedItems"]
                )
            )
            fishing_species = next(
                table for table in data["fishing"]["tables"] if table["role"] == "Species"
            )
            self.assertEqual(fishing_species["name"], "River Tomato")
            self.assertEqual(fishing_species["biomes"], ["minecraft:plains"])
            self.assertTrue(fishing_species["active"])
            self.assertIsNone(data["releaseHistory"])


if __name__ == "__main__":
    unittest.main()
