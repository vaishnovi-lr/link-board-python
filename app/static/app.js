// Link Board — Refined Dark Aesthetic Logic
// Preserves all API contracts and adds polished human-designed interactions

let currentFormula = 'reddit';
let currentPage = 1;
const pageSize = 10;
let currentLinkId = null;

// Track user-upvoted links in memory / session so the accent state persists
const votedLinks = new Set();
try {
  const saved = sessionStorage.getItem('lb_voted_links');
  if (saved) {
    JSON.parse(saved).forEach(id => votedLinks.add(Number(id)));
  }
} catch (e) {
  /* sessionStorage unavailable */
}

function recordVote(lid) {
  votedLinks.add(Number(lid));
  try {
    sessionStorage.setItem('lb_voted_links', JSON.stringify([...votedLinks]));
  } catch (e) {
    /* ignore */
  }
}

// DOM Elements
const views = {
  home: document.getElementById('viewHome'),
  details: document.getElementById('viewDetails'),
  explain: document.getElementById('viewExplain'),
  status: document.getElementById('viewStatus')
};

const desktopNavBtns = {
  home: document.getElementById('navBtnHome'),
  explain: document.getElementById('navBtnExplain'),
  status: document.getElementById('navBtnStatus'),
  submit: document.getElementById('navBtnSubmit')
};

const mobileNavBtns = {
  home: document.getElementById('mobNavHome'),
  explain: document.getElementById('mobNavExplain'),
  status: document.getElementById('mobNavStatus'),
  submit: document.getElementById('mobNavSubmit')
};

// --- ROUTING / VIEW SWITCHING ---
function switchView(viewName) {
  if (viewName === 'submit') {
    openSubmitModal();
    return;
  }

  Object.keys(views).forEach(name => {
    if (views[name]) {
      if (name === viewName) {
        views[name].classList.remove('hidden');
      } else {
        views[name].classList.add('hidden');
      }
    }
  });

  // Update Desktop Rail Active state
  Object.keys(desktopNavBtns).forEach(name => {
    if (desktopNavBtns[name]) {
      if (name === viewName) {
        desktopNavBtns[name].classList.add('active');
      } else {
        desktopNavBtns[name].classList.remove('active');
      }
    }
  });

  // Update Mobile Navigation Active state
  Object.keys(mobileNavBtns).forEach(name => {
    if (mobileNavBtns[name]) {
      if (name === viewName) {
        mobileNavBtns[name].classList.add('active');
      } else {
        mobileNavBtns[name].classList.remove('active');
      }
    }
  });

  window.scrollTo({ top: 0, behavior: 'smooth' });

  if (viewName === 'home') {
    loadFrontPage();
  } else if (viewName === 'status') {
    loadHealthStatus();
  }
}

// --- TIME FORMATTING ---
function formatAge(ageHours) {
  if (ageHours === undefined || ageHours === null) return '';
  if (ageHours < 0.05) {
    return 'just now';
  }
  if (ageHours < 1) {
    const mins = Math.max(1, Math.round(ageHours * 60));
    return `${mins}m ago`;
  } else if (ageHours < 24) {
    const hrs = Math.round(ageHours);
    return `${hrs}h ago`;
  } else {
    const days = Math.round(ageHours / 24);
    return `${days}d ago`;
  }
}

function extractDomain(urlStr) {
  try {
    const url = new URL(urlStr);
    return url.hostname.replace(/^www\./, '');
  } catch (e) {
    return urlStr;
  }
}

