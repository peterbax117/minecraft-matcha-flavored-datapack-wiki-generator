import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from datapack_wiki.builder import build


class BuildTests(unittest.TestCase):
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
            effect = data["foodItems"][0]["effects"][0]
            self.assertEqual(effect["level"], 2)
            self.assertEqual(effect["duration"], "2s")


if __name__ == "__main__":
    unittest.main()
