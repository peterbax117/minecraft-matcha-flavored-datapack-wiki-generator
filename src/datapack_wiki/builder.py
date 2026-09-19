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


def number_provider_text(value, default):
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, dict):
        return str(value)
    provider_type = value.get("type", "provider").split(":")[-1].replace("_", " ")
    if provider_type == "constant":
        return number_provider_text(value.get("value"), default)
    if provider_type == "uniform":
        minimum = number_provider_text(value.get("min"), "?")
        maximum = number_provider_text(value.get("max"), "?")
        return f"{minimum} to {maximum} (uniform)"
    if provider_type == "binomial":
        trials = number_provider_text(value.get("n"), "?")
        probability = number_provider_text(value.get("p"), "?")
        return f"binomial (n={trials}, p={probability})"
    return f"{provider_type}: {json.dumps(value, separators=(',', ':'))}"


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


def parse_trades(pack: Pack) -> list[dict]:
    levels = {"1": "Novice", "2": "Apprentice", "3": "Journeyman", "4": "Expert", "5": "Master"}
    result = []
    for namespace_root in sorted(path for path in pack.data.iterdir() if path.is_dir()):
        trade_root = namespace_root / "villager_trade"
        if not trade_root.exists():
            continue
        for path in sorted(trade_root.rglob("*.json")):
            raw = pack.read_json(path)
            gives = raw.get("gives", {})
            if not isinstance(gives, dict) or not gives.get("id"):
                continue
            components = gives.get("components", {})
            components = components if isinstance(components, dict) else {}
            result_id = normalize_id(gives["id"])
            result_name = pack.component_text(components.get("minecraft:item_name")) or pack.item_name(result_id)
            costs = []
            for field in ("wants", "additional_wants"):
                entries = raw.get(field, [])
                entries = entries if isinstance(entries, list) else [entries]
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("id"):
                        item_id = normalize_id(entry["id"])
                        costs.append(
                            {
                                "id": item_id,
                                "name": pack.item_name(item_id, entry.get("components")),
                                "count": number_provider_text(entry.get("count"), 1),
                                "modelId": entry.get("components", {}).get("minecraft:item_model"),
                            }
                        )
            contents = []
            for entry in components.get("minecraft:bundle_contents", []):
                if isinstance(entry, dict) and entry.get("id"):
                    item_id = normalize_id(entry["id"])
                    contents.append(
                        {
                            "id": item_id,
                            "name": pack.item_name(item_id, entry.get("components")),
                            "count": number_provider_text(entry.get("count"), 1),
                            "modelId": entry.get("components", {}).get("minecraft:item_model"),
                        }
                    )
            relative = path.relative_to(trade_root)
            namespace = namespace_root.name
            result.append(
                {
                    "id": f"{namespace}:{relative.with_suffix('').as_posix()}",
                    "profession": relative.parts[0].replace("_", " ").title(),
                    "level": levels.get(relative.parts[1], relative.parts[1]) if len(relative.parts) > 2 else "Any",
                    "costs": costs,
                    "result": {
                        "id": result_id,
                        "name": result_name,
                        "count": number_provider_text(gives.get("count"), 1),
                        "modelId": components.get("minecraft:item_model"),
                    },
                    "bundleContents": contents,
                    "maxUses": number_provider_text(raw.get("max_uses"), 4),
                    "xp": number_provider_text(raw.get("xp"), 1),
                    "reputationDiscount": number_provider_text(raw.get("reputation_discount"), 0),
                    "source": f"data/{namespace}/villager_trade/{relative.as_posix()}",
                }
            )
    return result


