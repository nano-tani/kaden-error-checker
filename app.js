const form = document.getElementById('search-form');
const input = document.getElementById('error-code');
const status = document.getElementById('status');
const results = document.getElementById('results');

let records = [];

function normalizeCode(value) {
  return value
    .trim()
    .normalize('NFKC')
    .toUpperCase()
    .replace(/\s+/g, '');
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function isMatch(record, query) {
  const candidates = [record.code, ...(record.aliases || [])].map(normalizeCode);
  return candidates.includes(query);
}

function render(matches, query) {
  results.innerHTML = '';

  if (!query) {
    status.textContent = '';
    return;
  }

  if (!matches.length) {
    status.textContent = `「${query}」は現在のデータベースに登録されていません。`;
    return;
  }

  status.textContent = `${matches.length}件見つかりました。メーカー・製品種別を確認してください。`;

  for (const item of matches) {
    const card = document.createElement('article');
    card.className = 'card';
    card.innerHTML = `
      <div class="meta">${escapeHtml(item.manufacturer)} / ${escapeHtml(item.appliance)}</div>
      <h2 class="code">${escapeHtml(item.code)}</h2>
      <p class="summary">${escapeHtml(item.summary)}</p>
      <ol class="actions">
        ${item.actions.map(action => `<li>${escapeHtml(action)}</li>`).join('')}
      </ol>
      <a class="source" href="${escapeHtml(item.source)}" target="_blank" rel="noopener noreferrer">メーカー公式情報を確認 →</a>
      <div class="meta">確認日: ${escapeHtml(item.verified)}</div>
    `;
    results.appendChild(card);
  }
}

async function loadData() {
  try {
    const response = await fetch('./data/errors.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    records = await response.json();
    status.textContent = `登録コード: ${records.length}件`;
  } catch (error) {
    console.error(error);
    status.textContent = 'データを読み込めませんでした。';
  }
}

form.addEventListener('submit', event => {
  event.preventDefault();
  const query = normalizeCode(input.value);
  const exact = records.filter(record => isMatch(record, query));
  render(exact, query);

  if (query) {
    const url = new URL(window.location.href);
    url.searchParams.set('code', query);
    history.replaceState(null, '', url);
  }
});

loadData().then(() => {
  const query = normalizeCode(new URLSearchParams(location.search).get('code') || '');
  if (query) {
    input.value = query;
    render(records.filter(record => isMatch(record, query)), query);
  }
});
