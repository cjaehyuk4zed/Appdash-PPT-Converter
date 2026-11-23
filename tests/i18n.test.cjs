const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/i18n.js'), 'utf8');
function load(saved = null, blocked = false) {
  const storage = { value: saved };
  const context = vm.createContext({ localStorage: {
    getItem() { if (blocked) throw Error('Storage unavailable'); return storage.value; },
    setItem(key, value) { if (blocked) throw Error('Storage unavailable'); storage.value = value; },
  } });
  vm.runInContext(source, context);
  return { ui: vm.runInContext('i18n', context), storage };
}

test('Korean is the default regardless of an invalid saved language', () => {
  for (const saved of [null, 'fr', '', 'ko']) {
    const { ui } = load(saved);
    assert.equal(ui.language, 'ko');
    assert.equal(ui.t('Convert'), '이미지로 변환');
  }
});

test('English selection persists and can be changed back to Korean', () => {
  const { ui, storage } = load('en');
  assert.equal(ui.t('Convert'), 'Convert');
  ui.setLanguage('ko');
  assert.equal(storage.value, 'ko');
  assert.equal(load(storage.value).ui.language, 'ko');
  ui.setLanguage('en');
  assert.equal(load(storage.value).ui.language, 'en');
});

test('blocked browser storage still allows language switching', () => {
  const { ui } = load(null, true);
  assert.equal(ui.language, 'ko');
  ui.setLanguage('en');
  assert.equal(ui.t('Image converter'), 'Image converter');
});

test('dynamic progress, errors, and parameters use the selected language', () => {
  const { ui } = load();
  assert.equal(ui.message('Converting output 2 of 7'), '파일 변환 중 · 2 / 7');
  assert.equal(ui.message('Page range must be between 1 and 12.'), '페이지 범위는 1~12 사이로 입력해 주세요.');
  assert.equal(ui.t('Remove {name}', { name: '원본 {file}.png' }), '원본 {file}.png 삭제');
  assert.equal(ui.message('Cannot open PPT. Check the file and its password.'), 'PPT를 열 수 없습니다. 파일과 암호를 확인해 주세요.');
  assert.equal(ui.message('Unexpected native codec error'), 'Unexpected native codec error');
  ui.setLanguage('en');
  assert.equal(ui.message('Converting output 2 of 7'), 'Converting output 2 of 7');
});

test('every HTML translation key has a Korean translation', () => {
  const { ui } = load();
  const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
  for (const match of html.matchAll(/data-i18n(?:-aria-label|-placeholder|-alt)?="([^"]+)"/g)) {
    const key = match[1].replaceAll('&amp;', '&');
    assert.notEqual(ui.t(key), key, `Missing Korean translation: ${key}`);
  }
  assert.equal(ui.message('Assembling slide 2 of 3'), '슬라이드 구성 중 · 2 / 3');
});