// --- FRONT PAGE FEED LOGIC ---
async function loadFrontPage() {
  const container = document.getElementById('linksList');
  container.innerHTML = `
    <div class="feed-empty-state">
      <span>Retrieving ranked feed...</span>
    </div>
  `;

  const hnNotice = document.getElementById('hnNoticeBanner');
  const formulaInfoText = document.getElementById('formulaInfoText');

  if (currentFormula === 'hn') {
    if (hnNotice) hnNotice.classList.remove('hidden');
    if (formulaInfoText) formulaInfoText.textContent = 'Computed dynamically per request (score decays with time)';
  } else {
    if (hnNotice) hnNotice.classList.add('hidden');
    if (formulaInfoText) formulaInfoText.textContent = 'Order is timestamp-stable. Instant lookup from Redis sorted set.';
  }

  try {
    const res = await fetch(`/front?page=${currentPage}&size=${pageSize}&formula=${currentFormula}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load links');
    }

    const data = await res.json();
    renderLinksList(data.links, data.from_sorted_set);
    updatePagination(data.page, data.links ? data.links.length : 0);
  } catch (err) {
    container.innerHTML = `
      <div class="modal-error-alert" style="margin-top: 1rem;">
        Error loading feed: ${escapeHtml(err.message)}
      </div>
    `;
  }
}

function renderLinksList(links, fromSortedSet) {
  const container = document.getElementById('linksList');
  if (!links || links.length === 0) {
    container.innerHTML = `
      <div class="feed-empty-state">
        <p>No links found on this page.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = links.map(link => {
    const domain = extractDomain(link.url);
    const ageText = formatAge(link.age_hours);
    const hotScoreStr = typeof link.hot === 'number' ? link.hot.toFixed(2) : link.hot;
    const isVoted = votedLinks.has(Number(link.id));

    return `
      <article class="feed-row" id="link-row-${link.id}">
        <span class="feed-rank">${link.position || ''}</span>

        <div class="vote-widget ${isVoted ? 'voted' : ''}" id="vote-widget-${link.id}">
          <button class="btn-vote-trigger ${isVoted ? 'voted' : ''}"
                  id="vote-btn-${link.id}"
                  onclick="handleVote(${link.id}, 'up', this)"
                  aria-label="Upvote ${escapeHtml(link.title)}"
                  title="Upvote">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="${isVoted ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="18 15 12 9 6 15"></polyline>
            </svg>
          </button>
          <span class="vote-number" id="score-${link.id}">${link.score}</span>
        </div>

        <div class="feed-body">
          <div class="feed-title-line">
            <a href="${link.url}" target="_blank" rel="noopener noreferrer" class="feed-title">${escapeHtml(link.title)}</a>
            <span class="feed-domain">${domain}</span>
          </div>

          <div class="feed-meta-line">
            <span class="meta-author">${escapeHtml(link.submitted_by || 'anonymous')}</span>
            <span class="meta-sep">&bull;</span>
            <span class="meta-time">${ageText}</span>
            <span class="meta-sep">&bull;</span>
            <button class="meta-comments-link" onclick="openLinkDetails(${link.id})" aria-label="View comments">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
              </svg>
              <span>${link.comments || 0} comments</span>
            </button>
            <span class="meta-sep">&bull;</span>
            <span class="meta-hot-score" title="Algorithm hot score">hot ${hotScoreStr}</span>
          </div>
        </div>
      </article>
    `;
  }).join('');
}

function updatePagination(page, count) {
  const prevBtn = document.getElementById('btnPrevPage');
  const nextBtn = document.getElementById('btnNextPage');
  const pageIndicator = document.getElementById('pageIndicator');

  if (pageIndicator) pageIndicator.textContent = `Page ${page}`;
  if (prevBtn) prevBtn.disabled = (page <= 1);
  if (nextBtn) nextBtn.disabled = (count < pageSize);
}

// --- VOTING LOGIC ---
async function handleVote(lid, direction, btnElement) {
  if (!btnElement || btnElement.disabled) return;
  btnElement.disabled = true;

  try {
    const res = await fetch(`/links/${lid}/vote`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Voting failed: ${err.detail || 'Unknown error'}`);
      btnElement.disabled = false;
      return;
    }

    const data = await res.json();
    recordVote(lid);

    // Update active visual state
    btnElement.classList.add('voted');
    const svg = btnElement.querySelector('svg');
    if (svg) svg.setAttribute('fill', 'currentColor');

    const widget = document.getElementById(`vote-widget-${lid}`);
    if (widget) widget.classList.add('voted');

    const scoreElem = document.getElementById(`score-${lid}`);
    if (scoreElem) {
      scoreElem.textContent = data.score;
    }

    // If currently on home feed or in details view, refresh smoothly
    if (views.home && !views.home.classList.contains('hidden')) {
      // Re-fetch to recalculate ranking position accurately
      setTimeout(loadFrontPage, 250);
    } else if (currentLinkId === lid) {
      openLinkDetails(lid);
    }
  } catch (err) {
    alert(`Voting error: ${err.message}`);
    btnElement.disabled = false;
  }
}

// --- LINK DETAILS & THREADED COMMENTS ---
async function openLinkDetails(lid) {
  currentLinkId = lid;
  switchView('details');

  const detailCard = document.getElementById('linkDetailCard');
  const commentsTree = document.getElementById('commentsTreeContainer');

  detailCard.innerHTML = '<div class="feed-empty-state">Loading link details...</div>';
  commentsTree.innerHTML = '<div class="feed-empty-state">Loading discussion...</div>';

  try {
    const res = await fetch(`/links/${lid}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to load link details');
    }

    const data = await res.json();
    renderLinkDetailCard(data);
    renderCommentsTree(data.comments || []);
  } catch (err) {
    detailCard.innerHTML = `<div class="modal-error-alert">Error: ${escapeHtml(err.message)}</div>`;
    commentsTree.innerHTML = '';
  }
}

function renderLinkDetailCard(link) {
  const detailCard = document.getElementById('linkDetailCard');
  const domain = extractDomain(link.url);
  const hotScoreStr = typeof link.hot === 'number' ? link.hot.toFixed(2) : link.hot;
  const isVoted = votedLinks.has(Number(link.id));
  const commentCount = link.stats ? link.stats.comments : 0;

  detailCard.innerHTML = `
    <div class="vote-widget ${isVoted ? 'voted' : ''}" id="vote-widget-${link.id}">
      <button class="btn-vote-trigger ${isVoted ? 'voted' : ''}"
              onclick="handleVote(${link.id}, 'up', this)"
              title="Upvote">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="${isVoted ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2.5">
          <polyline points="18 15 12 9 6 15"></polyline>
        </svg>
      </button>
      <span class="vote-number" id="score-${link.id}">${link.score}</span>
    </div>

    <div class="detail-hero-content">
      <h1 class="detail-title">
        <a href="${link.url}" target="_blank" rel="noopener noreferrer">${escapeHtml(link.title)}</a>
        <span class="feed-domain">(${domain})</span>
      </h1>
      <div class="feed-meta-line">
        <span>Submitted by <strong class="meta-author">${escapeHtml(link.submitted_by || 'anonymous')}</strong></span>
        <span class="meta-sep">&bull;</span>
        <span>${commentCount} comments</span>
        <span class="meta-sep">&bull;</span>
        <span class="meta-hot-score">hot score ${hotScoreStr}</span>
      </div>
    </div>
  `;

  const countTitle = document.getElementById('commentsCountTitle');
  if (countTitle) countTitle.textContent = `Discussion (${commentCount})`;
}

function renderCommentsTree(tree) {
  const container = document.getElementById('commentsTreeContainer');
  if (!tree || tree.length === 0) {
    container.innerHTML = `
      <div class="feed-empty-state">
        <p>No comments yet. Start the conversation!</p>
      </div>
    `;
    return;
  }

  container.innerHTML = renderCommentNodes(tree, 1);
}

function renderCommentNodes(nodes, depth) {
  return nodes.map(node => {
    const formattedDate = node.created_at ? new Date(node.created_at).toLocaleDateString(undefined, {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    }) : '';
    const levelClass = `comment-level-${Math.min(depth, 5)}`;

    const repliesHtml = (node.replies && node.replies.length > 0)
      ? renderCommentNodes(node.replies, depth + 1)
      : '';

    const safeAuthor = escapeHtml(node.author || 'anonymous');
    const safeBody = escapeHtml(node.body);
    const cleanSnippet = node.body ? node.body.replace(/[\r\n\t]+/g, ' ').slice(0, 100) : '';
    const escapedForParam = escapeHtml(cleanSnippet).replace(/'/g, "\\'").replace(/"/g, '&quot;');

    return `
      <div class="comment-unit ${levelClass}" id="comment-${node.id}">
        <div class="comment-meta">
          <span class="comment-author-name">${safeAuthor}</span>
          <span class="meta-sep">&bull;</span>
          <span class="comment-timestamp">${formattedDate}</span>
        </div>
        <div class="comment-text">${safeBody}</div>
        <div class="comment-footer-actions">
          <button class="btn-comment-reply" onclick="openReplyModal(${node.id}, '${safeAuthor}', '${escapedForParam}')">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="15 14 20 9 15 4"></polyline>
              <path d="M4 20v-7a4 4 0 0 1 4-4h12"></path>
            </svg>
            <span>Reply</span>
          </button>
        </div>
      </div>
      ${repliesHtml}
    `;
  }).join('');
}

// --- ADD ROOT COMMENT ---
document.getElementById('formAddRootComment').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!currentLinkId) return;

  const btn = document.getElementById('btnSubmitRootComment');
  btn.disabled = true;

  const author = document.getElementById('rootCommentAuthor').value.trim();
  const body = document.getElementById('rootCommentBody').value.trim();

  try {
    const res = await fetch(`/links/${currentLinkId}/comments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        body,
        author: author || 'anonymous',
        parent_id: null
      })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to add comment');
    }

    document.getElementById('rootCommentBody').value = '';
    openLinkDetails(currentLinkId);
  } catch (err) {
    alert(`Error: ${err.message}`);
  } finally {
    btn.disabled = false;
  }
});

// --- REPLY MODAL ---
function openReplyModal(parentId, author, bodyText) {
  document.getElementById('replyParentId').value = parentId;
  const preview = document.getElementById('parentCommentPreview');
  if (preview) {
    preview.textContent = `Replying to ${author}: "${bodyText}"`;
  }
  document.getElementById('replyBody').value = '';
  document.getElementById('replyErrorAlert').classList.add('hidden');
  document.getElementById('replyModal').classList.remove('hidden');
  setTimeout(() => document.getElementById('replyBody').focus(), 50);
}

document.getElementById('btnCloseReplyModal').addEventListener('click', () => {
  document.getElementById('replyModal').classList.add('hidden');
});
document.getElementById('btnCancelReplyModal').addEventListener('click', () => {
  document.getElementById('replyModal').classList.add('hidden');
});

document.getElementById('formSubmitReply').addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!currentLinkId) return;

  const btn = document.getElementById('btnSubmitReply');
  const errAlert = document.getElementById('replyErrorAlert');
  btn.disabled = true;
  errAlert.classList.add('hidden');

  const parentId = parseInt(document.getElementById('replyParentId').value, 10);
  const author = document.getElementById('replyAuthor').value.trim();
  const body = document.getElementById('replyBody').value.trim();

  try {
    const res = await fetch(`/links/${currentLinkId}/comments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        body,
        author: author || 'anonymous',
        parent_id: parentId
      })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to submit reply');
    }

    document.getElementById('replyModal').classList.add('hidden');
    openLinkDetails(currentLinkId);
  } catch (err) {
    errAlert.textContent = err.message;
    errAlert.classList.remove('hidden');
  } finally {
    btn.disabled = false;
  }
});

