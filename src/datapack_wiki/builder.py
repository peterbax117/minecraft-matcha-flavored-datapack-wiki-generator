from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parent
TEMPLATES = PACKAGE_ROOT / "templates"
USER_AGENT = "MinecraftDatapackWiki/0.1 (personal offline documentation)"


def normalize_id(value: str | None) -> str:
    value = value or "minecraft:unknown"
    return value if ":" in value else f"minecraft:{value}"


def split_id(value: str | None) -> tuple[str, str]:
    return tuple(normalize_id(value).split(":", 1))


def duration_text(ticks: int | float) -> str:
    seconds = ticks / 20
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds / 60:g}m"
    return f"{seconds:g}s"


class Pack:
    def __init__(self, root: Path):
        self.root = root
        self.data = root / "data"
        self.assets = root / "assets/minecraft"
        language = self.assets / "lang/en_us.json"
        self.lang = self.read_json(language) if language.exists() else {}

    @staticmethod
    def read_json(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def component_text(self, value) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(self.component_text(part) for part in value)
        if isinstance(value, dict):
            if "translate" in value:
                return self.lang.get(value["translate"], value["translate"])
            return value.get("text", "")
        return ""

    def item_name(self, item_id: str, components: dict | None = None) -> str:
        components = components or {}
        custom = self.component_text(components.get("minecraft:item_name"))
        if custom:
            return custom
        namespace, name = split_id(item_id)
        for key in (f"item.{namespace}.{name}", f"block.{namespace}.{name}"):
            if key in self.lang:
                return self.lang[key]
        return name.replace("_", " ").title()


def ingredient_entries(pack: Pack, value) -> list[dict]:
    if isinstance(value, list):
        result = []
        for option in value:
            result.extend(ingredient_entries(pack, option))
        return result
    if isinstance(value, str):
        token = value
    elif isinstance(value, dict):
        token = value.get("id") or value.get("item")
        if not token and value.get("tag"):
            token = "#" + value["tag"]
    else:
        token = str(value)
    if token.startswith("#"):
        return [{"id": token, "name": token[1:].replace("_", " ").title(), "tag": True}]
    token = normalize_id(token)
    return [{"id": token, "name": pack.item_name(token), "tag": False}]


def parse_ingredients(pack: Pack, recipe: dict) -> list[dict]:
    recipe_type = recipe.get("type", "").split(":")[-1]
    entries = []
    if recipe_type == "crafting_shaped":
        counts = Counter("".join(recipe.get("pattern", [])))
        for symbol, value in recipe.get("key", {}).items():
            options = ingredient_entries(pack, value)
            for option in options:
                option.update(count=counts.get(symbol, 1), symbol=symbol)
            entries.extend(options)
    elif recipe_type == "crafting_shapeless":
        for value in recipe.get("ingredients", []):
            entries.extend(ingredient_entries(pack, value))
        grouped = {}
        for entry in entries:
            key = (entry["id"], entry["name"], entry["tag"])
            grouped.setdefault(key, entry.copy())
            grouped[key]["count"] = grouped[key].get("count", 0) + 1
        entries = list(grouped.values())
    elif recipe_type in {"smelting", "blasting", "smoking", "campfire_cooking", "stonecutting"}:
        entries = ingredient_entries(pack, recipe.get("ingredient", ""))
    elif recipe_type in {"smithing_transform", "smithing_trim"}:
        for role in ("template", "base", "addition"):
            for entry in ingredient_entries(pack, recipe.get(role, "")):
                entry["role"] = role
                entries.append(entry)
    for entry in entries:
        entry.setdefault("count", 1)
    return entries


def parse_recipes(pack: Pack) -> list[dict]:
    recipes = []
    for namespace in sorted(path for path in pack.data.iterdir() if path.is_dir()):
        recipe_root = namespace / "recipe"
        if not recipe_root.exists():
            continue
        for path in sorted(recipe_root.rglob("*.json")):
            raw = pack.read_json(path)
            result = raw.get("result", {})
            result_id = normalize_id(result.get("id"))
            components = result.get("components", {})
            recipe_id = f"{namespace.name}:{path.relative_to(recipe_root).with_suffix('').as_posix()}"
            ingredients = parse_ingredients(pack, raw)
            result_name = pack.item_name(result_id, components)
            lore = [
                pack.component_text(line)
                for line in components.get("minecraft:lore", [])
            ]
            if result_name == "Blessing" and lore:
                result_name = lore[0]
            recipe_type = raw.get("type", "").split(":")[-1]
            if recipe_type == "crafting_shaped":
                pattern_label = " / ".join(raw.get("pattern", []))
            elif recipe_type == "smithing_transform":
                pattern_label = "Smithing upgrade"
            elif recipe_type == "stonecutting":
                pattern_label = "Stonecutter"
            else:
                pattern_label = recipe_type.replace("_", " ").title()
            search_terms = [
                recipe_id,
                namespace.name,
                raw.get("type", ""),
                result_id,
                result_name,
                *lore,
            ]
            for ingredient in ingredients:
                search_terms.extend((ingredient["id"], ingredient["name"]))
            recipes.append(
                {
                    "id": recipe_id,
                    "namespace": namespace.name,
                    "type": recipe_type,
                    "resultId": result_id,
                    "resultName": result_name,
                    "count": result.get("count", 1),
                    "ingredients": ingredients,
                    "pattern": raw.get("pattern", []),
                    "patternLabel": pattern_label,
                    "lore": lore,
                    "custom": bool(components),
                    "search": " ".join(map(str, search_terms)).lower(),
                    "source": f"data/{namespace.name}/recipe/{path.relative_to(recipe_root).as_posix()}",
                    "_components": components,
                }
            )
    return recipes


def parse_food(pack: Pack, recipes: list[dict]) -> list[dict]:
    beneficial = {
        "absorption", "conduit_power", "fire_resistance", "haste",
        "health_boost", "invisibility", "night_vision", "regeneration",
        "resistance", "speed", "strength", "water_breathing",
    }
    harmful = {
        "bad_omen", "blindness", "darkness", "hunger", "infested",
        "levitation", "mining_fatigue", "nausea", "oozing", "poison",
        "slowness", "weakness", "weaving", "wither",
    }
    groups = {}
    for recipe in recipes:
        components = recipe["_components"]
        food = components.get("minecraft:food")
        consumable = components.get("minecraft:consumable")
        if not food and not consumable:
            continue
        effects, removes, clear_all = [], [], False
        for action in (consumable or {}).get("on_consume_effects", []):
            action_type = action.get("type", "").split(":")[-1]
            if action_type == "apply_effects":
                for effect in action.get("effects", []):
                    effect_id = normalize_id(effect.get("id"))
                    short = effect_id.split(":")[-1]
                    category = "beneficial" if short in beneficial else "harmful" if short in harmful else "neutral"
                    ticks = effect.get("duration", 0)
                    effects.append(
                        {
                            "id": effect_id,
                            "name": pack.item_name(effect_id).replace("Minecraft:", ""),
                            "level": effect.get("amplifier", 0) + 1,
                            "ticks": ticks,
                            "duration": duration_text(ticks),
                            "probability": action.get("probability", 1),
                            "category": category,
                            "particles": effect.get("show_particles", True),
                            "icon": effect.get("show_icon", True),
                        }
                    )
            elif action_type == "remove_effects":
                values = action.get("effects", [])
                values = [values] if isinstance(values, str) else values
                removes.extend(
                    {"id": normalize_id(effect), "name": effect.split(":")[-1].replace("_", " ").title()}
                    for effect in values
                )
            elif action_type == "clear_all_effects":
                clear_all = True
        remainder = components.get("minecraft:use_remainder")
        if isinstance(remainder, dict):
            remainder = remainder.get("id")
        details = {
            "nutrition": (food or {}).get("nutrition"),
            "saturation": (food or {}).get("saturation"),
            "alwaysEdible": (food or {}).get("can_always_eat", False),
            "consumeSeconds": (consumable or {}).get("consume_seconds"),
            "animation": (consumable or {}).get("animation"),
            "sound": (consumable or {}).get("sound"),
            "effects": effects,
            "removes": removes,
            "clearAll": clear_all,
            "remainder": remainder,
        }
        identity = {
            "underlyingId": recipe["resultId"],
            "model": components.get("minecraft:item_model"),
            "name": components.get("minecraft:item_name"),
            "food": food,
            "consumable": consumable,
            "remainder": components.get("minecraft:use_remainder"),
        }
        signature = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        key = f"food:{signature}"
        group = groups.setdefault(
            key,
            {
                "key": key,
                "name": recipe["resultName"],
                "underlyingId": recipe["resultId"],
                "itemModel": components.get("minecraft:item_model"),
                "recipes": [],
                **details,
            },
        )
        group["recipes"].append(recipe["id"])
        recipe.update(foodKey=key, food=details)
    return sorted(groups.values(), key=lambda item: item["name"].lower())


def build_items(pack: Pack, recipes: list[dict], foods: list[dict], acquisition: list[dict]) -> list[dict]:
    items = {}
    food_keys = {food["key"] for food in foods}

    def base_key(item_id):
        return "base:" + normalize_id(item_id)

    def ensure(key, item_id, name, icon=None, custom=False, components=None):
        record = items.setdefault(
            key,
            {
                "key": key,
                "id": normalize_id(item_id),
                "name": name,
                "icon": icon,
                "custom": custom,
                "lore": [],
                "properties": [],
                "outputOf": [],
                "usedIn": [],
                "foodKey": None,
            },
        )
        if icon and not record.get("icon"):
            record["icon"] = icon
        if components:
            record["lore"] = [
                pack.component_text(line)
                for line in components.get("minecraft:lore", [])
            ]
            properties = []
            if "minecraft:max_damage" in components:
                properties.append(f"Durability: {components['minecraft:max_damage']}")
            if "minecraft:enchantments" in components:
                enchants = components["minecraft:enchantments"]
                properties.append(
                    "Enchantments: "
                    + ", ".join(
                        f"{key.split(':')[-1].replace('_', ' ').title()} {level}"
                        for key, level in enchants.items()
                    )
                )
            if "minecraft:repairable" in components:
                repairable = components["minecraft:repairable"].get("items", [])
                repairable = [repairable] if isinstance(repairable, str) else repairable
                properties.append(
                    "Repairable with: "
                    + ", ".join(item.split(":")[-1].replace("_", " ").title() for item in repairable)
                )
            tool = components.get("minecraft:tool", {})
            if tool.get("default_mining_speed") is not None:
                properties.append(f"Default mining speed: {tool['default_mining_speed']}")
            record["properties"] = properties
        return record

    for acquisition_item in acquisition:
        ensure(
            base_key(acquisition_item["id"]),
            acquisition_item["id"],
            acquisition_item["name"],
        )

    for recipe in recipes:
        components = recipe.get("_components", {})
        if recipe.get("foodKey") in food_keys:
            result_key = recipe["foodKey"]
        elif recipe["custom"]:
            identity = {
                "id": recipe["resultId"],
                "model": components.get("minecraft:item_model"),
                "name": components.get("minecraft:item_name"),
                "components": components,
            }
            digest = hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:16]
            result_key = f"variant:{digest}"
        else:
            result_key = base_key(recipe["resultId"])
        result_item = ensure(
            result_key,
            recipe["resultId"],
            recipe["resultName"],
            recipe.get("icon"),
            recipe["custom"],
            components,
        )
        result_item["outputOf"].append(recipe["id"])
        result_item["foodKey"] = recipe.get("foodKey")
        recipe["resultKey"] = result_key

        for ingredient in recipe["ingredients"]:
            if ingredient["id"].startswith("#"):
                continue
            key = base_key(ingredient["id"])
            ingredient["itemKey"] = key
            ensure(
                key,
                ingredient["id"],
                ingredient["name"],
                ingredient.get("icon"),
            )["usedIn"].append(recipe["id"])

    for record in items.values():
        record["outputOf"] = sorted(set(record["outputOf"]))
        record["usedIn"] = sorted(set(record["usedIn"]))
        lower = record["name"].lower()
        if record["foodKey"]:
            category = "Food and drink"
        elif "blessing" in lower or record["key"].startswith("variant:") and "enchanted_book" in record["id"]:
            category = "Blessings"
        elif any(word in lower for word in ("sword", "axe", "pickaxe", "shovel", "hoe", "helmet", "chestplate", "leggings", "boots", "shield", "bow", "mace", "mattock", "dolabra")):
            category = "Gear"
        elif any(word in lower for word in ("ingot", "alloy", "nugget", "scrap", "fragment", "dust", "wire", "leather", "flour", "dough", "wax")):
            category = "Materials"
        elif any(word in lower for word in ("block", "bricks", "stairs", "slab", "wall", "fence", "door", "trapdoor", "planks", "glass")):
            category = "Blocks"
        else:
            category = "Other"
        record["category"] = category
    return sorted(items.values(), key=lambda item: item["name"].lower())


