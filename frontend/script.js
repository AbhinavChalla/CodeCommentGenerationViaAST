  const textarea    = document.getElementById('codeInput');
  const lineNumbers = document.getElementById('lineNumbers');
  const generateBtn = document.getElementById('generateBtn');
  const btnIcon     = document.getElementById('btnIcon');
  const btnText     = document.getElementById('btnText');
  const outputWrap  = document.getElementById('outputWrap');
  const emptyState  = document.getElementById('emptyState');
  const copyBtn     = document.getElementById('copyBtn');
  const statusBar   = document.getElementById('statusBar');
  const statusMsg   = document.getElementById('statusMsg');
  const lineCountEl = document.getElementById('lineCount');

  // ── Line numbers ──────────────────────────────
  function updateLineNumbers() {
    const lines = textarea.value.split('\n').length;
    let html = '';
    for (let i = 1; i <= lines; i++) {
      html += `<span>${i}</span>`;
    }
    lineNumbers.innerHTML = html;

    // Sync scroll
    lineNumbers.scrollTop = textarea.scrollTop;
  }

  function updateCursor() {
    const txt   = textarea.value.substring(0, textarea.selectionStart);
    const lines = txt.split('\n');
    const ln    = lines.length;
    const col   = lines[lines.length - 1].length + 1;
    lineCountEl.textContent = `Ln ${ln}, Col ${col}`;

    // Highlight current line number
    const spans = lineNumbers.querySelectorAll('span');
    spans.forEach((s, i) => {
      s.classList.toggle('active', i === ln - 1);
    });
  }

  textarea.addEventListener('input',   updateLineNumbers);
  textarea.addEventListener('keyup',   updateCursor);
  textarea.addEventListener('click',   updateCursor);
  textarea.addEventListener('scroll',  () => { lineNumbers.scrollTop = textarea.scrollTop; });

  // Tab key support
  textarea.addEventListener('keydown', e => {
    if (e.key === 'Tab') {
      e.preventDefault();
      const s = textarea.selectionStart;
      textarea.value = textarea.value.substring(0, s) + '    ' + textarea.value.substring(textarea.selectionEnd);
      textarea.selectionStart = textarea.selectionEnd = s + 4;
      updateLineNumbers();
    }
  });

  // ── Generate ──────────────────────────────────
  async function generateComments() {
    const code = textarea.value.trim();
    if (!code) { showToast('⚠ Please paste some Python code first.'); return; }

    setLoading(true);
    setStatus('⏳ Sending to API...', 'default');

    try {
      const response = await fetch('/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Server error');
      }

      renderOutput(data.commented_code);
      setStatus('✓ Done', 'success');
      showToast('✓ Comments generated successfully!');

    } catch (err) {
      setStatus('✕ Error', 'error');
      showToast(`✕ ${err.message}`);
    } finally {
      setLoading(false);
    }
  }

  // ── Render output with syntax highlight + line numbers ──
  function renderOutput(code) {
    emptyState.style.display = 'none';
    copyBtn.style.display    = 'flex';

    const lines = code.split('\n');
    let gutterHtml = '';
    lines.forEach((_, i) => { gutterHtml += `<span>${i + 1}</span>`; });

    // Highlight
    const highlighted = hljs.highlight(code, { language: 'python' }).value;

    outputWrap.innerHTML = `
      <div class="output-inner">
        <div class="output-gutter">${gutterHtml}</div>
        <div class="output-code-wrap">
          <pre><code class="hljs language-python">${highlighted}</code></pre>
        </div>
      </div>`;

    // Store raw for copy
    outputWrap.dataset.raw = code;
  }

  // ── Helpers ───────────────────────────────────
  function setLoading(on) {
    generateBtn.disabled = on;
    btnIcon.innerHTML = on ? '<span class="loader"></span>' : '▶';
    btnText.textContent = on ? 'Generating...' : 'Generate Comments';
  }

  function setStatus(msg, type) {
    statusMsg.textContent = msg;
    statusBar.className = 'status-bar';
    if (type === 'error')   statusBar.classList.add('error-state');
    if (type === 'success') statusBar.classList.add('success-state');
    if (type !== 'default') setTimeout(() => { statusBar.className = 'status-bar'; statusMsg.textContent = '◈ Ready'; }, 3000);
  }

  function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 3000);
  }

  function copyOutput() {
    const raw = outputWrap.dataset.raw;
    if (!raw) return;
    navigator.clipboard.writeText(raw).then(() => showToast('⧉ Copied to clipboard!'));
  }

  function clearInput() {
    textarea.value = '';
    updateLineNumbers();
  }

  function clearAll() {
    clearInput();
    outputWrap.innerHTML = '';
    outputWrap.dataset.raw = '';
    const es = document.createElement('div');
    es.id = 'emptyState';
    es.className = 'empty-state';
    es.innerHTML = '<div class="es-icon">◈</div><p>Your commented code<br/>will appear here.</p>';
    outputWrap.appendChild(es);
    copyBtn.style.display = 'none';
    setStatus('◈ Ready', 'default');
  }

  function loadExample() {
    textarea.value = `def fibonacci(n):
    if n <= 0:
        return []
    elif n == 1:
        return [0]
    sequence = [0, 1]
    while len(sequence) < n:
        sequence.append(sequence[-1] + sequence[-2])
    return sequence

def is_prime(num):
    if num < 2:
        return False
    for i in range(2, int(num**0.5) + 1):
        if num % i == 0:
            return False
    return True

result = fibonacci(10)
primes = [x for x in result if is_prime(x)]
print(primes)`;
    updateLineNumbers();
    showToast('⤓ Example code loaded.');
  }

  async function openAstPdf() {
    const code = textarea.value.trim();
    if (!code) { showToast('⚠ Please paste some Python code first.'); return; }
    try {
      const response = await fetch('/open_ast_pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code })
      });
      if (!response.ok) {
        const data = await response.json();
        showToast(`✕ ${data.error || 'Failed to generate PDF'}`);
      }
    } catch (err) {
      showToast(`✕ ${err.message}`);
    }
  }

  // Init
  updateLineNumbers();
