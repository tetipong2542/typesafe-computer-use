import fs from "node:fs";
import path from "node:path";
import { load } from "js-yaml";

const root = path.resolve(import.meta.dirname, "..");
const allow = new Set(JSON.parse(fs.readFileSync(path.join(import.meta.dirname, "golden-leakage-allowlist.json"), "utf8")).map(({ file, literal }) => `${file}:${String(literal).toLowerCase()}`));
const literals = [];
function extractPredicateValues(pred) {
  if (!pred || typeof pred !== "object") return [];
  const vals = [];
  if (Array.isArray(pred.contains)) vals.push(...pred.contains);
  if (Array.isArray(pred.contains_any)) vals.push(...pred.contains_any);
  if (Array.isArray(pred.all)) for (const sub of pred.all) vals.push(...extractPredicateValues(sub));
  if (Array.isArray(pred.any)) for (const sub of pred.any) vals.push(...extractPredicateValues(sub));
  return vals;
}

function extractPredicateMatches(pred) {
  if (!pred || typeof pred !== "object") return [];
  const matches = [];
  if (pred.matches) matches.push(pred.matches);
  if (Array.isArray(pred.all)) for (const sub of pred.all) matches.push(...extractPredicateMatches(sub));
  if (Array.isArray(pred.any)) for (const sub of pred.any) matches.push(...extractPredicateMatches(sub));
  return matches;
}

for (const file of fs.readdirSync(path.join(root, "tasks")).filter((file) => file.endsWith(".yaml") && !file.startsWith("calibration-"))) {
  const doc = load(fs.readFileSync(path.join(root, "tasks", file), "utf8"));
  for (const task of doc.tasks ?? []) {
    for (const value of extractPredicateValues(task.predicate)) {
      if (String(value).length >= 6) literals.push(String(value).toLowerCase());
    }
  }
}
const hits = [];
// 1. Scan goldens for answer leakage
for (const file of fs.readdirSync(path.join(root, "goldens")).filter((file) => file.endsWith(".patch"))) {
  const added = fs.readFileSync(path.join(root, "goldens", file), "utf8").split("\n").filter((line) => /^\+.*\b(description|title)\s*[:=]/.test(line));
  for (const literal of literals) for (const line of added) if (line.toLowerCase().includes(literal) && !allow.has(`${file}:${literal}`)) hits.push(`${file}:${literal}: ${line}`);
}

// 2. Scan arms for tool description and prompt template leakage
const armEntities = new Set(["development", "figma", "dribbble", "github", "react", "collaborative"]);
for (const file of fs.readdirSync(path.join(root, "tasks")).filter((f) => f.endsWith(".yaml") && !f.startsWith("calibration-"))) {
  const doc = load(fs.readFileSync(path.join(root, "tasks", file), "utf8"));
  for (const task of doc.tasks ?? []) {
    for (const val of extractPredicateValues(task.predicate)) {
      const s = String(val).toLowerCase();
      if (s.length >= 5 && !["webmcp", "empty", "online"].includes(s)) {
        armEntities.add(s);
      }
    }
    for (const matchStr of extractPredicateMatches(task.predicate)) {
      const words = String(matchStr).match(/[a-zA-Z]{5,}/g) || [];
      for (const w of words) {
        const s = w.toLowerCase();
        if (["github", "react", "figma", "dribbble"].includes(s)) {
          armEntities.add(s);
        }
      }
    }
  }
}

const armsDir = path.join(root, "arms");
const armFiles = fs.readdirSync(armsDir).filter((f) => f.endsWith(".mjs") && f !== "scripted.mjs");
for (const armFile of armFiles) {
  const content = fs.readFileSync(path.join(armsDir, armFile), "utf8");
  const lines = content.split("\n");
  for (let idx = 0; idx < lines.length; idx++) {
    const line = lines[idx];
    if (/description\s*:\s*["\x27\x60]/.test(line) || /system\s*:\s*["\x27\x60]/.test(line) || /initialPrompt\s*=/.test(line)) {
      for (const entity of armEntities) {
        const wordRegex = new RegExp(`\\b${entity}\\b`, "i");
        if (wordRegex.test(line) && !allow.has(`${armFile}:${entity}`)) {
          hits.push(`arms/${armFile}:${idx + 1}: leaks entity "${entity}": ${line.trim()}`);
        }
      }
    }
  }
}

if (hits.length) { console.error(`golden answer leakage:\n${hits.join("\n")}`); process.exitCode = 1; }

