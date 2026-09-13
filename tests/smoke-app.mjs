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