def parse_acquisition(pack: Pack, recipes: list[dict], trades: list[dict]) -> list[dict]:
    names = {}
    acquired = defaultdict(
        lambda: {"recipes": [], "sources": [], "sourceVariants": [], "trades": []}
    )
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
        found = {}

        def walk(value):
            if isinstance(value, dict):
                if value.get("type") == "minecraft:item" and value.get("name"):
                    item_id = normalize_id(value["name"])
                    components = {}
                    for function in value.get("functions", []):
                        if function.get("function", "").endswith("set_components"):
                            components.update(function.get("components", {}))
                    item = {
                        "id": item_id,
                        "name": pack.item_name(item_id, components),
                        "modelId": components.get("minecraft:item_model"),
                    }
                    key = (item["id"], item["name"], item["modelId"])
                    found[key] = item
                elif value.get("type") == "minecraft:loot_table":
                    reference = value.get("value") or value.get("name")
                    if isinstance(reference, str):
                        for item in contents(normalize_id(reference), seen.copy()):
                            key = (item["id"], item["name"], item.get("modelId"))
                            found[key] = item
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(tables.get(table_id, {}))
        return list(found.values())

    def source_label(table_id):
        path = table_id.split(":", 1)[1]
        parts = path.split("/")
        pretty = lambda value: value.replace("_", " ").title()
        if parts[0] == "chests":
            if len(parts) > 1 and parts[1] == "equipment":
                return None
            location_parts = parts[1:]
            if location_parts and location_parts[0] == "village":
                location_parts[-1] = location_parts[-1].removeprefix("village_")
            location = pretty(" ".join(location_parts))
            if path == "chests/village/village_snowy_house":
                location = "Snowy Village House"
            return "Chest loot", location
        if parts[0] == "archaeology":
            if path == "archaeology/trail_ruins_common":
                location = "Trail Ruins (common)"
            elif path == "archaeology/trail_ruins_rare":
                location = "Trail Ruins (rare)"
            else:
                location = pretty(" ".join(parts[1:]))
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
        for item in contents(table_id):
            item_id = item["id"]
            names.setdefault(item_id, item["name"])
            source = {"kind": label[0], "location": label[1], "id": table_id}
            acquired[item_id]["sources"].append(source)
            acquired[item_id]["sourceVariants"].append(
                {"kind": label[0], "location": label[1], "sourceId": table_id}
                | item
            )
    for item_id in ("minecraft:beetroot", "minecraft:beetroot_seeds"):
        acquired[item_id]["sources"].append(
            {
                "kind": "Natural generation",
                "location": "Village farm plots (vanilla beetroot crop behavior)",
                "id": "https://minecraft.wiki/w/Beetroot_Seeds",
            }
        )

    for trade in trades:
        outputs = [(trade["result"], None)]
        outputs.extend((item, trade["result"]["name"]) for item in trade["bundleContents"])
        cost = " + ".join(f"{item['count']} {item['name']}" for item in trade["costs"])
        for output, container in outputs:
            names.setdefault(output["id"], output["name"])
            acquisition_trade = {
                "profession": trade["profession"],
                "level": trade["level"],
                "cost": cost or "Trade cost defined by pack",
                "id": trade["id"],
            }
            if container:
                acquisition_trade["container"] = container
            acquired[output["id"]]["trades"].append(acquisition_trade)

    result = []
    for item_id, name in sorted(names.items(), key=lambda pair: pair[1].lower()):
        record = acquired[item_id]
        sources = {
            (item["kind"], item["location"], item["id"]): item
            for item in record["sources"]
        }
        trades = {
            (item["profession"], item["level"], item["cost"], item["id"], item.get("container")): item
            for item in record["trades"]
        }
        namespace, raw_name = split_id(item_id)
        result.append(
            {
                "id": item_id,
                "name": name,
                "recipes": sorted(set(record["recipes"])),
                "sources": list(sources.values()),
                "sourceVariants": record["sourceVariants"],
                "trades": list(trades.values()),
                "wiki": "https://minecraft.wiki/w/" + "_".join(word.capitalize() for word in raw_name.split("_"))
                if namespace == "minecraft" else None,
            }
        )
    return result


def parse_places(pack: Pack, acquisition: list[dict]) -> dict:
    structures = {}
    for namespace_root in sorted(path for path in pack.data.iterdir() if path.is_dir()):
        structure_root = namespace_root / "worldgen/structure"
        if not structure_root.exists():
            continue
        for path in sorted(structure_root.rglob("*.json")):
            relative = path.relative_to(structure_root)
            namespace = namespace_root.name
            structure_id = f"{namespace}:{relative.with_suffix('').as_posix()}"
            raw = pack.read_json(path)
            selector = raw.get("biomes")
            if isinstance(selector, list):
                biomes = [normalize_id(item) for item in selector if isinstance(item, str)]
            elif isinstance(selector, str) and selector.startswith("#"):
                namespace, tag = split_id(selector[1:])
                tag_path = pack.data / namespace / f"tags/worldgen/biome/{tag}.json"
                values = pack.read_json(tag_path).get("values", [])
                biomes = [normalize_id(item) for item in values if isinstance(item, str)]
            elif isinstance(selector, str):
                biomes = [normalize_id(selector)]
            else:
                biomes = []
            structures[structure_id] = {
                "kind": "Structure definition",
                "id": structure_id,
                "name": relative.stem.replace("_", " ").title(),
                "type": raw.get("type"),
                "biomeSelector": selector,
                "biomes": biomes,
                "step": raw.get("step"),
                "terrainAdaptation": raw.get("terrain_adaptation"),
                "startPool": raw.get("start_pool"),
                "size": raw.get("size"),
                "maxDistance": raw.get("max_distance_from_center"),
                "startHeight": raw.get("start_height"),
                "placement": None,
                "source": f"data/{namespace}/worldgen/structure/{relative.as_posix()}",
            }

    for namespace_root in sorted(path for path in pack.data.iterdir() if path.is_dir()):
        set_root = namespace_root / "worldgen/structure_set"
        if not set_root.exists():
            continue
        for path in sorted(set_root.rglob("*.json")):
            raw = pack.read_json(path)
            relative = path.relative_to(set_root)
            namespace = namespace_root.name
            placement = raw.get("placement", {})
            placement_record = {
                "setId": f"{namespace}:{relative.with_suffix('').as_posix()}",
                "type": placement.get("type"),
                "spacing": placement.get("spacing"),
                "separation": placement.get("separation"),
                "salt": placement.get("salt"),
                "exclusionZone": placement.get("exclusion_zone"),
                "source": f"data/{namespace}/worldgen/structure_set/{relative.as_posix()}",
            }
            for entry in raw.get("structures", []):
                structure_id = entry.get("structure") if isinstance(entry, dict) else entry
                if structure_id in structures:
                    structures[structure_id]["placement"] = placement_record

    locations = {}
    for item in acquisition:
        for source in item["sources"]:
            if source["kind"] not in {"Chest loot", "Archaeology"}:
                continue
            location = locations.setdefault(
                source["id"],
                {
                    "kind": source["kind"],
                    "id": source["id"],
                    "name": source["location"],
                    "items": [],
                    "source": "data/"
                    + source["id"].replace(":", "/loot_table/", 1)
                    + ".json",
                },
            )
            variants = [
                variant
                for variant in item.get("sourceVariants", [])
                if variant["sourceId"] == source["id"]
            ]
            location["items"].extend(
                {
                    "id": variant.get("id", item["id"]),
                    "name": variant.get("name", item["name"]),
                    "modelId": variant.get("modelId"),
                }
                for variant in variants or [item]
            )
    for location in locations.values():
        location["items"] = list(
            {
                (item["id"], item["name"], item.get("modelId")): item
                for item in location["items"]
            }.values()
        )
        location["items"].sort(key=lambda item: item["name"].lower())
    return {
        "structures": sorted(structures.values(), key=lambda item: item["name"].lower()),
        "locations": sorted(locations.values(), key=lambda item: (item["kind"], item["name"].lower())),
    }


