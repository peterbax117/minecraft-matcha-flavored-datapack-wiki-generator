import fs from "node:fs";
import vm from "node:vm";

const site = process.argv[2];
if (!site) throw new Error("Usage: node tests/smoke-app.mjs <generated-site>");

const element = () => ({
  value: "",
  innerHTML: "",
  textContent: "",
  dataset: {},
  classList: { toggle() {} },
  focus() {},
  querySelectorAll() {
    return [];
  },
});
const elements = new Map();
const document = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  },
  querySelectorAll() {
    return [];
  },
};
const context = {
  console,
  document,
  location: { hash: "" },
  scrollTo() {},
  setTimeout,
  clearTimeout,
};
context.window = {
  addEventListener() {},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(`${site}/data.js`, "utf8"), context);
vm.runInContext(fs.readFileSync(`${site}/app.js`, "utf8"), context);

if (!elements.get("item-results").innerHTML.includes("catalog-item")) {
  throw new Error("Item Catalogue did not render");
}
if (!elements.get("food-results").innerHTML.includes("food-card")) {
  throw new Error("Food catalogue did not render");
}
if (
  context.window.MATCHA_DATA.trades.length &&
  !elements.get("trade-results").innerHTML.includes("trade-card")
) {
  throw new Error("Villager trade catalogue did not render");
}
if (context.window.MATCHA_DATA.trades.length) {
  const trade = context.window.MATCHA_DATA.trades[0];
  elements.get("trade-search").value = trade.result.name;
  elements.get("trade-search").oninput();
  await new Promise((resolve) => setTimeout(resolve, 200));
  if (!elements.get("trade-results").innerHTML.includes(trade.result.name)) {
    throw new Error("Villager trade filtering did not return its output item");
  }
  elements.get("global-search").value = trade.result.name;
  elements.get("global-search").oninput();
  await new Promise((resolve) => setTimeout(resolve, 200));
  if (!elements.get("global-results").innerHTML.includes("Villager trades")) {
    throw new Error("Unified search did not return villager trades");
  }
}
if (
  context.window.MATCHA_DATA.places.structures.length &&
  !elements.get("place-results").innerHTML.includes("place-card")
) {
  throw new Error("Places and structures catalogue did not render");
}
if (context.window.MATCHA_DATA.places.locations.length) {
  const place = context.window.MATCHA_DATA.places.locations[0];
  elements.get("place-search").value = place.name;
  elements.get("place-search").oninput();
  await new Promise((resolve) => setTimeout(resolve, 200));
  if (!elements.get("place-results").innerHTML.includes(place.name)) {
    throw new Error("Place filtering did not return a matching location");
  }
}
if (
  context.window.MATCHA_DATA.archaeology.length &&
  !elements.get("archaeology-results").innerHTML.includes("archaeology-card")
) {
  throw new Error("Archaeology catalogue did not render");
}
if (
  context.window.MATCHA_DATA.archaeology.some((site) =>
    site.entries.some((entry) => entry.icon)
  ) &&
  !elements.get("archaeology-results").innerHTML.includes("archaeology-item")
) {
  throw new Error("Archaeology item images did not render");
}
if (
  context.window.MATCHA_DATA.fishing.tables.length &&
  !elements.get("fishing-results").innerHTML.includes("fishing-")
) {
  throw new Error("Fishing catalogue did not render");
}
const stagedItemKey = Object.keys(context.window.MATCHA_DATA.spoilers.stages.item)[0];
if (stagedItemKey) context.openCatalogItem(stagedItemKey);
elements.get("spoiler-mode").value = "minimal";
elements.get("spoiler-mode").onchange();
if (
  stagedItemKey &&
  !elements.get("obtain-results").innerHTML.includes("Hidden by minimal spoiler mode")
) {
  throw new Error("Mode change left a staged item detail visible");
}
if (
  context.window.MATCHA_DATA.blessings.length &&
  !elements.get("blessing-results").innerHTML.includes("Hidden by minimal spoiler mode")
) {
  throw new Error("Minimal spoiler mode did not redact Blessings");
}
if (
  context.window.MATCHA_DATA.advancements.some((item) => item.hidden) &&
  context.window.MATCHA_DATA.advancements
    .filter((item) => item.hidden)
    .some((item) => elements.get("advancement-results").innerHTML.includes(item.title))
) {
  throw new Error("Minimal spoiler mode exposed a hidden advancement");
}
if (
  context.window.MATCHA_DATA.releaseHistory &&
  !elements.get("version-comparison").innerHTML.includes("Hidden by minimal spoiler mode")
) {
  throw new Error("Minimal spoiler mode exposed release comparison details");
}
elements.get("spoiler-mode").value = "progression";
elements.get("spoiler-stage").value = "5";
elements.get("spoiler-stage").onchange();
elements.get("spoiler-mode").onchange();
if (elements.get("spoiler-stage").innerHTML.includes("Complete the End path")) {
  throw new Error("Progression selector exposed a future stage title");
}
if (
  context.window.MATCHA_DATA.blessings.length &&
  !elements.get("blessing-results").innerHTML.includes("Hidden by progression spoiler mode")
) {
  throw new Error("Stage 5 progression mode exposed Blessings");
}
elements.get("spoiler-stage").value = "6";
elements.get("spoiler-stage").onchange();
if (
  context.window.MATCHA_DATA.blessings.length &&
  !elements.get("blessing-results").innerHTML.includes("blessing-card")
) {
  throw new Error("Stage 6 progression mode did not reveal Blessings");
}
elements.get("spoiler-mode").value = "complete";
elements.get("spoiler-mode").onchange();
if (
  context.window.MATCHA_DATA.blessings.length &&
  !elements.get("blessing-results").innerHTML.includes("blessing-card")
) {
  throw new Error("Complete spoiler mode did not restore Blessings");
}
if (context.window.MATCHA_DATA.releaseHistory) {
  if (
    !elements.get("release-summary").innerHTML.includes("Official source") ||
    !elements.get("release-results").innerHTML.includes("release-card")
  ) {
    throw new Error("Modrinth release history did not render");
  }
  if (
    context.window.MATCHA_DATA.releaseHistory.comparison &&
    !elements.get("version-comparison").innerHTML.includes("ZIP comparison")
  ) {
    throw new Error("Modrinth comparison did not render");
  }
  if (
    !context.window.MATCHA_DATA.releaseHistory.comparison &&
    !elements.get("version-comparison").innerHTML.includes("No earlier stable release")
  ) {
    throw new Error("No-baseline comparison fallback did not render");
  }
} else if (!elements.get("release-summary").innerHTML.includes("does not include Modrinth metadata")) {
  throw new Error("Offline release-history fallback did not render");
}
if (
  context.window.MATCHA_DATA.enchantments.length &&
  !elements.get("enchantment-results").innerHTML.includes("enchant-card")
) {
  throw new Error("Enchantment catalogue did not render");
}
if (
  context.window.MATCHA_DATA.blessings.length &&
  !elements.get("blessing-results").innerHTML.includes("blessing-card")
) {
  throw new Error("Blessing catalogue did not render");
}
if (
  context.window.MATCHA_DATA.advancements.length &&
  !elements.get("advancement-results").innerHTML.includes("advancement-card")
) {
  throw new Error("Progression advancement catalogue did not render");
}
if (
  context.window.MATCHA_DATA.differences.length &&
  !elements.get("difference-results").innerHTML.includes("difference-card")
) {
  throw new Error("Differences from Vanilla did not render");
}
elements.get("global-search").value = "Aqua Affinity";
elements.get("global-search").oninput();
await new Promise((resolve) => setTimeout(resolve, 200));
if (
  !elements.get("global-results").innerHTML.includes("Recipes") ||
  !elements.get("global-results").innerHTML.includes("Blessings")
) {
  throw new Error("Unified search did not return cross-category results");
}
elements.get("all-search").value = "Tomatoes";
elements.get("all-search").oninput();
await new Promise((resolve) => setTimeout(resolve, 200));
if (!elements.get("all-results").innerHTML.includes("Matching items")) {
  throw new Error("Recipe search did not render matching item identities");
}
console.log("Generated app initialized and rendered all catalogue pages");