def parse_blessings(pack: Pack, recipes: list[dict]) -> list[dict]:
    blessings = []
    for recipe in recipes:
        if recipe["namespace"] != "blessings":
            continue
        stored = recipe.get("_components", {}).get("minecraft:stored_enchantments", {})
        blessings.append(
            {
                "id": recipe["id"],
                "name": recipe["resultName"],
                "icon": recipe.get("icon"),
                "recipeId": recipe["id"],
                "enchantments": [
                    {
                        "id": normalize_id(enchantment_id),
                        "name": enchantment_id.split(":")[-1].replace("_", " ").title(),
                        "level": level,
                    }
                    for enchantment_id, level in stored.items()
                ],
                "source": recipe["source"],
            }
        )
    return blessings


def parse_enchantments(pack: Pack, recipes: list[dict], blessings: list[dict]) -> list[dict]:
    root = pack.data / "main/enchantment"
    result = []
    if not root.exists():
        return result
    curation_path = PACKAGE_ROOT / "curation/matcha_enchantments.json"
    curation = json.loads(curation_path.read_text(encoding="utf-8")) if curation_path.exists() else {}
    recipe_equipment = defaultdict(list)
    for recipe in recipes:
        for enchantment_id, level in recipe.get("_components", {}).get("minecraft:enchantments", {}).items():
            recipe_equipment[normalize_id(enchantment_id)].append(
                {
                    "name": recipe["resultName"],
                    "itemId": recipe["resultId"],
                    "itemKey": recipe.get("resultKey"),
                    "recipeId": recipe["id"],
                    "level": level,
                }
            )

    def function_references(value):
        found = set()
        if isinstance(value, dict):
            if value.get("type") == "minecraft:run_function" and value.get("function"):
                found.add(normalize_id(value["function"]))
            for child in value.values():
                found.update(function_references(child))
        elif isinstance(value, list):
            for child in value:
                found.update(function_references(child))
        return found

    for path in sorted(root.glob("*.json")):
        raw = pack.read_json(path)
        items = raw.get("supported_items", [])
        items = [items] if isinstance(items, str) else items
        enchantment_id = f"main:{path.stem}"
        translated_name = pack.component_text(raw.get("description"))
        fallback_name = re.sub(r"(?<=\D)(\d+)$", r" \1", path.stem.replace("_", " ").title())
        has_private_glyph = any("\ue000" <= character <= "\uf8ff" for character in translated_name)
        display_name = fallback_name if has_private_glyph else translated_name or fallback_name
        functions = []
        for function_id in sorted(function_references(raw.get("effects", {}))):
            namespace, function_path = split_id(function_id)
            source = pack.data / namespace / "function" / f"{function_path}.mcfunction"
            commands = []
            comments = []
            if source.exists():
                for line in source.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        comments.append(stripped.removeprefix("#").strip())
                    elif stripped:
                        commands.append(stripped)
            functions.append(
                {
                    "id": function_id,
                    "source": f"data/{namespace}/function/{function_path}.mcfunction",
                    "commands": commands,
                    "comments": comments,
                }
            )
        related_blessings = [
            {"id": blessing["id"], "name": blessing["name"], "level": enchantment["level"]}
            for blessing in blessings
            for enchantment in blessing["enchantments"]
            if enchantment["id"] == enchantment_id
        ]
        result.append(
            {
                "name": display_name,
                "inGameName": translated_name,
                "id": enchantment_id,
                "max": raw.get("max_level", "?"),
                "slots": raw.get("slots", []),
                "items": items,
                "weight": raw.get("weight"),
                "anvilCost": raw.get("anvil_cost"),
                "effectTriggers": list(raw.get("effects", {})),
                "functions": functions,
                "equipment": recipe_equipment.get(enchantment_id, []),
                "blessings": related_blessings,
                "summary": curation.get(enchantment_id, {}).get("summary"),
                "activation": curation.get(enchantment_id, {}).get("activation"),
                "scaling": curation.get(enchantment_id, {}).get("scaling"),
                "provenance": "curated from definition and function commands"
                if enchantment_id in curation else "extracted from definition",
                "source": f"data/main/enchantment/{path.name}",
            }
        )
    return result