def parse_archaeology(pack: Pack) -> list[dict]:
    tables = {}
    for namespace_root in sorted(path for path in pack.data.iterdir() if path.is_dir()):
        loot_root = namespace_root / "loot_table"
        if not loot_root.exists():
            continue
        for path in loot_root.rglob("*.json"):
            relative = path.relative_to(loot_root)
            table_id = f"{namespace_root.name}:{relative.with_suffix('').as_posix()}"
            tables[table_id] = (pack.read_json(path), path)

    def item_entry(entry):
        item_id = normalize_id(entry.get("name"))
        components = {}
        for function in entry.get("functions", []):
            if function.get("function", "").endswith("set_components"):
                components.update(function.get("components", {}))
        return {
            "id": item_id,
            "name": pack.item_name(item_id, components),
            "modelId": components.get("minecraft:item_model"),
        }

    def resolve(table_id, seen=None):
        seen = set() if seen is None else seen
        if table_id in seen or table_id not in tables:
            return []
        seen.add(table_id)
        found = {}
        raw, _ = tables[table_id]
        for pool in raw.get("pools", []):
            for entry in pool.get("entries", []):
                entry_type = entry.get("type")
                if entry_type == "minecraft:item" and entry.get("name"):
                    item = item_entry(entry)
                    found[item["id"] + "\0" + item["name"]] = item
                elif entry_type == "minecraft:loot_table":
                    reference = entry.get("value") or entry.get("name")
                    if isinstance(reference, str):
                        for item in resolve(normalize_id(reference), seen.copy()):
                            found[item["id"] + "\0" + item["name"]] = item
        return sorted(found.values(), key=lambda item: item["name"].lower())

    result = []
    for table_id, (raw, path) in sorted(tables.items()):
        if ":archaeology/" not in table_id:
            continue
        entries = []
        pool_rolls = []
        for pool_index, pool in enumerate(raw.get("pools", []), start=1):
            pool_entries = pool.get("entries", [])
            total_weight = sum(
                entry.get("weight", 1)
                for entry in pool_entries
                if isinstance(entry.get("weight", 1), (int, float))
            )
            pool_rolls.append(number_provider_text(pool.get("rolls"), 1))
            for entry in pool_entries:
                entry_type = entry.get("type")
                weight = entry.get("weight", 1)
                base_chance = (
                    round(weight / total_weight * 100, 2)
                    if isinstance(weight, (int, float)) and total_weight
                    else None
                )
                if entry_type == "minecraft:item" and entry.get("name"):
                    item = item_entry(entry)
                    entries.append(
                        {
                            "kind": "Item",
                            **item,
                            "pool": pool_index,
                            "weight": number_provider_text(weight, 1),
                            "quality": number_provider_text(entry.get("quality"), 0),
                            "baseChance": base_chance,
                            "conditional": bool(entry.get("conditions")),
                            "resolvedItems": [],
                        }
                    )
                elif entry_type == "minecraft:loot_table":
                    reference = entry.get("value") or entry.get("name")
                    if isinstance(reference, str):
                        reference = normalize_id(reference)
                        entries.append(
                            {
                                "kind": "Referenced table",
                                "id": reference,
                                "name": reference.split(":", 1)[1].replace("_", " ").replace("/", " / ").title(),
                                "pool": pool_index,
                                "weight": number_provider_text(weight, 1),
                                "quality": number_provider_text(entry.get("quality"), 0),
                                "baseChance": base_chance,
                                "conditional": bool(entry.get("conditions")),
                                "resolvedItems": resolve(reference),
                            }
                        )
        relative = path.relative_to(pack.data)
        raw_name = table_id.split(":", 1)[1].removeprefix("archaeology/")
        name = raw_name.replace("_", " ").replace("/", " ").title()
        if raw_name == "trail_ruins_common":
            name = "Trail Ruins (common)"
        elif raw_name == "trail_ruins_rare":
            name = "Trail Ruins (rare)"
        result.append(
            {
                "id": table_id,
                "name": name,
                "tableType": raw.get("type"),
                "rolls": pool_rolls,
                "entries": entries,
                "source": f"data/{relative.as_posix()}",
            }
        )
    return result


