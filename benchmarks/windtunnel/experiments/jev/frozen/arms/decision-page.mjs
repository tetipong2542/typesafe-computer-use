export const EMPTY_SCHEMA = { type: "object", properties: {}, additionalProperties: false };
const field = (name, schema) => ({ type: "object", properties: { [name]: schema }, required: [name], additionalProperties: false });
const SEMANTIC_SELECTOR = 'a[href],button,input:not([type="hidden"]):not([type="file"]),textarea,select,[role="button"],[role="link"],[role="textbox"],[role="checkbox"],[role="radio"],[role="combobox"],[role="option"],[role="menuitem"],[contenteditable="true"]';
const SELECTOR = `${SEMANTIC_SELECTOR},[aria-selected],[tabindex]`;
export const PAGE_SETTLE = Object.freeze({ networkIdleTimeoutMs: 5000, domQuietMs: 300, domTimeoutMs: 2000 });
const signatureOf = el => ({ tag: el.tagName, type: el.getAttribute('type'), role: el.getAttribute('role'),
  label: el.getAttribute('aria-label'), text: el.textContent?.slice(0, 2000), href: el.getAttribute('href'),
  value: el.value ?? (el.isContentEditable ? el.innerText : null),
  state: {
    checked: ['checkbox', 'radio'].includes(el.type) ? el.checked : el.getAttribute('aria-checked'),
    selected: el.getAttribute('aria-selected') ?? (el.tagName === 'OPTION' ? el.selected : null),
    expanded: el.getAttribute('aria-expanded'), current: el.getAttribute('aria-current'),
    selectedIndex: el.tagName === 'SELECT' ? el.selectedIndex : null,
  } });

export async function observePage(page) {
  for (let attempt = 0; ; attempt++) {
    try {
      const snapshot = await pageSnapshot(page);
      snapshot.observation.navigationRetries = attempt;
      return snapshot;
    } catch (error) {
      // Navigation can replace the document between collecting handles and reading text.
      // Retry only this read, never the preceding click/submit.
      if (attempt || !/Execution context was destroyed|Cannot find context/i.test(error.message)) throw error;
      await page.waitForLoadState('domcontentloaded', { timeout: 10000 });
    }
  }
}

