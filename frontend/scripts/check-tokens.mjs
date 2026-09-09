/*
  Asserts the two dark palettes in tokens.css declare the same values.

  One covers the system preference and lives inside a media query; the other covers an
  explicit choice from the toggle and cannot. CSS has no way to share a declaration list
  between them, so the list is written twice, and a list written twice drifts. It already had:
  the explicit-dark block was missing eleven tokens, so choosing dark on a machine set to
  light inherited light values for the highlighter tint and the inverse text colour.

  Run by `npm run build`.
*/

import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const file = join(root, "src", "styles", "tokens.css");

/** Every `--name: value` in a chunk of CSS, as a map, with whitespace collapsed. */
function declarations(block) {
  const found = new Map();
  for (const match of block.matchAll(/(--[\w-]+)\s*:\s*([^;}]+)/g)) {
    found.set(match[1], match[2].replace(/\s+/g, " ").trim());
  }
  return found;
}

const source = await readFile(file, "utf8");
const blocks = [...source.matchAll(/\/\* dark:start \*\/([\s\S]*?)\/\* dark:end \*\//g)].map(
  (match) => match[1],
);

if (blocks.length !== 2) {
  console.error(`check-tokens: expected 2 dark blocks in tokens.css, found ${blocks.length}`);
  process.exit(1);
}

const [media, explicit] = blocks.map(declarations);
const problems = [];

for (const [name, value] of media) {
  if (!explicit.has(name)) problems.push(`${name} is missing from the explicit-dark block`);
  else if (explicit.get(name) !== value) {
    problems.push(`${name} differs: media has "${value}", explicit has "${explicit.get(name)}"`);
  }
}
for (const name of explicit.keys()) {
  if (!media.has(name)) problems.push(`${name} is missing from the system-dark block`);
}

if (problems.length) {
  console.error("check-tokens: the two dark palettes in tokens.css have drifted apart.");
  for (const problem of problems) console.error(`  ${problem}`);
  process.exit(1);
}

console.log(`check-tokens: ${media.size} dark tokens match across both blocks.`);