def parse_fishing(pack: Pack) -> dict:
    tables = {}
    fishing_ids = set()
    for namespace_root in sorted(path for path in pack.data.iterdir() if path.is_dir()):
        loot_root = namespace_root / "loot_table"
        if not loot_root.exists():
            continue
        for path in sorted(loot_root.rglob("*.json")):
            relative = path.relative_to(loot_root)
            table_id = f"{namespace_root.name}:{relative.with_suffix('').as_posix()}"
            tables[table_id] = (pack.read_json(path), path)
            relative_id = relative.with_suffix("").as_posix()
            if relative_id == "gameplay/fishing" or relative_id.startswith("gameplay/fishing/"):
                fishing_ids.add(table_id)

    def item_entry(entry):
        item_id = normalize_id(entry.get("name"))
        components = {}
        modifiers = []
        for function in entry.get("functions", []):
            function_type = function.get("function", "").split(":")[-1]
            if function_type == "set_components":
                components.update(function.get("components", {}))
            elif function_type == "set_potion":
                modifiers.append("Potion: " + str(function.get("id", "specified by pack")))
            elif function_type == "set_count":
                modifiers.append("Count: " + str(number_provider_text(function.get("count"), 1)))
            elif function_type == "set_damage":
                modifiers.append("Randomized durability")
            elif function_type == "enchant_with_levels":
                modifiers.append("Random enchantments")
            elif function_type == "set_contents":
                modifiers.append("Contains pack-defined items")
            elif function_type:
                modifiers.append(function_type.replace("_", " ").title())
        name = pack.item_name(item_id, components)
        if name.startswith(("item.", "block.")):
            name = name.rsplit(".", 1)[-1].replace("_", " ").title()
        return {
            "id": item_id,
            "name": name,
            "modifiers": modifiers,
            "modelId": components.get("minecraft:item_model"),
        }

    def condition_text(condition):
        condition_type = condition.get("condition", "").split(":")[-1]
        if condition_type == "entity_properties":
            hook = condition.get("predicate", {}).get("minecraft:type_specific/fishing_hook", {})
            if hook.get("in_open_water") is True:
                return "Open water only"
        if condition_type == "location_check":
            biomes = condition.get("predicate", {}).get("biomes")
            if isinstance(biomes, list):
                return "Biomes: " + ", ".join(biomes)
            if biomes:
                return "Biome: " + str(biomes)
        return condition_type.replace("_", " ").title() or "Conditional"

    def direct_items(table_id, seen=None):
        seen = set() if seen is None else seen
        if table_id in seen or table_id not in tables:
            return []
        seen.add(table_id)
        found = {}
        raw, _ = tables[table_id]
        for pool in raw.get("pools", []):
            for entry in pool.get("entries", []):
                if entry.get("type") == "minecraft:item" and entry.get("name"):
                    item = item_entry(entry)
                    found[item["id"] + "\0" + item["name"]] = item
                elif entry.get("type") == "minecraft:loot_table":
                    reference = entry.get("value") or entry.get("name")
                    if isinstance(reference, str):
                        for item in direct_items(normalize_id(reference), seen.copy()):
                            found[item["id"] + "\0" + item["name"]] = item
        return sorted(found.values(), key=lambda item: item["name"].lower())

    def table_entries(raw):
        result = []
        for pool_index, pool in enumerate(raw.get("pools", []), start=1):
            entries = pool.get("entries", [])
            has_conditions = any(entry.get("conditions") for entry in entries)
            total_weight = sum(
                entry.get("weight", 1)
                for entry in entries
                if isinstance(entry.get("weight", 1), (int, float))
            )
            for entry in entries:
                weight = entry.get("weight", 1)
                base_chance = (
                    round(weight / total_weight * 100, 2)
                    if not has_conditions and isinstance(weight, (int, float)) and total_weight
                    else None
                )
                conditions = [condition_text(item) for item in entry.get("conditions", [])]
                if entry.get("type") == "minecraft:item" and entry.get("name"):
                    item = item_entry(entry)
                    result.append(
                        {
                            "kind": "Item",
                            **item,
                            "pool": pool_index,
                            "weight": number_provider_text(weight, 1),
                            "quality": number_provider_text(entry.get("quality"), 0),
                            "baseChance": base_chance,
                            "conditions": conditions,
                            "resolvedItems": [],
                        }
                    )
                elif entry.get("type") == "minecraft:loot_table":
                    reference = entry.get("value") or entry.get("name")
                    if isinstance(reference, str):
                        reference = normalize_id(reference)
                        resolved = direct_items(reference)
                        result.append(
                            {
                                "kind": "Referenced table",
                                "id": reference,
                                "name": resolved[0]["name"] if len(resolved) == 1 else reference.split("/")[-1].replace("_", " ").title(),
                                "modifiers": [],
                                "pool": pool_index,
                                "weight": number_provider_text(weight, 1),
                                "quality": number_provider_text(entry.get("quality"), 0),
                                "baseChance": base_chance,
                                "conditions": conditions,
                                "resolvedItems": resolved,
                            }
                        )
        return result

    routes = []
    route_biomes = defaultdict(set)
    globally_eligible = set()
    references = defaultdict(set)
    for table_id in fishing_ids:
        raw, _ = tables[table_id]
        for pool in raw.get("pools", []):
            for entry in pool.get("entries", []):
                if entry.get("type") == "minecraft:loot_table":
                    reference = entry.get("value") or entry.get("name")
                    if isinstance(reference, str):
                        references[table_id].add(normalize_id(reference))

    def reachable(start):
        found = set()
        pending = [start]
        while pending:
            table_id = pending.pop()
            if table_id in found:
                continue
            found.add(table_id)
            pending.extend(references.get(table_id, set()) - found)
        return found

    records = []
    for table_id in sorted(fishing_ids):
        raw, path = tables[table_id]
        relative_id = table_id.split(":", 1)[1]
        entries = table_entries(raw)
        if relative_id == "gameplay/fishing":
            for entry in entries:
                routes.append(entry)
                selectors = []
                for condition in entry["conditions"]:
                    if condition.startswith("Biome: "):
                        selectors.append(condition.removeprefix("Biome: "))
                    elif condition.startswith("Biomes: "):
                        selectors.extend(
                            value.strip() for value in condition.removeprefix("Biomes: ").split(",")
                        )
                descendants = reachable(entry["id"])
                if selectors:
                    for descendant in descendants:
                        route_biomes[descendant].update(selectors)
                else:
                    globally_eligible.update(descendants)
            continue
        role = "Species" if "/fish/" in relative_id else (
            "General rewards" if relative_id.endswith(("/junk", "/treasure")) else "Habitat"
        )
        raw_name = relative_id.split("/")[-1]
        name = raw_name.replace("_", " ").title()
        if role == "Species" and len(entries) == 1:
            name = entries[0]["name"]
        relative = path.relative_to(pack.data)
        records.append(
            {
                "id": table_id,
                "name": name,
                "role": role,
                "entries": entries,
                "biomeSelectors": [],
                "biomes": [],
                "globalBiomes": False,
                "active": False,
                "source": f"data/{relative.as_posix()}",
            }
        )

    for record in records:
        if record["id"] in globally_eligible:
            record["globalBiomes"] = True
            record["active"] = True
            continue
        selectors = sorted(route_biomes.get(record["id"], set()))
        record["biomeSelectors"] = selectors
        record["active"] = bool(selectors)
        biomes = []
        for selector in selectors:
            if selector.startswith("#"):
                namespace, tag = split_id(selector[1:])
                tag_path = pack.data / namespace / f"tags/worldgen/biome/{tag}.json"
                biomes.extend(
                    normalize_id(value)
                    for value in pack.read_json(tag_path).get("values", [])
                    if isinstance(value, str)
                )
            else:
                biomes.append(normalize_id(selector))
        record["biomes"] = sorted(set(biomes))
    return {
        "routes": routes,
        "tables": sorted(records, key=lambda item: (item["role"], item["name"].lower())),
    }


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
                "iconModelId": (
                    (icon.get("components") or {}).get("minecraft:item_model")
                    if isinstance(icon, dict) else None
                ),
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


