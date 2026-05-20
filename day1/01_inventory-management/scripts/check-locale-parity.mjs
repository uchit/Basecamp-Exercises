#!/usr/bin/env node
/**
 * Locale parity check.
 *
 * Imports en.js + ja.js, recursively diffs their key structures, exits 1 if
 * any key is missing from either side. Run from the repo root (or from this
 * script's directory).
 *
 *   node day1/01_inventory-management/scripts/check-locale-parity.mjs
 */

import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const LOCALES_DIR = resolve(__dirname, "..", "client", "src", "locales");

const en = (await import(`${LOCALES_DIR}/en.js`)).default;
const ja = (await import(`${LOCALES_DIR}/ja.js`)).default;

// Keys that are intentionally one-way (present in one locale only by design).
// Adding to this list requires a one-line justification.
const ALLOWED_ONE_WAY = new Set([
  // Japanese-only fallback lookup: source data (orders/inventory) is English,
  // the JA locale maps English product names to Japanese display strings.
  "productNames",
  // Same pattern for customer names.
  "customerNames",
]);

const missing = []; // { side, path }

function diff(a, b, path = "") {
  const aIsObj = a && typeof a === "object" && !Array.isArray(a);
  const bIsObj = b && typeof b === "object" && !Array.isArray(b);
  if (aIsObj !== bIsObj) {
    missing.push({ side: aIsObj ? "ja" : "en",
                   path: path || "(root)",
                   reason: `type mismatch — one side is object, the other is leaf` });
    return;
  }
  if (!aIsObj) return; // both leaves; values can differ across locales by design

  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) {
    const here = path ? `${path}.${k}` : k;
    if (ALLOWED_ONE_WAY.has(here)) continue;
    if (!(k in a)) missing.push({ side: "en", path: here });
    else if (!(k in b)) missing.push({ side: "ja", path: here });
    else diff(a[k], b[k], here);
  }
}

diff(en, ja);

if (missing.length === 0) {
  console.log("Locale parity: en ↔ ja, all keys present on both sides.");
  process.exit(0);
}

console.error(`Locale parity FAILED: ${missing.length} divergence(s)\n`);
for (const m of missing) {
  const side = m.side === "en" ? "missing in en" : "missing in ja";
  const extra = m.reason ? ` — ${m.reason}` : "";
  console.error(`  - ${m.path}  (${side})${extra}`);
}
process.exit(1);