// --- SUBMIT LINK MODAL ---
function openSubmitModal() {
  const modal = document.getElementById('submitModal');
  document.getElementById('formSubmitLink').reset();
  document.getElementById('submitErrorAlert').classList.add('hidden');
  modal.classList.remove('hidden');
  setTimeout(() => document.getElementById('submitTitle').focus(), 50);
}

document.getElementById('btnOpenSubmitModal').addEventListener('click', openSubmitModal);
const mobileSubmitBtn = document.getElementById('btnMobileSubmit');
if (mobileSubmitBtn) mobileSubmitBtn.addEventListener('click', openSubmitModal);

document.getElementById('btnCloseSubmitModal').addEventListener('click', () => {
  document.getElementById('submitModal').classList.add('hidden');
});
document.getElementById('btnCancelSubmitModal').addEventListener('click', () => {
  document.getElementById('submitModal').classList.add('hidden');
});

document.getElementById('formSubmitLink').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('btnSubmitLink');
  const errAlert = document.getElementById('submitErrorAlert');
  btn.disabled = true;
  errAlert.classList.add('hidden');

  const title = document.getElementById('submitTitle').value.trim();
  const url = document.getElementById('submitUrl').value.trim();
  const submittedBy = document.getElementById('submitAuthor').value.trim();

  try {
    const res = await fetch('/links', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title,
        url,
        submitted_by: submittedBy || 'anonymous'
      })
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to submit link');
    }

    document.getElementById('submitModal').classList.add('hidden');
    currentPage = 1;
    switchView('home');
  } catch (err) {
    errAlert.textContent = err.message;
    errAlert.classList.remove('hidden');
  } finally {
    btn.disabled = false;
  }
});