def build_spoilers(recipes, items, blessings, advancements, progression, places, differences, trades=()):
    path = PACKAGE_ROOT / "curation/matcha_spoilers.json"
    rules = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    stages = defaultdict(dict)
    hidden = defaultdict(list)

    for stage, section in enumerate(progression, start=1):
        for advancement_id in section.get("advancements", []):
            stages["advancement"][advancement_id] = stage
    for advancement in advancements:
        for rule in rules.get("advancementPrefixes", []):
            if advancement["id"].startswith(rule["prefix"]):
                stages["advancement"][advancement["id"]] = rule["stage"]
        if advancement.get("hidden"):
            hidden["advancement"].append(advancement["id"])

    staged_recipe_ids = {}
    for recipe in recipes:
        for rule in rules.get("recipePrefixes", []):
            if recipe["id"].startswith(rule["prefix"]):
                stages["recipe"][recipe["id"]] = rule["stage"]
                staged_recipe_ids[recipe["id"]] = rule["stage"]
    for item in items:
        item_stages = [
            staged_recipe_ids[recipe_id]
            for recipe_id in item.get("outputOf", [])
            if recipe_id in staged_recipe_ids
        ]
        if item_stages:
            stages["item"][item["key"]] = min(item_stages)
    for blessing in blessings:
        stages["blessing"][blessing["id"]] = 6
    stages["place"].update(rules.get("placeStages", {}))
    stages["difference"].update(rules.get("differenceStages", {}))

    item_stage_by_id = {}
    for item in items:
        stage = stages["item"].get(item["key"])
        if stage is not None:
            item_stage_by_id[item["id"]] = min(stage, item_stage_by_id.get(item["id"], stage))
    for trade in trades:
        trade_stage = None
        for rule in rules.get("tradePrefixes", []):
            if trade["id"].startswith(rule["prefix"]):
                trade_stage = rule["stage"]
                break
        if trade_stage is None:
            candidate_ids = [trade["result"]["id"], *(entry["id"] for entry in trade.get("bundleContents", []))]
            candidate_stages = [item_stage_by_id[cid] for cid in candidate_ids if cid in item_stage_by_id]
            if candidate_stages:
                trade_stage = min(candidate_stages)
        if trade_stage is not None:
            stages["trade"][trade["id"]] = trade_stage
    return {
        "defaultMode": "complete",
        "defaultStage": 1,
        "stageCount": len(progression),
        "stages": dict(stages),
        "hidden": dict(hidden),
        "policy": "Only source-backed progression gates and explicitly hidden pack content are classified. Unclassified content remains visible.",
    }