def parse_acquisition(pack: Pack, recipes: list[dict]) -> list[dict]:
    names = {}
    acquired = defaultdict(lambda: {"recipes": [], "sources": [], "trades": []})
    for recipe in recipes:
        names[recipe["resultId"]] = recipe["resultName"]
        acquired[recipe["resultId"]]["recipes"].append(recipe["id"])
        for ingredient in recipe["ingredients"]:
            if not ingredient["id"].startswith("#"):
                names[normalize_id(ingredient["id"])] = ingredient["name"]

    tables = {}
    for path in pack.data.rglob("loot_table/**/*.json"):
        relative = path.relative_to(pack.data)
        table_id = f"{relative.parts[0]}:{Path(*relative.parts[2:]).with_suffix('').as_posix()}"
        tables[table_id] = pack.read_json(path)

    def contents(table_id, seen=None):
        seen = set() if seen is None else seen
        if table_id in seen:
            return set()
        seen.add(table_id)
        found = set()

        def walk(value):
            if isinstance(value, dict):
                if value.get("type") == "minecraft:item" and value.get("name"):
                    found.add(normalize_id(value["name"]))
                elif value.get("type") == "minecraft:loot_table":
                    reference = value.get("value") or value.get("name")
                    if isinstance(reference, str):
                        found.update(contents(normalize_id(reference), seen.copy()))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(tables.get(table_id, {}))
        return found

    def source_label(table_id):
        path = table_id.split(":", 1)[1]
        parts = path.split("/")
        pretty = lambda value: value.replace("_", " ").title()
        if parts[0] == "chests":
            location = pretty(" ".join(parts[1:]))
            if path == "chests/village/village_snowy_house":
                location = "Snowy Village House"
            return "Chest loot", location
        if parts[0] == "archaeology":
            location = "Trail Ruins (common)" if path == "archaeology/trail_ruins_common" else pretty(" ".join(parts[1:]))
            return "Archaeology", location
        if parts[0] == "blocks":
            location = "Harvest a mature Tomato crop" if parts[-1] == "beetroots" else "Harvest or break " + pretty(" ".join(parts[1:]))
            return "Block drop", location
        if parts[0] == "entities":
            return "Entity drop", pretty(" ".join(parts[1:]))
        if path.startswith("gameplay/fishing"):
            return "Fishing", pretty(" ".join(parts[2:])) or "Fishing"
        if parts[0] == "gameplay":
            return "Gameplay loot", pretty(" ".join(parts[1:]))
        return None

    for table_id in sorted(tables):
        label = source_label(table_id)
        if not label:
            continue
        for item_id in contents(table_id):
            names.setdefault(item_id, pack.item_name(item_id))
            acquired[item_id]["sources"].append(
                {"kind": label[0], "location": label[1], "id": table_id}
            )
    for item_id in ("minecraft:beetroot", "minecraft:beetroot_seeds"):
        acquired[item_id]["sources"].append(
            {
                "kind": "Natural generation",
                "location": "Village farm plots (vanilla beetroot crop behavior)",
                "id": "https://minecraft.wiki/w/Beetroot_Seeds",
            }
        )

    trade_root = pack.data / "minecraft/villager_trade"
    levels = {"1": "Novice", "2": "Apprentice", "3": "Journeyman", "4": "Expert", "5": "Master"}
    if trade_root.exists():
        for path in sorted(trade_root.rglob("*.json")):
            raw = pack.read_json(path)
            gives = raw.get("gives", {})
            result_id = normalize_id(gives.get("id"))
            components = gives.get("components", {})
            custom_name = pack.component_text(components.get("minecraft:item_name"))
            outputs = [(result_id, None)]
            for entry in components.get("minecraft:bundle_contents", []):
                if entry.get("id"):
                    outputs.append((normalize_id(entry["id"]), custom_name or pack.item_name(result_id)))
            relative = path.relative_to(trade_root)
            profession = relative.parts[0].replace("_", " ").title()
            level = levels.get(relative.parts[1], relative.parts[1]) if len(relative.parts) > 2 else "Any"
            wants = raw.get("wants", {})
            wants = wants if isinstance(wants, list) else [wants]
            costs = []
            for cost in wants:
                if isinstance(cost, dict) and cost.get("id"):
                    cost_id = normalize_id(cost["id"])
                    costs.append(f"{cost.get('count', 1)} {pack.item_name(cost_id)}")
            for item_id, container in outputs:
                names.setdefault(item_id, pack.item_name(item_id))
                trade = {
                    "profession": profession,
                    "level": level,
                    "cost": " + ".join(costs) or "Trade cost defined by pack",
                    "id": "minecraft:" + path.relative_to(pack.data / "minecraft").with_suffix("").as_posix(),
                }
                if container:
                    trade["container"] = container
                acquired[item_id]["trades"].append(trade)

    result = []
    for item_id, name in sorted(names.items(), key=lambda pair: pair[1].lower()):
        record = acquired[item_id]
        sources = {
            (item["kind"], item["location"], item["id"]): item
            for item in record["sources"]
        }
        trades = {
            (item["profession"], item["level"], item["cost"], item["id"]): item
            for item in record["trades"]
        }
        namespace, raw_name = split_id(item_id)
        result.append(
            {
                "id": item_id,
                "name": name,
                "recipes": sorted(set(record["recipes"])),
                "sources": list(sources.values()),
                "trades": list(trades.values()),
                "wiki": "https://minecraft.wiki/w/" + "_".join(word.capitalize() for word in raw_name.split("_"))
                if namespace == "minecraft" else None,
            }
        )
    return result


