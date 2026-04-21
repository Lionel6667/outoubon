const SUBJECT_LABELS = {
  maths:'Maths', physique:'Physique', chimie:'Chimie', svt:'SVT',
  francais:'Kreyòl', philosophie:'Philosophie', histoire:'Sciences Sociales',
  anglais:'Anglais', espagnol:'Espagnol',
};

function normalizeDiagText(raw, stripChoicePrefix = false) {
  let txt = String(raw || '');
  if (stripChoicePrefix) {
    // Strip letter label prefix like "A:", "A:", "A)", "A -" etc.
    txt = txt.replace(/^\s*[A-D]\s*[:.)-]\s*/i, '');
  }
  return fixLatexControls(txt.replace(/\u00A0/g, ' ').replace(/\u202F/g, ' ').trim());
}

function selectOpt(label, name) {
  const card = label.closest('.diag-card');
  if (card.dataset.answered) return;
  card.dataset.answered = '1';

  const correctIdx = parseInt(card.dataset.correct, 10);
  const chosenIdx  = parseInt(label.dataset.idx, 10);
  const isCorrect  = chosenIdx === correctIdx;
  const expl       = card.dataset.expl || '';
  const cleanExpl  = normalizeDiagText(expl, false);

  label.querySelector('input[type=radio]').checked = true;

  const allLabels = card.querySelectorAll('.diag-option');
  allLabels.forEach(l => l.classList.add('disabled'));
  label.classList.add(isCorrect ? 'correct' : 'wrong');
  allLabels[correctIdx].classList.add('correct');

  const fb = card.querySelector('.diag-feedback');
  const explHtml = cleanExpl
    ? `<div style="margin-top:.4rem;padding:.65rem .9rem;background:rgba(16,185,129,.06);border-left:3px solid var(--green);border-radius:0 8px 8px 0;font-size:.83rem;color:var(--t2);line-height:1.55">
        <span style="font-size:.7rem;font-weight:700;color:var(--green);text-transform:uppercase;letter-spacing:.05em">📖 Explication</span><br>
        ${renderMarkdown(cleanExpl)}
       </div>` : '';

  if (isCorrect) {
    fb.innerHTML = `<div style="display:flex;align-items:center;gap:.4rem;color:var(--green);font-weight:700;font-size:.88rem;">✅ Bonne réponse !</div>${explHtml}`;
  } else {
    const correctText = normalizeDiagText(allLabels[correctIdx].querySelector('.diag-opt-text').textContent, true);
    fb.innerHTML = `<div style="font-size:.88rem;">❌ <strong style="color:#fca5a5;">Mauvaise réponse.</strong> Bonne réponse : <strong style="color:var(--green)">${correctText}</strong></div>${explHtml}`;
  }
  fb.style.cssText = 'display:block;margin-top:.75rem;';
  renderKatex(fb);
}

let currentBlock = 1;
const totalBlocks = 8;

function _syncUI() {
  document.getElementById('dNum').textContent = currentBlock;
  document.getElementById('dProgBar').style.width = ((currentBlock / totalBlocks) * 100) + '%';
  const block = document.getElementById('dq-' + currentBlock);
  if (block) {
    const subj = block.dataset.subject || '';
    document.getElementById('subjTag').textContent = SUBJECT_LABELS[subj] || subj;
    block.querySelectorAll('.diag-card').forEach((c, i) => {
      c.style.animation = 'none';
      void c.offsetHeight;
      c.style.animation = `slideIn .28s ease ${i * 55}ms both`;
    });
  }
  const isLast = currentBlock === totalBlocks;
  document.getElementById('backBtn').style.display       = currentBlock > 1 ? 'inline-flex' : 'none';
  document.getElementById('nextDiagBtn').style.display   = isLast ? 'none' : 'block';
  document.getElementById('submitDiagBtn').style.display = isLast ? 'block' : 'none';
}

function diagNext() {
  if (currentBlock < totalBlocks) {
    document.getElementById('dq-' + currentBlock).style.display = 'none';
    currentBlock++;
    document.getElementById('dq-' + currentBlock).style.display = 'block';
    _syncUI();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}
function diagBack() {
  if (currentBlock > 1) {
    document.getElementById('dq-' + currentBlock).style.display = 'none';
    currentBlock--;
    document.getElementById('dq-' + currentBlock).style.display = 'block';
    _syncUI();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
}

_syncUI();

document.getElementById('diagForm').addEventListener('submit', function() {
  document.getElementById('savingOverlay').style.display = 'flex';
});

function initDiagnosticMathRender() {
  if (typeof renderMathInElement === 'undefined') {
    console.error('[DIAG] renderMathInElement not loaded! KaTeX auto-render CDN may be blocked.');
    return;
  }
  // Strip numbering prefix from enonces and render KaTeX
  document.querySelectorAll('.diag-enonce').forEach(el => {
    let text = el.textContent.replace(/\u00A0/g, ' ').replace(/\u202F/g, ' ').replace(/^\s*\d+[\.)-]\s*/, '').trim();
    text = fixLatexControls(text);
    el.textContent = text;
    renderMathInElement(el, _katexOpts);
  });
  // Strip "A:" prefix from options and render KaTeX
  document.querySelectorAll('.diag-opt-text').forEach(el => {
    let text = el.textContent.replace(/\u00A0/g, ' ').replace(/\u202F/g, ' ').replace(/^\s*[A-D]\s*[:.)\-]\s*/i, '').trim();
    text = fixLatexControls(text);
    el.textContent = text;
    renderMathInElement(el, _katexOpts);
  });
}

// Use setTimeout(0) to guarantee all scripts and DOM are fully ready
setTimeout(initDiagnosticMathRender, 0);