def build_optimum_builds(enchantments, blessings, foods, items):
    path = PACKAGE_ROOT / "curation/matcha_optimum_builds.json"
    if not path.exists():
        return {"personas": [], "allAround": None}
    curation = json.loads(path.read_text(encoding="utf-8"))
    enchantment_by_id = {enchantment["id"]: enchantment for enchantment in enchantments}
    blessing_by_id = {blessing["id"]: blessing for blessing in blessings}
    food_by_recipe = {
        recipe_id: food for food in foods for recipe_id in food.get("recipes", [])
    }
    item_by_recipe = {
        recipe_id: item for item in items for recipe_id in item.get("outputOf", [])
    }
    armor_materials = curation.get("armorMaterials", {})

    def resolve_enchantments(entries):
        resolved = []
        for entry in entries:
            enchantment = enchantment_by_id.get(entry["id"])
            if not enchantment:
                continue
            resolved.append(
                {
                    "id": entry["id"],
                    "name": enchantment["name"],
                    "reason": entry["reason"],
                    "slots": enchantment.get("slots", []),
                }
            )
        return resolved

    def resolve_blessings(entries):
        resolved = []
        for entry in entries:
            blessing = blessing_by_id.get(entry["id"])
            if not blessing:
                continue
            resolved.append(
                {
                    "id": entry["id"],
                    "name": blessing["name"],
                    "reason": entry["reason"],
                    "targets": entry.get("targets", []),
                }
            )
        return resolved

    def resolve_consumables(entries):
        resolved = []
        for entry in entries:
            food = food_by_recipe.get(entry["recipe"])
            if not food:
                continue
            resolved.append({"key": food["key"], "name": food["name"], "reason": entry["reason"]})
        return resolved

    def resolve_armor(entries):
        resolved = []
        for entry in entries:
            material = armor_materials.get(entry["material"])
            if not material:
                continue
            pieces = []
            for slot, recipe_id in material.get("pieces", {}).items():
                item = item_by_recipe.get(recipe_id)
                if not item:
                    continue
                pieces.append(
                    {
                        "slot": slot,
                        "key": item["key"],
                        "name": item["name"],
                        "id": item["id"],
                        "icon": item.get("icon"),
                    }
                )
            if not pieces:
                continue
            resolved.append(
                {
                    "id": entry["material"],
                    "title": material["title"],
                    "summary": material.get("summary", ""),
                    "reason": entry["reason"],
                    "pieces": pieces,
                }
            )
        return resolved

    personas = [
        {
            "id": persona["id"],
            "title": persona["title"],
            "summary": persona.get("summary", ""),
            "enchantments": resolve_enchantments(persona.get("enchantments", [])),
            "blessings": resolve_blessings(persona.get("blessings", [])),
            "consumables": resolve_consumables(persona.get("consumables", [])),
            "armor": resolve_armor(persona.get("armor", [])),
        }
        for persona in curation.get("personas", [])
    ]
    all_around_raw = curation.get("allAround")
    all_around = (
        {
            "title": all_around_raw["title"],
            "summary": all_around_raw.get("summary", ""),
            "enchantments": resolve_enchantments(all_around_raw.get("enchantments", [])),
            "blessings": resolve_blessings(all_around_raw.get("blessings", [])),
            "armor": resolve_armor(all_around_raw.get("armor", [])),
        }
        if all_around_raw
        else None
    )
    return {"personas": personas, "allAround": all_around}


class Assets:
    def __init__(self, pack: Pack, output: Path, reuse_site: Path | None, fetch_wiki: bool):
        self.pack = pack
        self.output = output
        self.image_dir = output / "images"
        self.fetch_wiki = fetch_wiki
        self.cache = Path.home() / "AppData/Local/minecraft-datapack-wiki/cache"
        self.reuse_recipe = {}
        self.reuse_item = {}
        self.failed_wiki = set()
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
        assets = self.pack.root / "assets" / namespace
        definition = self.pack.read_json(assets / f"items/{name}.json")
        model_reference = definition.get("model", {}).get("model", f"{namespace}:item/{name}")
        model_namespace, model_path = split_id(model_reference)
        model_name = model_path.removeprefix("item/").removeprefix("block/")
        model_assets = self.pack.root / "assets" / model_namespace
        model = self.pack.read_json(model_assets / f"models/{model_path}.json")
        texture = model.get("textures", {}).get("layer0")
        candidates = []
        if texture:
            texture_namespace, texture_name = split_id(texture)
            candidates.append(
                self.pack.root / "assets" / texture_namespace / f"textures/{texture_name}.png"
            )
        candidates.extend(
            (
                model_assets / f"textures/item/{model_name}.png",
                assets / f"textures/item/{name}.png",
                assets / f"textures/block/{name}.png",
            )
        )
        return next((path.read_bytes() for path in candidates if path.exists()), None)

    def _wiki(self, item_id):
        if item_id in self.failed_wiki:
            return None
        namespace, name = split_id(item_id)
        if namespace != "minecraft" or item_id.startswith("#"):
            self.failed_wiki.add(item_id)
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
                self.failed_wiki.add(item_id)
                return None
            self.cache.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(content)
            return content
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            self.failed_wiki.add(item_id)
            return None

    def save(self, content):
        if not content:
            return None
        extension = ".gif" if content.startswith(b"GIF8") else ".png"
        name = hashlib.sha256(content).hexdigest()[:20] + extension
        path = self.image_dir / name
        self.image_dir.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        return "images/" + name

    def item_icon(self, item_id, model_id=None):
        content = self._local(item_id, model_id)
        fallback_id = normalize_id(model_id or item_id)
        if not content:
            content = self.reuse_item.get(fallback_id)
        if not content and self.fetch_wiki:
            content = self._wiki(fallback_id)
        return self.save(content)

    def apply_item_icons(self, items):
        items = list(items)
        missing = {
            normalize_id(item.get("modelId") or item["id"])
            for item in items
            if not self._local(item["id"], item.get("modelId"))
            and not self.reuse_item.get(normalize_id(item.get("modelId") or item["id"]))
        }
        if self.fetch_wiki and missing:
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(self._wiki, sorted(missing)))
        for item in items:
            item["icon"] = self.item_icon(item["id"], item.get("modelId"))

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