async function pageSnapshot(page) {
  const handles = await page.locator(SELECTOR).elementHandles();
  const actions = [], controls = [];
  let omitted = 0;
  for (const element of handles) {
    const info = await element.evaluate((el, { semanticSelector, selector }) => {
      const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
      if (!el.isConnected || !rect.width || !rect.height || rect.bottom <= 0 || rect.right <= 0 || rect.top >= innerHeight || rect.left >= innerWidth ||
        style.visibility === "hidden" || style.display === "none" || el.closest('[aria-hidden="true"],[inert]') || el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true') return null;
      // Generic focus/selection attributes also occur on wrappers. Offer their
      // actual controls, not a centre click that can hit an arbitrary child.
      if (!el.matches(semanticSelector) && el.querySelector(selector)) return null;
      const tag = el.tagName.toLowerCase(), type = el.getAttribute('type') ?? '';
      if (tag === 'input' && ['file', 'hidden'].includes(type)) return null;
      const labelled = (el.getAttribute('aria-labelledby') ?? '').split(/\s+/).map(id => document.getElementById(id)?.textContent ?? '').join(' ').trim();
      const name = el.getAttribute('aria-label') || labelled || [...(el.labels ?? [])].map(l => l.innerText).join(' ') || el.innerText || el.getAttribute('alt') || el.getAttribute('placeholder') || el.getAttribute('title') || '';
      const editable = !el.readOnly && (el.isContentEditable || tag === 'textarea' || tag === 'input' && !['button', 'submit', 'reset', 'checkbox', 'radio', 'range', 'color', 'image'].includes(type));
      return { tag, type, role: el.getAttribute('role') || tag, name: name.trim().slice(0, 240), editable,
        value: type === 'password' ? (el.value ? '[filled]' : '') : String(el.value ?? '').slice(0, 240),
        ...(tag === 'select' ? { options: [...el.options].filter(o => !o.disabled).map(o => ({ value: o.value, label: o.label })) } : {}) };
    }, { semanticSelector: SEMANTIC_SELECTOR, selector: SELECTOR }).catch(() => null);
    if (!info) continue;
    if (controls.length >= 80) { omitted++; continue; }
    const target = `e${controls.length + 1}`;
    const signature = await element.evaluate(signatureOf);
    controls.push({ id: target, ...info,
      ...Object.fromEntries(Object.entries(signature.state).filter(([, value]) => value !== null)),
      options: info.options?.slice(0, 100), optionsOmitted: Math.max(0, (info.options?.length ?? 0) - 100) });
    const add = (kind, inputSchema = EMPTY_SCHEMA, extra = {}) => actions.push({ id: `${kind}:${target}`, kind, target, element, signature,
      description: `${kind} ${target}: ${info.role} ${JSON.stringify(info.name)}`, inputSchema, ...extra });
    if (info.tag === 'select') {
      const values = info.options.slice(0, 100).map(o => o.value);
      if (values.length) add('select', field('value', { type: 'string', enum: values }));
    } else if (info.editable) {
      add('fill', field('value', { type: 'string', maxLength: 4000 }));
      add('press', EMPTY_SCHEMA, { key: 'Enter', id: `enter:${target}`, description: `Press Enter in ${target}: ${JSON.stringify(info.name)}` });
    } else add('click');
  }
  for (const direction of ['up', 'down']) actions.push({ id: `scroll:${direction}`, kind: 'scroll', direction,
    description: `Scroll the page ${direction} one viewport`, inputSchema: EMPTY_SCHEMA });
  actions.push({ id: 'back', kind: 'back', description: 'Go back one page in browser history', inputSchema: EMPTY_SCHEMA });
  let text = await page.locator('body').ariaSnapshot();
  const textOmitted = Math.max(0, text.length - 16000);
  text = text.slice(0, 16000);
  const observation = { url: page.url(), text, controls, truncated: { controls: omitted, textCharacters: textOmitted },
    scope: 'visible page text and viewport controls; no frames, screenshots or WebMCP' };
  // Keep complete control/schema pairs. Truncating a select's enum independently could
  // leave a displayed option that the model cannot actually execute.
  while (controls.length && JSON.stringify({ observation, actions: actions.map(({ id, description, inputSchema }) => ({ id, description, inputSchema })) }).length > 40000) {
    const { id } = controls.pop();
    for (let i = actions.length - 1; i >= 0; i--) if (actions[i].target === id) actions.splice(i, 1);
    observation.truncated.controls++;
  }
  return { observation, actions,
    release: () => Promise.all(handles.map(h => h.dispose().catch(() => {}))) };
}

async function settlePage(page) {
  const settled = { network: 'idle', dom: 'quiet' };
  try { await page.waitForLoadState('networkidle', { timeout: PAGE_SETTLE.networkIdleTimeoutMs }); }
  catch (error) { if (error.name !== 'TimeoutError') throw error; settled.network = 'pending'; }
  try {
    // Observe rendered-document mutations, not application internals. The cap keeps
    // polling widgets/animations from consuming the whole attempt.
    settled.dom = await page.evaluate(({ domQuietMs, domTimeoutMs }) => new Promise(resolve => {
      let quietTimer;
      const finish = status => { clearTimeout(quietTimer); clearTimeout(deadline); observer.disconnect(); resolve(status); };
      const changed = () => { clearTimeout(quietTimer); quietTimer = setTimeout(() => finish('quiet'), domQuietMs); };
      const observer = new MutationObserver(changed);
      const deadline = setTimeout(() => finish('pending'), domTimeoutMs);
      observer.observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
      changed();
    }), PAGE_SETTLE);
  } catch (error) {
    if (!/Execution context was destroyed|Cannot find context/i.test(error.message)) throw error;
    settled.dom = 'pending'; // observePage retries document replacement; never replay the action.
  }
  return settled;
}

export async function executePageAction(page, action, args) {
  const options = { timeout: 5000 };
  let actionError = '', loadState = 'ready';
  if (action.element) {
    if (!await action.element.evaluate(el => el.isConnected) || !await action.element.isVisible() || !await action.element.isEnabled()) throw new Error('observed target is stale, hidden or disabled');
    const current = await action.element.evaluate(signatureOf);
    if (JSON.stringify(current) !== JSON.stringify(action.signature)) throw new Error('observed target changed; refresh page observation');
  }
  try { switch (action.kind) {
    case 'click': await action.element.click(options); break;
    case 'fill': await action.element.fill(args.value, options); break;
    case 'select': await action.element.selectOption(args.value, options); break;
    case 'press': await action.element.press(action.key, options); break;
    case 'scroll': await page.mouse.wheel(0, action.direction === 'down' ? 650 : -650); break;
    case 'back': await page.goBack({ ...options, waitUntil: 'domcontentloaded' }); break;
    default: throw new Error(`unsupported page action: ${action.kind}`);
  } } catch (error) {
    if (error.name !== 'TimeoutError') throw error;
    actionError = error.message; // A failed step is evidence for the next decision, not an attempt deadline.
  }
  if (action.kind === 'click' || action.kind === 'press' || actionError) {
    try {
      await page.waitForLoadState('domcontentloaded', { timeout: 5000 });
    } catch (error) { if (error.name !== 'TimeoutError') throw error; loadState = 'pending'; }
  }
  const settled = await settlePage(page);
  if (settled.network === 'pending' || settled.dom === 'pending') loadState = 'pending';
  const readback = {};
  if (action.kind === 'fill') {
    try {
      readback.valueAfter = await action.element.evaluate(el => {
        if (!el.isConnected) throw new Error('filled target detached before read-back');
        return el.isContentEditable ? el.innerText : el.value;
      });
    } catch (error) { readback.valueAfterError = error.message; }
  }
  return { action: action.id, ok: !actionError, loadState, settled, ...(actionError ? { error: actionError } : {}), ...readback, url: page.url() };
}
