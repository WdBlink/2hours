const desk = document.querySelector('.desk');
const dialog = document.querySelector('#review-window');
const content = document.querySelector('.window-content');
const storage = document.querySelector('#panel-store');
const closeButton = document.querySelector('.window-close');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let origin = null;
let motion = null;
let closing = false;

function cardTransform() {
  const card = origin.getBoundingClientRect();
  const paper = dialog.getBoundingClientRect();
  return `translate(${card.x + card.width / 2 - paper.x - paper.width / 2}px, ${card.y + card.height / 2 - paper.y - paper.height / 2}px) scale(${card.width / paper.width}, ${card.height / paper.height})`;
}
function openCard(button) {
  if (dialog.open) return;
  const panel = document.getElementById(button.dataset.panel);
  if (!panel) return;
  origin = button;
  closing = false;
  const overview = document.createElement('div');
  overview.className = 'window-card-info';
  for (const value of [button.dataset.summary, [button.dataset.value, button.dataset.unit].filter(Boolean).join(' '), button.dataset.state]) {
    if (!value) continue;
    const line = document.createElement('p');
    line.textContent = value;
    overview.append(line);
  }
  if (overview.childElementCount) content.append(overview);
  content.append(panel);
  document.querySelector('#window-label').textContent = button.dataset.title;
  document.querySelector('.window-context').textContent = button.dataset.context;
  dialog.showModal();
  content.scrollTop = 0;
  closeButton.focus({preventScroll: true});
  if (!reducedMotion.matches) {
    motion = dialog.animate([
      {transform: cardTransform(), opacity: .15},
      {transform: 'none', opacity: 1}
    ], {duration: 720, easing: 'cubic-bezier(.22,1,.36,1)'});
  }
}
async function closeCard() {
  if (!dialog.open || closing) return;
  closing = true;
  motion?.cancel();
  // Recompute after cancelling the opening animation so rapid Escape uses final geometry.
  if (!reducedMotion.matches) {
    motion = dialog.animate([
      {transform: 'none', opacity: 1},
      {transform: cardTransform(), opacity: 0}
    ], {duration: 320, easing: 'cubic-bezier(.4,0,1,1)'});
    await motion.finished.catch(() => {});
  }
  dialog.close();
  motion?.cancel();
  content.querySelector('.window-card-info')?.remove();
  while (content.firstElementChild) storage.append(content.firstElementChild);
  origin?.focus({preventScroll: true});
  closing = false;
}
desk.addEventListener('click', event => {
  const button = event.target.closest('button[data-panel]');
  if (button) openCard(button);
});
closeButton.addEventListener('click', closeCard);
dialog.addEventListener('cancel', event => { event.preventDefault(); closeCard(); });
// A drag ending on the backdrop must not dismiss a selection made inside the report.
let backdropPress = false;
dialog.addEventListener('pointerdown', event => { backdropPress = event.target === dialog; });
dialog.addEventListener('click', event => {
  if (backdropPress && event.target === dialog) closeCard();
  backdropPress = false;
});