def compare_pack_zips(current_path: Path, baseline_path: Path) -> dict:
    def files(path):
        with zipfile.ZipFile(path) as archive:
            return {
                item.filename.replace("\\", "/"): hashlib.sha256(archive.read(item)).hexdigest()
                for item in archive.infolist()
                if not item.is_dir()
            }

    def category(path):
        match = re.match(
            r"data/([^/]+)/(recipe|villager_trade|loot_table|advancement|enchantment|worldgen/structure)/(.+)\.json$",
            path,
        )
        if match:
            labels = {
                "recipe": "Recipes",
                "villager_trade": "Villager trades",
                "loot_table": "Loot tables",
                "advancement": "Advancements",
                "enchantment": "Enchantments",
                "worldgen/structure": "Structures",
            }
            return labels[match.group(2)], f"{match.group(1)}:{match.group(3)}"
        if path.startswith("assets/"):
            return "Resource-pack assets", path.removeprefix("assets/")
        if path.startswith("data/"):
            return "Other datapack files", path.removeprefix("data/")
        return "Pack metadata", path

    current = files(current_path)
    baseline = files(baseline_path)
    added_paths = sorted(current.keys() - baseline.keys())
    removed_paths = sorted(baseline.keys() - current.keys())
    changed_paths = sorted(path for path in current.keys() & baseline.keys() if current[path] != baseline[path])
    grouped = defaultdict(lambda: {"added": [], "removed": [], "changed": []})
    for state, paths in (("added", added_paths), ("removed", removed_paths), ("changed", changed_paths)):
        for path in paths:
            label, item_id = category(path)
            grouped[label][state].append({"id": item_id, "path": path})
    return {
        "summary": {
            "added": len(added_paths),
            "removed": len(removed_paths),
            "changed": len(changed_paths),
            "unchanged": sum(current[path] == baseline[path] for path in current.keys() & baseline.keys()),
        },
        "categories": [
            {"name": name, **changes}
            for name, changes in sorted(grouped.items())
        ],
    }