// --- RANKING EXPLANATION LOGIC ---
document.getElementById('formExplain').addEventListener('submit', async (e) => {
  e.preventDefault();
  runExplainComparison();
});

async function runExplainComparison() {
  const linkA = document.getElementById('explainLinkA').value;
  const linkB = document.getElementById('explainLinkB').value;
  const resultsContainer = document.getElementById('explainResults');

  resultsContainer.innerHTML = '<div class="feed-empty-state">Running mathematical comparison...</div>';

  try {
    const res = await fetch(`/ranking/explain?a=${linkA}&b=${linkB}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Failed to compare links');
    }

    const data = await res.json();
    renderExplainResults(data);
  } catch (err) {
    resultsContainer.innerHTML = `<div class="modal-error-alert">Error: ${escapeHtml(err.message)}</div>`;
  }
}

function renderExplainResults(data) {
  const container = document.getElementById('explainResults');

  const redditWinner = data.formulas.reddit.winner === 'a' ? 'Link A' : (data.formulas.reddit.winner === 'b' ? 'Link B' : 'Tie');
  const hnWinner = data.formulas.hn.winner === 'a' ? 'Link A' : (data.formulas.hn.winner === 'b' ? 'Link B' : 'Tie');

  container.innerHTML = `
    <div class="explain-summary-card">
      <div class="summary-headline">Comparison Target</div>
      <div class="summary-link-item"><strong>Link A:</strong> ${escapeHtml(data.a)}</div>
      <div class="summary-link-item"><strong>Link B:</strong> ${escapeHtml(data.b)}</div>
      <div class="callout-body mt-2" style="color: ${data.agree ? 'var(--status-connected)' : 'var(--accent)'};">
        <strong>Verdict:</strong> ${escapeHtml(data.note)}
      </div>
    </div>

    <div class="explain-grid">
      <div class="explain-formula-box">
        <div class="explain-box-title">
          <span>Reddit Formula</span>
          <span class="formula-winner-tag">Winner: ${redditWinner}</span>
        </div>
        <div class="explain-stat-row">
          <span>Score Link A:</span>
          <span class="explain-stat-val">${data.formulas.reddit.a}</span>
        </div>
        <div class="explain-stat-row">
          <span>Score Link B:</span>
          <span class="explain-stat-val">${data.formulas.reddit.b}</span>
        </div>
        <div class="explain-stat-row">
          <span>Margin:</span>
          <span class="explain-stat-val">${data.formulas.reddit.margin}</span>
        </div>
        <div class="explain-stat-row">
          <span>Order Stable:</span>
          <span class="explain-stat-val">${data.reddit_order_is_stable ? 'Yes (Fixed Order)' : 'No'}</span>
        </div>
      </div>

      <div class="explain-formula-box">
        <div class="explain-box-title">
          <span>Hacker News Formula</span>
          <span class="formula-winner-tag">Winner: ${hnWinner}</span>
        </div>
        <div class="explain-stat-row">
          <span>Score Link A:</span>
          <span class="explain-stat-val">${data.formulas.hn.a}</span>
        </div>
        <div class="explain-stat-row">
          <span>Score Link B:</span>
          <span class="explain-stat-val">${data.formulas.hn.b}</span>
        </div>
        <div class="explain-stat-row">
          <span>Margin:</span>
          <span class="explain-stat-val">${data.formulas.hn.margin}</span>
        </div>
        <div class="explain-stat-row">
          <span>Order Stable:</span>
          <span class="explain-stat-val">${data.hn_order_is_stable ? 'Yes' : 'No (Time-decaying)'}</span>
        </div>
      </div>
    </div>

    <div class="explain-summary-card">
      <div class="summary-headline">Vote Multiplier Equivalents</div>
      <p class="sidebar-copy">
        • 10x votes buys <strong>${(data.ten_times_the_votes_is_worth_seconds / 3600).toFixed(1)} hours</strong> (${data.ten_times_the_votes_is_worth_seconds}s) of staying power.
      </p>
      <p class="sidebar-copy mt-2">
        • 2x votes (doubling) buys <strong>${(data.doubling_the_votes_is_worth_seconds / 3600).toFixed(1)} hours</strong> (${data.doubling_the_votes_is_worth_seconds}s) of staying power.
      </p>
    </div>
  `;
}

// --- SYSTEM TELEMETRY / HEALTH ---
async function loadHealthStatus() {
  const badge = document.getElementById('overallStatusBadge');
  const dot = document.getElementById('overallStatusDot');
  const miniDot = document.getElementById('miniStatusDot');
  const miniText = document.getElementById('miniStatusText');
  const valApi = document.getElementById('valStatusAPI');
  const valPg = document.getElementById('valStatusPostgres');
  const valRedis = document.getElementById('valStatusRedis');

  if (badge) badge.textContent = 'Probing infrastructure...';
  if (valApi) valApi.textContent = 'Checking...';
  if (valPg) valPg.textContent = 'Checking...';
  if (valRedis) valRedis.textContent = 'Checking...';

  try {
    const res = await fetch('/health');
    const data = await res.json();

    if (valApi) {
      valApi.innerHTML = '<span class="service-badge-live">HTTP 200 OK</span>';
    }

    if (valPg) {
      if (data.postgres) {
        valPg.innerHTML = '<span class="service-badge-live">Connected</span>';
      } else {
        valPg.innerHTML = `<span class="service-badge-error">Disconnected (${escapeHtml(data.pg_error || 'Error')})</span>`;
      }
    }

    if (valRedis) {
      if (data.redis) {
        valRedis.innerHTML = '<span class="service-badge-live">Connected</span>';
      } else {
        valRedis.innerHTML = `<span class="service-badge-error">Disconnected (${escapeHtml(data.redis_error || 'Error')})</span>`;
      }
    }

    const allGood = res.ok && data.postgres && data.redis;

    if (badge) {
      badge.textContent = allGood ? 'All Systems Operational' : 'Degraded Connectivity';
    }
    if (dot) {
      dot.className = `status-pulse-large ${allGood ? '' : 'down'}`;
    }
    if (miniDot) {
      miniDot.style.backgroundColor = allGood ? 'var(--status-connected)' : 'var(--status-error)';
    }
    if (miniText) {
      miniText.textContent = allGood ? 'Systems live' : 'Degraded';
    }
  } catch (err) {
    if (valApi) valApi.innerHTML = `<span class="service-badge-error">Failed (${escapeHtml(err.message)})</span>`;
    if (valPg) valPg.innerHTML = '<span class="meta-time">Unavailable</span>';
    if (valRedis) valRedis.innerHTML = '<span class="meta-time">Unavailable</span>';
    if (badge) badge.textContent = 'API Unreachable';
    if (dot) dot.className = 'status-pulse-large down';
    if (miniDot) miniDot.style.backgroundColor = 'var(--status-error)';
    if (miniText) miniText.textContent = 'Offline';
  }
}

document.getElementById('btnRefreshHealth').addEventListener('click', loadHealthStatus);

// --- NAVIGATION HOOKS ---
document.getElementById('navHome').addEventListener('click', (e) => { e.preventDefault(); switchView('home'); });
const mobileBrand = document.getElementById('mobileBrandHome');
if (mobileBrand) mobileBrand.addEventListener('click', (e) => { e.preventDefault(); switchView('home'); });

document.getElementById('navBtnHome').addEventListener('click', () => switchView('home'));
document.getElementById('navBtnSubmit').addEventListener('click', openSubmitModal);
document.getElementById('navBtnExplain').addEventListener('click', () => {
  switchView('explain');
  runExplainComparison();
});
document.getElementById('navBtnStatus').addEventListener('click', () => switchView('status'));
document.getElementById('btnBackToHome').addEventListener('click', () => switchView('home'));

// Mobile navigation links
if (mobileNavBtns.home) mobileNavBtns.home.addEventListener('click', () => switchView('home'));
if (mobileNavBtns.submit) mobileNavBtns.submit.addEventListener('click', openSubmitModal);
if (mobileNavBtns.explain) mobileNavBtns.explain.addEventListener('click', () => {
  switchView('explain');
  runExplainComparison();
});
if (mobileNavBtns.status) mobileNavBtns.status.addEventListener('click', () => switchView('status'));

// Formula Toggles
document.getElementById('btnFormulaReddit').addEventListener('click', () => {
  if (currentFormula !== 'reddit') {
    currentFormula = 'reddit';
    currentPage = 1;
    document.getElementById('btnFormulaReddit').classList.add('active');
    document.getElementById('btnFormulaReddit').setAttribute('aria-selected', 'true');
    document.getElementById('btnFormulaHN').classList.remove('active');
    document.getElementById('btnFormulaHN').setAttribute('aria-selected', 'false');
    loadFrontPage();
  }
});

document.getElementById('btnFormulaHN').addEventListener('click', () => {
  if (currentFormula !== 'hn') {
    currentFormula = 'hn';
    currentPage = 1;
    document.getElementById('btnFormulaHN').classList.add('active');
    document.getElementById('btnFormulaHN').setAttribute('aria-selected', 'true');
    document.getElementById('btnFormulaReddit').classList.remove('active');
    document.getElementById('btnFormulaReddit').setAttribute('aria-selected', 'false');
    loadFrontPage();
  }
});

// Pagination Controls
document.getElementById('btnPrevPage').addEventListener('click', () => {
  if (currentPage > 1) {
    currentPage--;
    loadFrontPage();
  }
});

document.getElementById('btnNextPage').addEventListener('click', () => {
  currentPage++;
  loadFrontPage();
});

// XSS Prevention Helper
function escapeHtml(str) {
  if (typeof str !== 'string') return str;
  return str.replace(/[&<>"']/g, function(m) {
    return {
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#039;'
    }[m];
  });
}

// Initial Load
document.addEventListener('DOMContentLoaded', () => {
  loadFrontPage();
  loadHealthStatus();
});