const narrowBoard = matchMedia('(max-width: 1099px)');
const shelves = [...document.querySelectorAll('.board-shelf')].map(element => ({
  element,
  grid: element.querySelector('.card-grid'),
  cards: [...element.querySelectorAll('.card-shell')],
  page: 0,
  pages: [{start: 0, end: 0, rows: []}]
}));
let activeArea = shelves.find(shelf => shelf.cards.length)?.element.dataset.area || 'reading';
function pageRows(heights, columns, available, gap) {
  const pages = [];
  let page = {start: 0, end: 0, rows: []};
  let used = 0;
  for (let start = 0; start < heights.length; start += columns) {
    const end = Math.min(start + columns, heights.length);
    const height = Math.max(...heights.slice(start, end));
    if (page.rows.length && used + gap + height > available) {
      pages.push(page);
      page = {start, end: start, rows: []};
      used = 0;
    }
    used += (page.rows.length ? gap : 0) + height;
    page.rows.push(height);
    page.end = end;
  }
  pages.push(page);
  return pages;
}
function paintPage(shelf) {
  shelf.page = Math.min(shelf.page, shelf.pages.length - 1);
  const page = shelf.pages[shelf.page];
  const reading = shelf.element.dataset.area === 'reading';
  shelf.cards.forEach((card, index) => {card.hidden = index < page.start || index >= page.end;});
  shelf.element.querySelector('[data-step="-1"]').disabled = shelf.page === 0;
  shelf.element.querySelector('[data-step="1"]').disabled = shelf.page === shelf.pages.length - 1;
  shelf.element.querySelector('.page-status').textContent = shelf.cards.length ? `${page.start + 1}–${page.end} / ${shelf.cards.length} 份 · 第 ${shelf.page + 1}/${shelf.pages.length} 页` : '0 份';
  const gap = parseFloat(shelf.grid.style.gap) || 0;
  const free = Math.max(0, shelf.grid.clientHeight - page.rows.reduce((sum, height) => sum + height, 0) - gap * (page.rows.length - 1));
  shelf.grid.style.gridTemplateRows = page.rows.map(height => `${reading ? height : height + Math.min(free / page.rows.length, Math.max(0, 240 - height))}px`).join(' ') || 'minmax(0,1fr)';
  shelf.grid.classList.toggle('has-tall-row', page.rows.some(height => height > shelf.grid.clientHeight));
}
function fitShelf(shelf) {
  if (shelf.element.hidden) return;
  const first = shelf.pages[shelf.page]?.start || 0;
  const navigation = shelf.element.querySelector('.shelf-pages');
  navigation.hidden = true;
  shelf.element.dataset.paged = 'false';
  if (!shelf.cards.length) {
    shelf.pages = [{start: 0, end: 0, rows: []}];
    paintPage(shelf);
    return;
  }
  const reading = shelf.element.dataset.area === 'reading';
  const width = shelf.grid.clientWidth;
  const available = shelf.grid.clientHeight;
  const maximum = reading ? 1 : Math.min(shelf.cards.length, Math.max(1, Math.floor((width + 12) / 248)));
  const preferred = Math.min(maximum, Math.max(1, Math.floor((width + 18) / 338)));
  const columns = [...Array(maximum)].map((_, index) => index + 1).sort((a, b) => Math.abs(a - preferred) - Math.abs(b - preferred) || b - a);
  const layouts = [];
  shelf.cards.forEach(card => {card.hidden = false;});
  shelf.grid.classList.add('is-measuring');
  for (const gap of reading ? [0] : [18, 12]) {
    for (const count of columns) {
      const density = gap === 12 ? 'compact' : 'comfortable';
      shelf.grid.dataset.density = density;
      shelf.grid.style.gap = `${gap}px`;
      shelf.grid.style.gridTemplateColumns = `repeat(${count}, minmax(0,1fr))`;
      const heights = shelf.cards.map(card => card.getBoundingClientRect().height);
      const pages = pageRows(heights, count, available, gap);
      layouts.push({columns: count, gap, density, heights, pages});
      if (pages.length === 1) break;
    }
    if (layouts.at(-1).pages.length === 1) break;
  }
  let best = layouts.find(layout => layout.pages.length === 1);
  if (!best) {
    navigation.hidden = false;
    shelf.element.dataset.paged = 'true';
    for (const layout of layouts) layout.pages = pageRows(layout.heights, layout.columns, shelf.grid.clientHeight, layout.gap);
    best = layouts.sort((a, b) => a.pages.length - b.pages.length || b.pages[0].end - a.pages[0].end)[0];
  }
  shelf.grid.classList.remove('is-measuring');
  shelf.grid.dataset.density = best.density;
  shelf.grid.style.gap = `${best.gap}px`;
  shelf.grid.style.gridTemplateColumns = `repeat(${best.columns}, minmax(0,1fr))`;
  shelf.pages = best.pages;
  shelf.page = Math.max(0, shelf.pages.findIndex(page => page.start <= first && first < page.end));
  paintPage(shelf);
}
function fitBoard() {
  for (const shelf of shelves) {
    shelf.element.hidden = narrowBoard.matches && shelf.element.dataset.area !== activeArea;
    fitShelf(shelf);
  }
  for (const tab of document.querySelectorAll('.board-nav button')) {
    tab.setAttribute('aria-pressed', String(tab.dataset.area === activeArea));
  }
}
for (const shelf of shelves) {
  shelf.element.querySelector('.shelf-pages').addEventListener('click', event => {
    const button = event.target.closest('button[data-step]');
    if (!button || button.disabled) return;
    shelf.page += Number(button.dataset.step);
    paintPage(shelf);
  });
}
document.querySelector('.board-nav').addEventListener('click', event => {
  const button = event.target.closest('button[data-area]');
  if (!button) return;
  activeArea = button.dataset.area;
  fitBoard();
});
new ResizeObserver(fitBoard).observe(document.querySelector('.board'));
const reviewLabels = new MutationObserver(() => requestAnimationFrame(fitBoard));
for (const shelf of shelves) {
  for (const card of shelf.cards) reviewLabels.observe(card.querySelector('.review-card'), {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['data-read']});
}
fitBoard();
