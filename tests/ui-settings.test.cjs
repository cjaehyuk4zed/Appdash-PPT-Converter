const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function load() {
  const values = { format: 'jpeg', preset: 'balanced', color: 'original', quality: '85', dpi: '200', 'min-dpi': '100', 'min-edge': '1024', 'target-size': '1', 'target-unit': '1000' };
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, { value: values[id] ?? '', checked: false, hidden: false, options: [], dataset: {}, addEventListener() {}, replaceChildren() {} });
    return elements.get(id);
  };
  const translate = (value, values = {}) => value.replace(/\{(\w+)\}/g, (match, name) => String(values[name] ?? match));
  const context = vm.createContext({
    document: { getElementById: get, querySelectorAll: () => [] },
    window: { addEventListener() {} },
    i18n: { apply() {}, t: translate, message: value => value, language: 'ko' },
    t: translate,
    fetch: () => new Promise(() => {}),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), context);
  return { get, run: code => vm.runInContext(code, context) };
}

test('size labels follow the output type and image tools default to 1 MB', () => {
  const { get, run } = load();
  for (const tool of ['ppt_to_images', 'image_convert', 'images_to_ppt', 'ppt_compress']) {
    run(`switchTool('${tool}')`);
    const pdf = ['images_to_ppt', 'ppt_compress'].includes(tool);
    assert.equal(get('target-size-label').textContent, pdf ? 'Maximum size per PPTX file' : 'Maximum size per image');
    assert.equal(get('target-size').value, pdf ? '10' : '1');
    assert.equal(get('target-unit').value, '1000');
  }
});

test('size mode searches the full quality range and retains dimension settings', () => {
  const { get, run } = load();
  get('quality').value = '25'; get('preset').value = 'lossless'; get('lossless').checked = true;
  get('max-width').value = '1200'; get('max-height').value = '900';
  run("state.mode = 'target'; updateSettings()");
  const options = run('readOptions()');
  assert.equal(options.quality, 95); assert.equal(options.min_quality, 1); assert.equal(options.lossless, false);
  assert.equal(options.target_kb, 1000); assert.equal(options.dpi, 200);
  assert.equal(options.max_width, 1200); assert.equal(options.max_height, 900);
  assert.equal(get('quality-field').hidden, true); assert.equal(get('preset-field').hidden, true);
  assert.equal(get('lossless-field').hidden, true); assert.equal(get('dpi-field').hidden, false);
  assert.equal(get('raster-settings').hidden, false);
});

test('returning to quality mode keeps the previous manual quality and lossless choice', () => {
  const { get, run } = load();
  run("switchTool('image_convert')"); get('format').value = 'webp'; get('quality').value = '40'; get('lossless').checked = true;
  run("state.mode = 'target'; updateSettings(); state.mode = 'quality'; updateSettings()");
  const options = run('readOptions()');
  assert.equal(options.quality, 95); assert.equal(options.lossless, true);
  assert.equal(get('quality').value, '100'); assert.equal(get('quality').disabled, true);
  assert.equal(options.target_kb, null); assert.equal(get('preset-field').hidden, false); assert.equal(get('lossless-field').hidden, false);
  get('lossless').checked = false; run('updateSettings()');
  assert.equal(run('readOptions()').quality, 40);
});

test('quality sliders stay visible for lossless image formats and restore manual quality', () => {
  for (const tool of ['ppt_to_images', 'image_convert']) {
    const { get, run } = load(); run(`switchTool('${tool}')`);
    get('quality').value = '70'; run('updateSettings()');
    for (const format of ['png', 'tiff', 'bmp']) {
      get('format').value = format; run('updateSettings()');
      assert.equal(get('quality-field').hidden, false);
      assert.equal(get('quality').value, '100'); assert.equal(get('quality').disabled, true);
      assert.equal(get('quality-value').value, '100'); assert.equal(run('readOptions()').quality, 95);
    }
    get('format').value = 'jpeg'; run('updateSettings()');
    assert.equal(get('quality-field').hidden, false); assert.equal(get('quality').disabled, false);
    assert.equal(run('readOptions()').quality, 70);
    run("state.mode = 'target'; updateSettings()");
    assert.equal(get('quality-field').hidden, true); assert.equal(run('readOptions()').quality, 95);
  }
});

test('lossless preset shows 100 and changing to a lossy preset applies its quality', () => {
  const { get, run } = load();
  get('preset').value = 'lossless'; run("$('preset').onchange()");
  assert.equal(get('format').value, 'png'); assert.equal(get('quality').value, '100');
  assert.equal(get('quality-field').hidden, false);
  get('format').value = 'jpeg'; get('preset').value = 'print'; run("$('preset').onchange()");
  assert.equal(get('quality').disabled, false); assert.equal(run('readOptions()').quality, 95);
});

test('size summary follows settings while folding preserves conversion options', () => {
  const { get, run } = load();
  run('updateSettings()');
  assert.equal(get('size-summary').textContent, '200 DPI · Original size');
  get('dpi').value = '150'; get('max-width').value = '1200';
  run('updateSettings()');
  assert.equal(get('size-summary').textContent, '150 DPI · Width ≤ 1200 px');
  const before = JSON.stringify(run('readOptions()'));
  get('size-options').open = true;
  get('size-options').open = false;
  assert.equal(JSON.stringify(run('readOptions()')), before);
  run("switchTool('image_convert')");
  assert.equal(get('size-summary').textContent, 'Width ≤ 1200 px');
  get('max-height').value = '800'; run('updateSettings()');
  assert.equal(get('size-summary').textContent, '≤ 1200 × 800 px');
});