def modrinth_history(project_slug: str, pack_path: Path, compare_version=None) -> dict:
    def api_json(url):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())

    project = api_json(f"https://api.modrinth.com/v2/project/{urllib.parse.quote(project_slug)}")
    versions = api_json(f"https://api.modrinth.com/v2/project/{urllib.parse.quote(project_slug)}/version")
    local_sha512 = hashlib.sha512(pack_path.read_bytes()).hexdigest()
    current_match = next(
        (
            (version, file)
            for version in versions
            for file in version.get("files", [])
            if file.get("hashes", {}).get("sha512") == local_sha512
        ),
        None,
    )
    current, current_file = current_match or (None, None)
    stable = [version for version in versions if version.get("version_type") == "release"]
    latest_stable = stable[0] if stable else None
    baseline = None
    if compare_version:
        baseline = next((version for version in versions if version.get("version_number") == compare_version), None)
        if baseline is None:
            raise ValueError(f"Modrinth version not found: {compare_version}")
    elif current in stable:
        position = stable.index(current)
        baseline = stable[position + 1] if position + 1 < len(stable) else None
    elif current:
        baseline = next(
            (
                version
                for version in stable
                if version.get("date_published", "") < current.get("date_published", "")
            ),
            None,
        )

    comparison = None
    if baseline:
        baseline_files = baseline.get("files", [])
        if current_file:
            current_suffix = Path(current_file.get("filename", "")).suffix.lower()
            candidates = [
                item
                for item in baseline_files
                if bool(item.get("primary")) == bool(current_file.get("primary"))
                and Path(item.get("filename", "")).suffix.lower() == current_suffix
                and item.get("file_type") == current_file.get("file_type")
            ]
            if len(candidates) != 1:
                raise ValueError(
                    f"Could not identify one matching artifact in Modrinth version "
                    f"{baseline.get('version_number')} for {current_file.get('filename')}"
                )
            file = candidates[0]
        else:
            file = next((item for item in baseline_files if item.get("primary")), None)
            file = file or (baseline_files[0] if len(baseline_files) == 1 else None)
            if file is None:
                raise ValueError(
                    f"Could not identify a baseline artifact in Modrinth version "
                    f"{baseline.get('version_number')}"
                )
        if file:
            cache = Path.home() / "AppData/Local/minecraft-datapack-wiki/versions" / project_slug
            cache.mkdir(parents=True, exist_ok=True)
            baseline_path = cache / file["filename"]
            expected = file.get("hashes", {}).get("sha512")
            valid = baseline_path.exists() and (
                not expected or hashlib.sha512(baseline_path.read_bytes()).hexdigest() == expected
            )
            if not valid:
                request = urllib.request.Request(file["url"], headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(request, timeout=60) as response:
                    content = response.read()
                if expected and hashlib.sha512(content).hexdigest() != expected:
                    raise ValueError(f"Hash verification failed for {file['filename']}")
                baseline_path.write_bytes(content)
            comparison = compare_pack_zips(pack_path, baseline_path)
            comparison.update(
                {
                    "currentVersion": current.get("version_number") if current else pack_path.name,
                    "currentFile": current_file.get("filename") if current_file else pack_path.name,
                    "baselineVersion": baseline.get("version_number"),
                    "baselineFile": file["filename"],
                }
            )

    def release_record(version):
        return {
            "id": version.get("id"),
            "name": version.get("name"),
            "version": version.get("version_number"),
            "type": version.get("version_type"),
            "published": version.get("date_published"),
            "gameVersions": version.get("game_versions", []),
            "changelog": version.get("changelog") or "",
            "featured": version.get("featured", False),
            "current": bool(current and version.get("id") == current.get("id")),
        }

    return {
        "project": {
            "id": project.get("id"),
            "slug": project.get("slug"),
            "title": project.get("title"),
            "url": f"https://modrinth.com/datapack/{project.get('slug')}",
            "description": project.get("description"),
        },
        "latestStable": latest_stable.get("version_number") if latest_stable else None,
        "latestPublished": versions[0].get("version_number") if versions else None,
        "currentVersion": current.get("version_number") if current else None,
        "releases": [release_record(version) for version in versions],
        "comparison": comparison,
    }


def build(
    pack_path: Path,
    output: Path,
    fetch_wiki_icons=False,
    reuse_site=None,
    modrinth_project=None,
    compare_version=None,
) -> None:
    pack_path = pack_path.resolve()
    output = output.resolve()
    if compare_version and not modrinth_project:
        raise ValueError("--compare-version requires --modrinth-project")
    if not pack_path.is_file():
        raise FileNotFoundError(pack_path)
    with tempfile.TemporaryDirectory(prefix="datapack-wiki-") as temporary:
        with zipfile.ZipFile(pack_path) as archive:
            archive.extractall(temporary)
        pack = Pack(Path(temporary))
        recipes = parse_recipes(pack)
        foods = parse_food(pack, recipes)
        trades = parse_trades(pack)
        acquisition = parse_acquisition(pack, recipes, trades)
        places = parse_places(pack, acquisition)
        archaeology = parse_archaeology(pack)
        fishing = parse_fishing(pack)
        releases = (
            modrinth_history(modrinth_project, pack_path, compare_version)
            if modrinth_project else None
        )

        output.mkdir(parents=True, exist_ok=True)
        assets = Assets(pack, output, reuse_site, fetch_wiki_icons)
        image_dir = output / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        assets.apply(recipes)
        icon_records = [
            item
            for trade in trades
            for item in [*trade["costs"], trade["result"], *trade["bundleContents"]]
        ]
        icon_records.extend(
            item
            for site in archaeology
            for entry in site["entries"]
            for item in ([entry] if entry["kind"] == "Item" else []) + entry["resolvedItems"]
        )
        icon_records.extend(item for place in places["locations"] for item in place["items"])
        icon_records.extend(
            item
            for record in fishing["tables"]
            for entry in record["entries"]
            for item in ([entry] if entry["kind"] == "Item" else []) + entry["resolvedItems"]
        )
        assets.apply_item_icons(icon_records)
        recipe_map = {recipe["id"]: recipe for recipe in recipes}
        for food in foods:
            first = next((recipe_map[item] for item in food["recipes"] if item in recipe_map), None)
            food["icon"] = first.get("icon") if first else None
        items = build_items(pack, recipes, foods, acquisition)
        blessings = parse_blessings(pack, recipes)
        enchantments = parse_enchantments(pack, recipes, blessings)
        advancements = parse_advancements(pack)
        for advancement in advancements:
            advancement["icon"] = (
                assets.item_icon(advancement["iconId"], advancement.get("iconModelId"))
                if advancement.get("iconId") else None
            )
        item_icons = {item["key"]: item.get("icon") for item in items}
        for enchantment in enchantments:
            for equipment in enchantment["equipment"]:
                equipment["icon"] = item_icons.get(equipment["itemKey"])
        guides = parse_guides()
        spoilers = build_spoilers(
            recipes,
            items,
            blessings,
            advancements,
            guides["progression"],
            places,
            guides["differences"],
            trades,
        )
        optimum_builds = build_optimum_builds(enchantments, blessings, foods, items)
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
            "spoilers": spoilers,
            "optimumBuilds": optimum_builds,
            "mechanics": mechanics,
            "overrides": overrides,
            "acquisition": acquisition,
            "trades": trades,
            "places": places,
            "archaeology": archaeology,
            "fishing": fishing,
            "releaseHistory": releases,
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
