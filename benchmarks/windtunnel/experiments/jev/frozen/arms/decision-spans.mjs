import { assertArgs, validateArgumentSchema } from "./decision-providers.mjs";

export function collectCandidates(prompt = "", observations = []) {
  const found = new Map();
  let omitted = 0;
  const add = (value, source) => {
    if (typeof value === "string" && (!value.trim() || value.length > 2000)) { omitted++; return; }
    if (!["string", "number", "boolean"].includes(typeof value) && value !== null) return;
    const key = JSON.stringify(value);
    if (found.has(key)) {
      if (source.startsWith('observation') && !found.get(key).source.startsWith('observation')) {
        found.set(key, { value, source, label: `${source}: ${key}` });
      }
      return;
    }
    if (found.size >= 2000) { omitted++; return; }
    found.set(key, { value, source, label: `${source}: ${key}` });
  };
  const walk = (value, source, depth = 0) => {
    if (depth > 16) { omitted++; return; }
    if (typeof value === "string") {
      try {
        const parsed = JSON.parse(value);
        if (parsed && typeof parsed === "object") { walk(parsed, source, depth + 1); return; }
      } catch { /* ordinary observed text */ }
      add(value, source);
    } else if (value && typeof value === "object") {
      for (const [key, child] of Object.entries(value)) walk(child, `${source}.${key}`, depth + 1);
    } else add(value, source);
  };
  for (const match of prompt.matchAll(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|["“]([^"”]+)["”]|'([^'\n]+)'|\b\d{4}-\d{2}-\d{2}\b|\b[A-Z][A-Z_]{1,}\b|[+-]?\d+(?:\.\d+)?/g)) {
    const value = match[1] ?? match[2] ?? match[0];
    add(value, `task[${match.index}]`);
    if (/^[+-]?\d+(?:\.\d+)?$/.test(value)) add(Number(value), `task[${match.index}]`);
  }
  // Most recent results first; the same deterministic ordering applies to every task.
  observations.slice().reverse().forEach((o, i) => walk(o, `observation[-${i + 1}]`));
  if (prompt) add(prompt, "task.full");
  // ponytail: contiguous spans up to six words, not linguistic phrase extraction.
  // Longer newly composed strings require the hybrid filler.
  const words = [...prompt.matchAll(/\S+/g)];
  for (let size = 1; size <= 6; size++) for (let i = 0; i + size <= words.length; i++) {
    const end = words[i + size - 1];
    add(prompt.slice(words[i].index, end.index + end[0].length).replace(/^["'“”]|["'“”,.;!?]$/g, ""), `task.span[${i}:${i + size}]`);
  }
  return { candidates: [...found.values()], omitted };
}

export async function fillFromSpans(schema, state, choose, audit = () => {}) {
  validateArgumentSchema(schema);
  const pool = collectCandidates(state.task, state.history);
  audit({ kind: "candidate_pool", count: pool.candidates.length, omitted: pool.omitted });
  let fields = 0, pending = [], args;
  const omit = { value: undefined, label: "Omit this optional argument" };
  const enqueue = (question, options, accept, required) => pending.push({ question,
    options: required ? options : [omit, ...options], accept });
  function fill(node, path, accept, required = true, depth = 0) {
    if (++fields > 64 || depth > 6) throw new Error("span schema limit: 64 fields / 6 levels");
    if (node.$ref || node.oneOf || node.anyOf || node.allOf || node.if || node.patternProperties) throw new Error(`span schema unsupported at ${path}`);
    if (node.type === "object" || node.properties) {
      const children = () => {
        const object = {};
        accept(object);
        for (const [key, child] of Object.entries(node.properties ?? {})) {
          fill(child, `${path}.${key}`, value => {
            if (value !== undefined) Object.defineProperty(object, key, { value, enumerable: true, writable: true, configurable: true });
          }, Boolean(node.required?.includes(key)), depth + 1);
        }
      };
      // Optional containers gate their children; leaf omission shares the value menu.
      if (required) children();
      else enqueue(`Supply object ${path} or omit it? ${node.description ?? ""}`, [{ value: true, label: "Supply this object" }],
        value => { if (value !== undefined) children(); }, false);
      return;
    }
    if (node.type === "array") {
      if (!node.items || Array.isArray(node.items)) throw new Error(`span schema requires homogeneous array at ${path}`);
      const min = node.minItems ?? 0, max = Math.min(node.maxItems ?? 4, 8);
      if (min > max) throw new Error(`span array minimum exceeds limit at ${path}`);
      enqueue(`Choose array length for ${path}`, Array.from({ length: max - min + 1 }, (_, i) => ({ value: i + min })), length => {
        if (length === undefined) return;
        const items = [];
        accept(items);
        for (let i = 0; i < length; i++) fill(node.items, `${path}[${i}]`, value => { items[i] = value; }, true, depth + 1);
      }, required);
      return;
    }
    let options;
    if (Object.hasOwn(node, "const")) options = [{ value: node.const }];
    else if (node.enum) options = node.enum.map(value => ({ value }));
    else {
      options = [...pool.candidates];
      if (node.type === "boolean") options = [false, true].map(value => ({ value }));
      if (["integer", "number"].includes(node.type)) options.push(...Array.from({ length: 21 }, (_, value) => ({ value, source: 'bounded integer menu' })));
      if (node.type === "null") options = [{ value: null }];
      if (Object.hasOwn(node, "default")) options.unshift({ value: node.default, source: "schema default" });
    }
    const validate = validateArgumentSchema(node), unique = new Map();
    for (const option of options) if (validate(option.value)) unique.set(JSON.stringify(option.value), option);
    const collapsed = options.filter(o => validate(o.value)).length - unique.size;
    options = [...unique.values()];
    const cap = required ? 255 : 254;
    audit({ kind: "candidate_field", path, count: options.length, collapsed, omitted: Math.max(0, options.length - cap) });
    if (!options.length) {
      if (!required) { audit({ kind: 'argument_omitted', path, reason: 'no representable candidate' }); return; }
      throw new Error(`no representable candidate for required argument ${path}`);
    }
    enqueue(`Choose argument ${path}. ${node.description ?? ""} Preserve the requested item index for arrays.`, options.slice(0, cap), accept, required);
  }
  fill(schema, "$args", value => { args = value; });
  while (pending.length) {
    const batch = pending;
    pending = [];
    const answers = await choose(state, Object.fromEntries(batch.map(({ question, options }, i) => [`field${i}`, { question, options }])));
    // Resolving lengths/presence queues dependent fields for the next request.
    batch.forEach(({ accept }, i) => accept(answers[`field${i}`].value));
  }
  assertArgs(schema, args);
  return args;
}

export async function answerFromSpans(state, choose, audit = () => {}) {
  const { candidates, omitted } = collectCandidates(state.task, state.history.map(h => h.result ?? h));
  const values = candidates.filter(c => typeof c.value === "string" || typeof c.value === "number");
  const remaining = values.slice(0, 254); // Reserve one choice for stop.
  audit({ kind: "answer_candidates", count: remaining.length, omitted: omitted + Math.max(0, values.length - remaining.length) });
  const questions = Object.fromEntries(Array.from({ length: 8 }, (_, i) => [`slot${i}`, {
    question: `Select answer evidence for slot ${i + 1} of 8, in task order. Choose a distinct relevant value for each slot, or stop when no more evidence is needed.`,
    options: [{ value: null, label: "Stop selecting answer evidence" }, ...remaining],
  }]));
  const answers = await choose(state, questions), selected = [];
  for (const id of Object.keys(questions)) {
    const value = answers[id].value;
    if (value === null) break;
    if (!selected.includes(value)) selected.push(value);
  }
  if (!selected.length) throw new Error("no answer evidence selected");
  return selected.join("; ").slice(0, 3900);
}
