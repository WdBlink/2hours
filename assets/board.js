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