def parse_advancements(pack: Pack) -> list[dict]:
    root = pack.data / "main/advancement"
    result = []
    if not root.exists():
        return result
    excluded_sections = {"recipe_unlocks", "particle", "multiplayer_support", "cooking_recipes"}
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root)
        section = relative.parts[0] if len(relative.parts) > 1 else "other"
        if section in excluded_sections:
            continue
        raw = pack.read_json(path)
        display = raw.get("display")
        if not isinstance(display, dict):
            continue
        advancement_id = "main:" + relative.with_suffix("").as_posix()
        icon = display.get("icon", {})
        result.append(
            {
                "id": advancement_id,
                "section": section,
                "title": pack.component_text(display.get("title")) or relative.stem.replace("_", " ").title(),
                "description": pack.component_text(display.get("description")),
                "frame": display.get("frame", "task"),
                "hidden": display.get("hidden", False),
                "parent": raw.get("parent"),
                "iconId": normalize_id(icon.get("id")) if isinstance(icon, dict) and icon.get("id") else None,
                "source": f"data/main/advancement/{relative.as_posix()}",
            }
        )
    known_ids = {item["id"] for item in result}
    for item in result:
        item["children"] = sorted(
            candidate["id"] for candidate in result if candidate.get("parent") == item["id"]
        )
        item["parentVisible"] = item.get("parent") in known_ids
    return result


