'use strict';
const $ = id => document.getElementById(id);
const state = { tool: 'ppt_to_images', mode: 'quality', files: [], pages: [], currentJob: null, resultJob: null, busy: false, uploading: false, capabilities: null, notice: '', uploadCount: 0, jobStatus: '', jobPreview: false, connection: 'Connecting…', lossyQuality: 85 };
const tools = {
  ppt_to_images: { name: 'PPT to images', action: 'Convert', pdf: true },
  images_to_ppt: { name: 'Images to PPT', action: 'Create PPTX', pdf: false },
  image_convert: { name: 'Image converter', action: 'Convert images', pdf: false },
  ppt_compress: { name: 'Compress PPT', action: 'Compress PPT', pdf: true },
};
function node(tag, className, text) { const value = document.createElement(tag); if (className) value.className = className; if (text !== undefined) value.textContent = text; return value; }
function show(id, visible) { $(id).hidden = !visible; }
function notice(message) { state.notice = message; const text = typeof message === 'function' ? message() : i18n.message(message); $('notice').textContent = text; show('notice', Boolean(text)); }
function pageCount(count) { const unit = tools[state.tool].pdf || pdfOutput() ? 'slide' : 'page'; return t(count === 1 ? '{count} ' + unit : '{count} ' + unit + 's', { count }); }
function fileCount(count) { return t(count === 1 ? '{count} file' : '{count} files', { count }); }
function bytes(value) { return value >= 1e6 ? `${(value / 1e6).toFixed(2)} MB` : `${(value / 1000).toFixed(value < 10000 ? 1 : 0)} KB`; }
function currentFiles() { return state.files.filter(file => (file.kind === 'ppt') === tools[state.tool].pdf); }
function currentPages() { const ids = new Set(currentFiles().map(file => file.id)); return state.pages.filter(page => ids.has(page.file_id)); }
function selectedPages() { return currentPages().filter(page => page.selected); }
function pdfOutput() { return ['images_to_ppt', 'ppt_compress'].includes(state.tool); }
function preserveMode() { return state.tool === 'ppt_compress' && $('compression').value === 'preserve'; }
function isLossy() { return pdfOutput() ? (state.mode === 'target' || $('preset').value !== 'lossless') && $('color').value !== 'bw' : ['jpeg', 'webp', 'avif'].includes($('format').value) && !(state.mode !== 'target' && $('format').value === 'webp' && $('lossless').checked); }
async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    try { const error = await response.json(); message = Array.isArray(error.detail) ? error.detail.map(value => value.msg).join('\n') : error.detail || message; } catch {}
    throw new Error(message);
  }
  return response.json();
}
function setBusy(busy) {
  state.busy = busy;
  $('convert').disabled = busy || state.uploading || !selectedPages().length || !state.capabilities;
  $('preview-button').disabled = $('convert').disabled;
  $('clear-files').disabled = busy || state.uploading || !currentFiles().length;
  $('file-input').disabled = busy || state.uploading;
  document.querySelectorAll('.remove-file').forEach(button => button.disabled = busy);
  document.querySelectorAll('.page-item button, .page-item input, #select-all, #select-none').forEach(control => control.disabled = busy || state.uploading);
}
function updateSettings() {
  const pdf = pdfOutput(), preserve = preserveMode(), sourcePdf = tools[state.tool].pdf, target = state.mode === 'target';
  show('renderer-note', sourcePdf);
  show('format-field', !pdf);
  show('compression-field', state.tool === 'ppt_compress');
  show('preserve-note', preserve);
  show('raster-note', state.tool === 'ppt_compress' && !preserve);
  show('split-field', pdf);
  show('target-fields', target);
  $('target-size-label').textContent = t(pdf ? 'Maximum size per PPTX file' : 'Maximum size per image');
  show('preset-field', !target);
  show('raster-settings', !preserve);
  show('transform-fields', !preserve);
  show('quality-field', !target && !preserve);
  const quality = $('quality');
  if (!isLossy()) {
    if (!quality.disabled) state.lossyQuality = Number(quality.value);
    quality.max = '100'; quality.value = '100'; quality.disabled = true;
  } else {
    quality.max = '95';
    if (quality.disabled) quality.value = String(state.lossyQuality);
    quality.disabled = false; state.lossyQuality = Number(quality.value);
  }
  show('lossless-field', !target && !pdf && $('format').value === 'webp');
  show('dpi-field', sourcePdf && !preserve);
  show('pages-field', sourcePdf);
  show('pdf-layout-fields', state.tool === 'images_to_ppt');
  show('target-advanced', target && !preserve);
  show('min-dpi-field', sourcePdf);
  show('min-edge-field', !sourcePdf);
  show('page-budget-field', pdf && !preserve);
  show('document-metadata-field', pdf);
  show('image-metadata-field', !pdf);
  show('threshold-field', $('color').value === 'bw');
  show('background-field', pdf || ['jpeg', 'bmp'].includes($('format').value));
  $('metadata-label').textContent = t(preserve ? 'Remove PPTX document metadata' : 'Remove image EXIF / GPS metadata');
  $('quality-value').value = $('quality').value;
  const width = $('max-width').value, height = $('max-height').value;
  const dimensions = width && height ? `≤ ${width} × ${height} px` : width ? t('Width ≤ {width} px', { width }) : height ? t('Height ≤ {height} px', { height }) : t('Original size');
  $('size-summary').textContent = sourcePdf && !preserve ? `${$('dpi').value} DPI · ${dimensions}` : dimensions;
  const selected = selectedPages().length;
  $('conversion-summary').textContent = selected ? t('{pages} selected · {mode}', { pages: pageCount(selected), mode: t(target ? 'size target checked on the final file' : 'your quality, your choice') }) : t('Add a file to get started.');
  setBusy(state.busy);
}
function renderFiles() {
  $('file-count').textContent = currentFiles().length;
  renderPages(); updateSettings();
}
function renderPages(focusPage = null) {
  const pages = currentPages(), list = $('file-list'), scrollTop = list.scrollTop;
  list.replaceChildren();
  let focusHandle;
  show('order-section', pages.length > 0);
  $('selection-count').textContent = `${selectedPages().length} / ${pages.length}`;
  pages.forEach((page, index) => {
    const file = state.files.find(file => file.id === page.file_id), row = node('div', 'file-card page-item'), checkbox = node('input');
    row.classList.toggle('unselected', !page.selected);
    row.setAttribute('role', 'listitem');
    const handle = node('button', 'drag-handle', '⠿'); handle.type = 'button';
    handle.setAttribute('aria-label', t('Drag to reorder: {name}, page {page}', { name: file.name, page: page.page + 1 }));
    handle.setAttribute('aria-describedby', 'order-hint');
    handle.title = handle.getAttribute('aria-label');
    handle.onpointerdown = event => startPageDrag(event, page, row, handle);
    handle.onkeydown = event => {
      if (!['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault(); movePage(page, pages[index + (event.key === 'ArrowUp' ? -1 : 1)]);
    };
    if (page === focusPage) focusHandle = handle;
    checkbox.type = 'checkbox'; checkbox.checked = page.selected; checkbox.setAttribute('aria-label', t('Select {name}, page {page}', { name: file.name, page: page.page + 1 }));
    checkbox.onchange = () => { page.selected = checkbox.checked; row.classList.toggle('unselected', !page.selected); updateSettings(); $('selection-count').textContent = `${selectedPages().length} / ${pages.length}`; };
    const thumbnail = node('img', 'file-thumb'), info = node('div', 'file-info'), actions = node('div', 'page-actions');
    thumbnail.src = `/api/files/${file.id}/thumbnail?page=${page.page}`;
    thumbnail.alt = t('{name} · p{page}', { name: file.name, page: page.page + 1 }); thumbnail.loading = 'lazy'; thumbnail.draggable = false;
    thumbnail.style.transform = `rotate(${page.rotation}deg)`;
    const name = file.pages > 1 ? t('{name} · p{page}', { name: file.name, page: page.page + 1 }) : file.name;
    const dimensions = file.page_sizes?.[page.page] || [file.width, file.height];
    const title = node('div', 'file-name', name); title.title = name;
    info.append(title, node('div', 'file-meta', `${file.format} · ${dimensions[0]} × ${dimensions[1]} ${file.unit} · ${pageCount(file.pages)} · ${bytes(file.size)}` + (page.rotation ? ` · ${page.rotation}°` : '')));
    for (const [text, label, action] of [
      ['↻', 'Rotate clockwise', () => { page.rotation = (page.rotation + 90) % 360; renderPages(); }],
      ['↑', 'Move earlier', () => movePage(page, pages[index - 1])],
      ['↓', 'Move later', () => movePage(page, pages[index + 1])],
    ]) { const button = node('button', '', text); button.type = 'button'; button.setAttribute('aria-label', `${t(label)}: ${t('Select {name}, page {page}', { name: file.name, page: page.page + 1 })}`); button.title = button.getAttribute('aria-label'); button.onclick = action; actions.append(button); }
    const remove = node('button', 'remove-file', '×'); remove.type = 'button'; remove.title = t('Remove {name}', { name: file.name }); remove.setAttribute('aria-label', remove.title);
    remove.onclick = () => removeFile(file.id);
    actions.append(remove);
    row.append(handle, checkbox, thumbnail, info, actions);
    list.append(row);
  });
  list.scrollTop = scrollTop;
  if (focusHandle) focusHandle.focus({ preventScroll: true });
  setBusy(state.busy);
}
function reorderPage(page, target, after = false) {
  const pages = currentPages();
  if (state.busy || state.uploading || page === target || !pages.includes(page) || !pages.includes(target)) return;
  state.pages.splice(state.pages.indexOf(page), 1);
  state.pages.splice(state.pages.indexOf(target) + (after ? 1 : 0), 0, page);
  renderPages(page); updateSettings();
}
function movePage(page, neighbor) {
  const pages = currentPages();
  reorderPage(page, neighbor, pages.indexOf(page) < pages.indexOf(neighbor));
}
function startPageDrag(event, page, row, handle) {
  if (event.button !== 0 || state.busy || state.uploading || currentPages().length < 2) return;
  const list = $('file-list'), pointer = event.pointerId, rows = [...list.children], pages = currentPages();
  let active = false, target = null, after = false, frame = 0, x = event.clientX, y = event.clientY;
  const clearTarget = () => { if (target) target.classList.remove('drop-before', 'drop-after'); target = null; };
  const locateTarget = () => {
    clearTarget();
    const candidate = document.elementFromPoint(x, y)?.closest('.page-item');
    if (!candidate || candidate === row || candidate.parentElement !== list) return;
    target = candidate;
    const bounds = target.getBoundingClientRect();
    after = y >= bounds.top + bounds.height / 2;
    target.classList.add(after ? 'drop-after' : 'drop-before');
  };
  const scroll = () => {
    const bounds = list.getBoundingClientRect();
    if (x >= bounds.left && x <= bounds.right && y >= bounds.top && y <= bounds.bottom) {
      const step = y < bounds.top + 28 ? -8 : y > bounds.bottom - 28 ? 8 : 0;
      if (step) { list.scrollTop += step; locateTarget(); }
    }
    frame = requestAnimationFrame(scroll);
  };
  const move = next => {
    if (next.pointerId !== pointer) return;
    x = next.clientX; y = next.clientY;
    if (!active && Math.hypot(x - event.clientX, y - event.clientY) < 5) return;
    next.preventDefault();
    if (!active) { active = true; row.classList.add('dragging'); frame = requestAnimationFrame(scroll); }
    locateTarget();
  };
  const finish = next => {
    if (next.pointerId !== pointer) return;
    const destination = target ? pages[rows.indexOf(target)] : null;
    handle.removeEventListener('pointermove', move);
    for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) handle.removeEventListener(type, finish);
    cancelAnimationFrame(frame); row.classList.remove('dragging'); clearTarget();
    if (handle.hasPointerCapture(pointer)) handle.releasePointerCapture(pointer);
    if (active && next.type === 'pointerup' && destination) reorderPage(page, destination, after);
  };
  handle.setPointerCapture(pointer);
  handle.addEventListener('pointermove', move);
  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) handle.addEventListener(type, finish);
}
async function removeFile(id) {
  try { await api(`/api/files/${id}`, { method: 'DELETE' }); state.files = state.files.filter(file => file.id !== id); state.pages = state.pages.filter(page => page.file_id !== id); renderFiles(); } catch (error) { notice(error.message); }
}
async function uploadFiles(files) {
  if (state.busy || state.uploading || !files.length) return;
  notice(''); state.uploading = true; setBusy(false);
  state.uploadCount = files.length; $('upload-state').textContent = t('Inspecting {count} files…', { count: state.uploadCount });
  const form = new FormData(); Array.from(files).forEach(file => form.append('files', file)); 
  try {
    const data = await api('/api/files', { method: 'POST', body: form });
    const warnings = [];
    for (const file of data.files) {
      state.files.push(file);
      for (let page = 0; page < file.pages; page++) state.pages.push({ file_id: file.id, page, rotation: 0, selected: true });
      warnings.push(...file.warnings.map(message => () => `${file.name}: ${i18n.message(message)}`));
      if ((file.kind === 'ppt') !== tools[state.tool].pdf) warnings.push(() => `${file.name}: ${t('Use the {tools} tool to see this file.', { tools: (file.kind === 'ppt' ? ['PPT to images', 'Compress PPT'] : ['Images to PPT', 'Image converter']).map(key => t(key)).join(' / ') })}`);
    }
    warnings.push(...data.errors.map(error => () => `${error.name}: ${i18n.message(error.message)}`));
    if (warnings.length) notice(() => warnings.map(message => message()).join('\n'));

  } catch (error) { notice(error.message); }
  finally { state.uploading = false; state.uploadCount = 0; $('upload-state').textContent = ''; $('file-input').value = ''; renderFiles(); }
}
function readOptions() {
  const number = (id, fallback = null) => $(id).value === '' ? fallback : Number($(id).value);
  const preserve = preserveMode(), target = state.mode === 'target';
  // 100 marks lossless in the UI; the encoder API accepts quality values up to 95.
  return {
    operation: state.tool, format: $('format').value, mode: state.mode, quality: target ? 95 : Math.min(95, number('quality')), min_quality: target ? 1 : Math.min(60, number('quality')),
    lossless: !target && (pdfOutput() ? $('preset').value === 'lossless' : $('format').value === 'webp' && $('lossless').checked),
    dpi: number('dpi'), min_dpi: number('min-dpi'), max_width: preserve ? null : number('max-width'), max_height: preserve ? null : number('max-height'),
    target_kb: state.mode === 'target' ? number('target-size') * Number($('target-unit').value) : null,
    page_image_kb: pdfOutput() && !preserve ? number('page-budget') : null,
    allow_downscale: $('allow-downscale').checked, min_long_edge: number('min-edge'), color: preserve ? 'original' : $('color').value,
    threshold: number('threshold'), background: $('background').value, strip_metadata: pdfOutput() ? $('strip-document-metadata').checked : $('strip-metadata').checked,
    paper: $('paper').value, margin_mm: number('margin'), split: $('split').checked, preserve_original: $('preserve-original').checked,
    compression: $('compression').value, pages: tools[state.tool].pdf ? $('pages').value.trim() : '',
  };
}
async function startJob(preview) {
  if (state.busy || state.uploading) return;
  const selected = selectedPages(); if (!selected.length) return;
  if (!$('settings-form').reportValidity()) return;
  notice(''); setBusy(true); show('job-panel', true); show('results-panel', false);
  state.jobPreview = preview; $('job-title').textContent = t(preview ? 'Preparing your preview' : 'Converting your files');
  $('cancel-job').disabled = false; state.jobStatus = 'Starting…'; $('job-detail').textContent = t(state.jobStatus); $('progress-bar').style.width = '0%';
  try {
    const body = { selections: selected.map(({ file_id, page, rotation }) => ({ file_id, page, rotation })), options: readOptions(), preview };
    const job = await api('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    state.currentJob = job.id;
    while (true) {
      const status = await api(`/api/jobs/${job.id}`);
      state.jobStatus = status.detail; $('job-detail').textContent = i18n.message(status.detail);
      $('progress-bar').style.width = `${Math.max(3, status.progress)}%`;
      if (status.state === 'completed') { renderResults(status); break; }
      if (['failed', 'cancelled'].includes(status.state)) { notice(status.error || status.detail); break; }
      await new Promise(resolve => setTimeout(resolve, 650));
    }
  } catch (error) { notice(error.message); }
  finally { state.currentJob = null; setBusy(false); show('job-panel', false); }
}
function renderResults(job) {
  state.resultJob = job; show('results-panel', true); show('preview-empty', false); show('preview-stage', true);
  $('preview-image').src = `/api/jobs/${job.id}/preview`;
  $('preview-image').onerror = () => notice('Preview expired or could not be loaded. Try a new preview.');
  $('preview-fit').disabled = false; $('preview-actual').disabled = false;
  $('preview-tag').textContent = t(job.preview ? 'ONE-PAGE SAMPLE' : 'FIRST OUTPUT');
  $('results-title').textContent = job.preview ? t('Preview result · one-page sample') : `${fileCount(job.outputs.length)} · ${bytes(job.outputs.reduce((sum, output) => sum + output.size, 0))}`;
  if (!job.preview && ['ppt_compress', 'image_convert'].includes(state.tool) && job.input_bytes) {
    const reduction = 100 * (1 - job.outputs.reduce((sum, output) => sum + output.size, 0) / job.input_bytes);
    $('results-title').textContent += ` · ${t(reduction >= 0 ? '{percent}% smaller' : '{percent}% larger', { percent: Math.abs(reduction).toFixed(1) })}`;
  }
  $('download-zip').href = `/api/jobs/${job.id}/zip`;
  $('result-warnings').replaceChildren(...(job.warnings || []).map(message => node('p', '', i18n.message(message))));
  const list = $('result-list'); list.replaceChildren();
  job.outputs.forEach((output, index) => {
    const row = node('div', 'result-row'), info = node('div', 'file-info');
    const details = [output.width ? `${output.width} × ${output.height} px` : pageCount(output.pages)];
    if (output.quality) details.push(t('quality {quality}', { quality: output.quality }));
    if (output.original_pages) details.push(t(output.original_pages === 1 ? '{count} original image embedded' : '{count} original images embedded', { count: output.original_pages }));
    if (output.scale < 1) details.push(t('{percent}% resolution', { percent: Math.round(output.scale * 100) }));
    info.append(node('div', 'file-name', output.name), node('div', 'file-meta', details.join(' · ')));
    row.append(info);
    if (output.target_met !== null && output.target_met !== undefined) row.append(node('span', `result-badge${output.target_met ? '' : ' missed'}`, t(output.target_met ? 'Target met' : 'Target not met')));
    row.append(node('span', 'result-size', bytes(output.size)));
    const link = node('a', 'result-download', '↓'); link.href = `/api/jobs/${job.id}/outputs/${index}`; link.setAttribute('aria-label', t('Download {name}', { name: output.name })); row.append(link); list.append(row);
  });
}
function switchTool(tool) {
  if (state.busy) { notice('Finish or cancel the current conversion before changing tools.'); return; }
  state.tool = tool; notice('');
  document.querySelectorAll('.tool').forEach(button => { button.classList.toggle('active', button.dataset.tool === tool); button.setAttribute('aria-current', button.dataset.tool === tool ? 'page' : 'false'); });
  updateToolLabels();
  const selected = tools[tool];
  $('file-input').accept = selected.pdf ? '.ppt,.pptx' : '.jpg,.jpeg,.png,.webp,.tif,.tiff,.bmp,.gif,.avif,.heic,.heif';
  show('results-panel', false); show('preview-stage', false); show('preview-empty', true); $('preview-fit').disabled = true; $('preview-actual').disabled = true;
  $('target-size').value = pdfOutput() ? '10' : '1'; $('target-unit').value = '1000'; $('pages').value = '';
  renderFiles();
}
function resetSettings() {
  $('settings-form').reset(); $('size-options').open = false; state.mode = 'quality'; state.lossyQuality = 85;
  document.querySelectorAll('[data-mode]').forEach(button => button.classList.toggle('selected', button.dataset.mode === state.mode));
  $('target-size').value = pdfOutput() ? '10' : '1'; $('target-unit').value = '1000';
  updateSettings();
}
function updateToolLabels() {
  const selected = tools[state.tool];
  $('breadcrumb').textContent = t(selected.name); $('convert-label').textContent = t(selected.action);
  $('accepted-formats').textContent = t('{formats} · up to {limit} MB each', { formats: selected.pdf ? t('PPT / PPTX files') : 'JPG, PNG, WebP, TIFF, BMP, GIF, AVIF & HEIC', limit: state.capabilities?.limits.file_mb || 100 });
}
function refreshLanguage() {
  i18n.apply(); $('language-select').value = i18n.language;
  $('connection-text').textContent = t(state.connection);
  for (const option of $('format').options) option.textContent = t(option.dataset.i18n) + (option.disabled ? t(' (unavailable)') : '');
  updateToolLabels(); renderFiles();
  $('upload-state').textContent = state.uploading ? t('Inspecting {count} files…', { count: state.uploadCount }) : '';
  $('job-title').textContent = t(state.jobPreview ? 'Preparing your preview' : 'Converting your files');
  $('job-detail').textContent = i18n.message(state.jobStatus);
  if (state.capabilities) $('retention-note').textContent = t('Files are automatically deleted {minutes} minutes after completion. Refreshing the page clears the file list; your downloads remain on your device.', { minutes: Math.round(state.capabilities.limits.retention_seconds / 60) });
  if (state.resultJob && !$('results-panel').hidden) renderResults(state.resultJob);
  notice(state.notice);
}
$('language-select').onchange = event => { i18n.setLanguage(event.target.value); refreshLanguage(); };
refreshLanguage();
document.querySelectorAll('.tool').forEach(button => button.onclick = () => switchTool(button.dataset.tool));
document.querySelectorAll('[data-mode]').forEach(button => button.onclick = () => { state.mode = button.dataset.mode; document.querySelectorAll('[data-mode]').forEach(other => other.classList.toggle('selected', other === button)); updateSettings(); });
$('file-input').onchange = event => uploadFiles(event.target.files);
$('dropzone').onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('file-input').click(); } };
for (const event of ['dragenter', 'dragover']) $('dropzone').addEventListener(event, value => { value.preventDefault(); $('dropzone').classList.add('dragover'); });
for (const event of ['dragleave', 'drop']) $('dropzone').addEventListener(event, value => { value.preventDefault(); $('dropzone').classList.remove('dragover'); });
$('dropzone').addEventListener('drop', event => uploadFiles(event.dataTransfer.files));
window.addEventListener('dragover', event => event.preventDefault()); window.addEventListener('drop', event => event.preventDefault());
$('clear-files').onclick = async () => { for (const file of [...currentFiles()]) await removeFile(file.id); };
$('select-all').onclick = () => { currentPages().forEach(page => page.selected = true); renderPages(); updateSettings(); };
$('select-none').onclick = () => { currentPages().forEach(page => page.selected = false); renderPages(); updateSettings(); };
$('settings-form').onsubmit = event => { event.preventDefault(); startJob(false); };
$('preview-button').onclick = () => startJob(true);
$('cancel-job').onclick = async () => { if (!state.currentJob) return; try { await api(`/api/jobs/${state.currentJob}/cancel`, { method: 'POST' }); $('cancel-job').disabled = true; } catch (error) { notice(error.message); } };
$('preview-fit').onclick = () => $('preview-stage').classList.remove('actual');
$('preview-actual').onclick = () => $('preview-stage').classList.add('actual');
$('delete-results').onclick = async () => { if (!state.resultJob) return; try { await api(`/api/jobs/${state.resultJob.id}`, { method: 'DELETE' }); state.resultJob = null; show('results-panel', false); show('preview-stage', false); show('preview-empty', true); $('preview-image').removeAttribute('src'); $('preview-fit').disabled = true; $('preview-actual').disabled = true; } catch (error) { notice(error.message); } };
$('reset-settings').onclick = resetSettings;
$('settings-form').addEventListener('input', updateSettings);
$('settings-form').addEventListener('change', updateSettings);
$('size-options').addEventListener('invalid', () => { $('size-options').open = true; }, true);
$('quality').addEventListener('input', () => { $('preset').value = 'custom'; updateSettings(); });
$('dpi').addEventListener('input', () => { if ($('dpi').value) $('min-dpi').value = Math.min(Number($('min-dpi').value), Number($('dpi').value)); });
$('preset').onchange = () => {
  const presets = { balanced: [200, 85], small: [150, 70], print: [300, 95], lossless: [200, 95] };
  const preset = presets[$('preset').value]; if (!preset) return;
  [$('dpi').value, $('quality').value] = preset; state.lossyQuality = preset[1];
  if ($('preset').value === 'lossless') { if (!pdfOutput()) $('format').value = 'png'; $('lossless').checked = true; }
  else $('lossless').checked = false;
  $('min-dpi').value = Math.min(Number($('min-dpi').value), preset[0]); updateSettings();
};
(async () => {
  try {
    state.capabilities = await api('/api/ready');
    $('connection-dot').classList.add('online'); state.connection = 'Conversion engine ready';
    for (const option of $('format').options) option.disabled = !state.capabilities.formats.output.includes(option.value);
    refreshLanguage();
    switchTool(state.tool);
    if (!state.capabilities.renderer.available) notice('PPT rendering requires Microsoft PowerPoint on Windows or LibreOffice. Image conversion remains available.');
  } catch (error) { state.connection = 'Engine unavailable'; refreshLanguage(); notice(() => t('Could not connect to the conversion engine. {message}', { message: i18n.message(error.message) })); }
})();