def parse_guides() -> dict:
    path = PACKAGE_ROOT / "curation/matcha_guides.json"
    if not path.exists():
        return {"progression": [], "differences": []}
    return json.loads(path.read_text(encoding="utf-8"))


class Assets:
    def __init__(self, pack: Pack, output: Path, reuse_site: Path | None, fetch_wiki: bool):
        self.pack = pack
        self.output = output
        self.image_dir = output / "images"
        self.fetch_wiki = fetch_wiki
        self.cache = Path.home() / "AppData/Local/minecraft-datapack-wiki/cache"
        self.reuse_recipe = {}
        self.reuse_item = {}
        if reuse_site:
            self._load_reuse(reuse_site)

    def _load_reuse(self, site):
        data_file = site / "data.js"
        if not data_file.exists():
            return
        raw = data_file.read_text(encoding="utf-8")
        old = json.loads(raw.removeprefix("window.MATCHA_DATA=").strip().removesuffix(";"))
        for recipe in old.get("recipes", []):
            self.reuse_recipe[recipe["id"]] = self._read_reuse_icon(site, recipe.get("icon"))
            for ingredient in recipe.get("ingredients", []):
                self.reuse_item[normalize_id(ingredient["id"])] = self._read_reuse_icon(site, ingredient.get("icon"))

    @staticmethod
    def _read_reuse_icon(site, relative):
        path = site / relative if relative else None
        return path.read_bytes() if path and path.exists() else None

    def _local(self, item_id, model_id=None):
        namespace, name = split_id(model_id or item_id)
        if namespace != "minecraft":
            return None
        definition = self.pack.read_json(self.pack.assets / f"items/{name}.json")
        model_name = definition.get("model", {}).get("model", name)
        model_name = model_name.removeprefix("minecraft:").removeprefix("item/")
        model = self.pack.read_json(self.pack.assets / f"models/item/{model_name}.json")
        texture = model.get("textures", {}).get("layer0")
        candidates = []
        if texture:
            candidates.append(self.pack.assets / f"textures/{texture.removeprefix('minecraft:')}.png")
        candidates.extend(
            (
                self.pack.assets / f"textures/item/{model_name}.png",
                self.pack.assets / f"textures/item/{name}.png",
                self.pack.assets / f"textures/block/{name}.png",
            )
        )
        return next((path.read_bytes() for path in candidates if path.exists()), None)

    def _wiki(self, item_id):
        namespace, name = split_id(item_id)
        if namespace != "minecraft" or item_id.startswith("#"):
            return None
        filename = "Invicon_" + "_".join(word.capitalize() for word in name.split("_")) + ".png"
        cached = self.cache / filename
        if cached.exists():
            return cached.read_bytes()
        if not self.fetch_wiki:
            return None
        url = "https://minecraft.wiki/w/Special:Redirect/file/" + urllib.parse.quote(filename)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=20) as response:
                content = response.read()
            if not content.startswith((b"\x89PNG", b"GIF8")):
                return None
            self.cache.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(content)
            return content
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            return None

    def save(self, content):
        if not content:
            return None
        extension = ".gif" if content.startswith(b"GIF8") else ".png"
        name = hashlib.sha256(content).hexdigest()[:20] + extension
        path = self.image_dir / name
        if not path.exists():
            path.write_bytes(content)
        return "images/" + name

    def apply(self, recipes):
        self.image_dir.mkdir(parents=True, exist_ok=True)
        item_cache = {}
        missing = set()
        for recipe in recipes:
            components = recipe["_components"]
            model_id = components.get("minecraft:item_model")
            content = self._local(recipe["resultId"], model_id) or self.reuse_recipe.get(recipe["id"])
            if not content:
                content = self.reuse_item.get(recipe["resultId"])
            recipe["icon"] = self.save(content)
            for ingredient in recipe["ingredients"]:
                item_id = ingredient["id"]
                if item_id.startswith("#"):
                    continue
                if item_id not in item_cache:
                    item_cache[item_id] = self._local(item_id) or self.reuse_item.get(item_id)
                ingredient["icon"] = self.save(item_cache[item_id])
                if not ingredient["icon"]:
                    missing.add(item_id)
        if self.fetch_wiki and missing:
            with ThreadPoolExecutor(max_workers=6) as pool:
                futures = {pool.submit(self._wiki, item_id): item_id for item_id in missing}
                for future in as_completed(futures):
                    item_cache[futures[future]] = future.result()
            for recipe in recipes:
                if not recipe["icon"]:
                    recipe["icon"] = self.save(item_cache.get(recipe["resultId"]) or self._wiki(recipe["resultId"]))
                for ingredient in recipe["ingredients"]:
                    if not ingredient.get("icon") and not ingredient["id"].startswith("#"):
                        ingredient["icon"] = self.save(item_cache.get(ingredient["id"]))
def metadata(pack: Pack, pack_path: Path) -> dict:
    raw = pack.read_json(pack.root / "pack.mcmeta")
    description = raw.get("pack", {}).get("description", "Minecraft Datapack")
    title = pack.component_text(description).splitlines()[0] or pack_path.stem
    return {
        "title": title,
        "sourceFile": pack_path.name,
        "sha256": hashlib.sha256(pack_path.read_bytes()).hexdigest(),
        "minFormat": raw.get("pack", {}).get("min_format"),
        "maxFormat": raw.get("pack", {}).get("max_format"),
    }


def build(pack_path: Path, output: Path, fetch_wiki_icons=False, reuse_site=None) -> None:
    pack_path = pack_path.resolve()
    output = output.resolve()
    if not pack_path.is_file():
        raise FileNotFoundError(pack_path)
    with tempfile.TemporaryDirectory(prefix="datapack-wiki-") as temporary:
        with zipfile.ZipFile(pack_path) as archive:
            archive.extractall(temporary)
        pack = Pack(Path(temporary))
        recipes = parse_recipes(pack)
        foods = parse_food(pack, recipes)
        acquisition = parse_acquisition(pack, recipes)

        output.mkdir(parents=True, exist_ok=True)
        assets = Assets(pack, output, reuse_site, fetch_wiki_icons)
        image_dir = output / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        assets.apply(recipes)
        recipe_map = {recipe["id"]: recipe for recipe in recipes}
        for food in foods:
            first = next((recipe_map[item] for item in food["recipes"] if item in recipe_map), None)
            food["icon"] = first.get("icon") if first else None
        items = build_items(pack, recipes, foods, acquisition)
        blessings = parse_blessings(pack, recipes)
        enchantments = parse_enchantments(pack, recipes, blessings)
        advancements = parse_advancements(pack)
        guides = parse_guides()
        for recipe in recipes:
            recipe.pop("_components", None)

        mechanics_root = pack.data / "main/function/mechanic"
        mechanics = sorted(
            path.name.replace("_", " ").title()
            for path in mechanics_root.iterdir()
            if path.is_dir()
        ) if mechanics_root.exists() else []
        minecraft_root = pack.data / "minecraft"
        overrides = [
            {"type": path.name, "count": sum(1 for item in path.rglob("*") if item.is_file())}
            for path in sorted(minecraft_root.iterdir())
            if path.is_dir()
        ] if minecraft_root.exists() else []
        wiki_data = {
            "pack": metadata(pack, pack_path),
            "recipes": recipes,
            "enchantments": enchantments,
            "blessings": blessings,
            "advancements": advancements,
            "progression": guides["progression"],
            "differences": guides["differences"],
            "mechanics": mechanics,
            "overrides": overrides,
            "acquisition": acquisition,
            "foodItems": foods,
            "items": items,
            "foodEffects": sorted(
                {
                    (effect["id"], effect["name"], effect["category"])
                    for food in foods for effect in food["effects"]
                }
            ),
        }
        (output / "data.js").write_text(
            "window.MATCHA_DATA=" + json.dumps(wiki_data, separators=(",", ":")) + ";\n",
            encoding="utf-8",
        )
        index_template = (TEMPLATES / "index.html").read_text(encoding="utf-8")
        (output / "index.html").write_text(
            index_template.replace("{{PACK_TITLE}}", wiki_data["pack"]["title"]),
            encoding="utf-8",
        )
        for name in ("app.js", "styles.css"):
            shutil.copy2(TEMPLATES / name, output / name)
        shutil.copy2(TEMPLATES / "server.py", output / "server.py")
        (output / "Start Wiki.cmd").write_text(
            '@echo off\r\ncd /d "%~dp0"\r\npython server.py\r\n'
            'if errorlevel 1 (echo. & echo Python could not start the wiki. & pause)\r\n',
            encoding="ascii",
        )
        print(f"Built {output}")
        print(
            f"{len(recipes)} recipes, {len(items)} item entries, "
            f"{len(acquisition)} acquisition records, "
            f"{len(foods)} food variants, {len(enchantments)} enchantments"
        )
