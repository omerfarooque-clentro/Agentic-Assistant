(() => {
  // Decode a JWT's exp claim without a library — used to refresh ahead of
  // expiry instead of waiting for a request to fail with 401 first.
  const decodeJwtExpiryMs = token => {
    try {
      const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
      return typeof payload.exp === 'number' ? payload.exp * 1000 : null;
    } catch { return null; }
  };
  const isExpiringSoon = (token, bufferMs = 15000) => {
    const expiry = token && decodeJwtExpiryMs(token);
    return expiry ? expiry - Date.now() < bufferMs : false;
  };

  // Multiple in-flight requests (e.g. loading threads + integrations at once,
  // or a background poll overlapping a send) can all see an expired/expiring
  // token at the same moment. Without de-duping, each one fires its own
  // refresh call; sharing a single in-flight promise means only one refresh
  // ever happens at a time and everyone else just awaits its result.
  let refreshInFlight = null;
  const refreshAccessToken = () => {
    if (refreshInFlight) return refreshInFlight;
    const refreshToken = localStorage.getItem('ops_refresh');
    if (!refreshToken) return Promise.resolve(false);
    refreshInFlight = (async () => {
      try {
        const response = await fetch('/api/token/refresh/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh: refreshToken })
        });
        if (!response.ok) return false;
        const data = await response.json();
        localStorage.setItem('ops_access', data.access);
        return true;
      } catch {
        return false;
      }
    })();
    return refreshInFlight.finally(() => { refreshInFlight = null; });
  };

  const initThemeToggle = () => {
    const toggleTheme = () => {
      const current = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      const next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      if (next === 'dark') {
        document.documentElement.classList.add('theme-dark');
      } else {
        document.documentElement.classList.remove('theme-dark');
      }
      try { localStorage.setItem('ops_theme', next); } catch (e) { }
    };

    const buttons = document.querySelectorAll('#theme-toggle, .theme-toggle-btn');
    buttons.forEach(btn => {
      btn.onclick = (e) => {
        e.preventDefault();
        toggleTheme();
      };
    });

    if (!window.__themeKeyBound) {
      window.__themeKeyBound = true;
      window.addEventListener('keydown', e => {
        if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === 'T' || e.key === 't')) {
          e.preventDefault();
          toggleTheme();
        }
      });
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initThemeToggle);
  } else {
    initThemeToggle();
  }

  const forceSignOut = () => {
    // Session can't be recovered — send the user back to sign in instead of
    // leaving the workspace showing a stale/blank thread list and history.
    // Preserve anything they were mid-typing so it isn't silently lost.
    const draftInput = document.querySelector('#message-input');
    if (draftInput && draftInput.value.trim()) sessionStorage.setItem('ops_draft', draftInput.value);
    localStorage.removeItem('ops_access');
    localStorage.removeItem('ops_refresh');
    localStorage.removeItem('ops_user');
    window.location = '/signin/';
  };

  const extractErrorMessage = data => {
    if (!data) return 'Something went wrong.';
    if (typeof data === 'string') return data;
    if (typeof data === 'object') {
      if (data.detail) return Array.isArray(data.detail) ? data.detail.join(' ') : String(data.detail);
      if (data.error) return Array.isArray(data.error) ? data.error.join(' ') : String(data.error);
      const values = [];
      Object.values(data).forEach(value => {
        if (Array.isArray(value)) values.push(...value.filter(Boolean).map(item => typeof item === 'string' ? item : String(item)));
        else if (value && typeof value === 'object') values.push(JSON.stringify(value));
        else if (value != null && value !== '') values.push(String(value));
      });
      if (values.length) return values.join(' ');
    }
    return 'Something went wrong.';
  };

  const fetchWithAuth = async (path, options = {}) => {
    const request = async () => {
      let token = localStorage.getItem('ops_access');
      if (token && !options.public && isExpiringSoon(token)) {
        await refreshAccessToken();
        token = localStorage.getItem('ops_access');
      }
      const headers = { ...(options.headers || {}) };
      if (!(options.body instanceof FormData) && !Object.prototype.hasOwnProperty.call(headers, 'Content-Type')) {
        headers['Content-Type'] = 'application/json';
      }
      if (token && !options.public) headers.Authorization = `Bearer ${token}`;
      return fetch(path, { ...options, headers });
    };

    let response = await request();
    if (response.status === 401 && !options.skipRefresh && !options.public) {
      const refreshed = await refreshAccessToken();
      if (refreshed) response = await request();
      if (response.status === 401) {
        forceSignOut();
        throw new Error('Session expired. Redirecting to sign in...');
      }
    }
    return response;
  };

  const api = async (path, options = {}) => {
    const response = await fetchWithAuth(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(extractErrorMessage(data));
    return data;
  };
  const formData = form => Object.fromEntries(new FormData(form).entries());
  const showError = error => { const target = document.querySelector('#form-error'); if (target) target.textContent = error.message; };

  const downloadTextFile = (filename, content) => {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const generateClientPassword = (length = 16) => {
    const lower = 'abcdefghijkmnopqrstuvwxyz';
    const upper = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
    const numbers = '23456789';
    const symbols = '!@#$%^&*';
    const all = lower + upper + numbers + symbols;
    let pwd = '';
    pwd += lower[Math.floor(Math.random() * lower.length)];
    pwd += upper[Math.floor(Math.random() * upper.length)];
    pwd += numbers[Math.floor(Math.random() * numbers.length)];
    pwd += symbols[Math.floor(Math.random() * symbols.length)];
    for (let i = 4; i < length; i++) {
      pwd += all[Math.floor(Math.random() * all.length)];
    }
    return pwd.split('').sort(() => 0.5 - Math.random()).join('');
  };


  const MAX_COMPOSER_HEIGHT = 200;
  // Grows the textarea to fit its content (up to a cap, then scrolls
  // internally) instead of staying a fixed one-line box with a scrollbar.
  const autoGrowTextarea = el => {
    if (!el) return;
    el.style.height = 'auto';
    const next = Math.min(el.scrollHeight, MAX_COMPOSER_HEIGHT);
    el.style.height = `${next}px`;
    el.style.overflowY = el.scrollHeight > MAX_COMPOSER_HEIGHT ? 'auto' : 'hidden';
  };
  const truncateText = (text, max) => (typeof text === 'string' && text.length > max ? `${text.slice(0, max)}…` : text);
  const safeText = value => (value === null || value === undefined ? '' : String(value));
  const cleanMessageContent = text => safeText(text).replace(/<suggested_title>[\s\S]*?(?:<\/suggested_title>|$)/gi, '').trim();
  const cleanRetiredCardMarkdown = (rawText, cardType) => {
    if (!rawText || typeof rawText !== 'string') return '';
    if (!['weather', 'email_list', 'slack_mentions'].includes(cardType)) return rawText;
    let cleaned = rawText;
    // Strip markdown tables (e.g. | Day | Condition | ... |)
    cleaned = cleaned.replace(/(?:^|\n)\s*\|[^\n]+\|(?:\n\s*\|[^\n]+\|)+/g, '\n');
    if (cardType === 'weather') {
      // Strip redundant metric bullet dumps
      cleaned = cleaned.replace(/(?:^|\n)\s*[-*]\s*\*{0,2}(?:Temperature|Condition|Humidity|Wind|Rain chance|Feels like)[\s\S]*?(?=\n\n|\n[^\s*-]|$)/gi, '');
      // Strip subheadings like "Current weather in..." or "7-day outlook"
      cleaned = cleaned.replace(/(?:^|\n)\s*#{1,6}\s*(?:Current weather|7[-‑]day outlook|7[-‑]day forecast)[^\n]*/gi, '');
      cleaned = cleaned.replace(/(?:^|\n)\s*\*{1,2}(?:Current weather|7[-‑]day outlook|7[-‑]day forecast)[^\n]*\*{1,2}/gi, '');
    }
    return cleaned.replace(/\n{3,}/g, '\n\n').trim();
  };

  const formatRelativeTime = dateInput => {
    if (!dateInput) return '';
    const date = new Date(dateInput);
    if (isNaN(date.getTime())) return '';

    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);

    if (diffSec < 0) return 'Just now';
    if (diffSec < 60) return diffSec <= 5 ? 'Just now' : `${diffSec}s ago`;
    if (diffMin < 60) return `${diffMin}m ago`;

    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const itemDay = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const dayDiff = Math.round((today - itemDay) / (1000 * 60 * 60 * 24));

    if (dayDiff === 0 || diffHour < 24) return `${diffHour}h ago`;
    if (dayDiff === 1) return 'Yesterday';
    if (dayDiff > 1 && dayDiff <= 7) return 'Last week';

    return new Intl.DateTimeFormat('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    }).format(date);
  };

  const APPROVAL_FIELD_LABELS = { to: 'To', subject: 'Subject', body: 'Body', channel: 'Channel', message: 'Message' };
  const SKIPPED_APPROVAL_KEYS = new Set(['type', 'tool_name', 'is_duplicate', 'domain', 'message', 'args']);
  const titleCase = key => key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

  const getDomainIconSvg = (domain, size = 16) => {
    const d = (domain || '').toLowerCase();
    if (d === 'email' || d === 'gmail') {
      return `<svg class="domain-icon domain-icon-gmail" width="${size}" height="${size}" viewBox="0 0 24 24"><path fill="#4285F4" d="M4 19h3V9.5L2 6v11.5c0 .8.7 1.5 1.5 1.5h.5z"/><path fill="#34A853" d="M20 19c.8 0 1.5-.7 1.5-1.5V6l-5 3.5V19h3.5z"/><path fill="#EA4335" d="M16.5 9.5V5c0-.8-.7-1.5-1.5-1.5H9c-.8 0-1.5.7-1.5 1.5v4.5l4.5 3.4 4.5-3.4z"/><path fill="#FBBC05" d="M2 6l5.5 4V5c0-.4.2-.8.5-1.1L2 6z"/><path fill="#C5221F" d="M22 6l-5.5 4V5c0-.4-.2-.8-.5-1.1L22 6z"/></svg>`;
    }
    if (d === 'calendar') {
      return `<svg class="domain-icon domain-icon-calendar" width="${size}" height="${size}" viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="17" height="17" rx="3" fill="#ffffff" stroke="#4285f4" stroke-width="1.5"/><path d="M3.5 6.5C3.5 4.8 4.8 3.5 6.5 3.5H17.5C19.2 3.5 20.5 4.8 20.5 6.5V8.5H3.5V6.5Z" fill="#4285f4"/><rect x="7" y="2" width="2" height="3" rx="1" fill="#1a73e8"/><rect x="15" y="2" width="2" height="3" rx="1" fill="#1a73e8"/><text x="12" y="17" font-size="8.5" font-weight="800" fill="#1a73e8" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif">31</text></svg>`;
    }
    if (d === 'docs' || d === 'doc') {
      return `<svg class="domain-icon domain-icon-docs" width="${size}" height="${size}" viewBox="0 0 24 24"><path fill="#4285F4" d="M14.5 2H6C4.9 2 4 2.9 4 4v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V7.5L14.5 2z"/><path fill="#A1C2FA" d="M14 2v6h6L14 2z"/><path fill="#FFFFFF" d="M7.5 11h9v1.5h-9zm0 3h9v1.5h-9zm0 3h6v1.5h-6z"/></svg>`;
    }
    if (d === 'sheets' || d === 'sheet') {
      return `<svg class="domain-icon domain-icon-sheets" width="${size}" height="${size}" viewBox="0 0 24 24"><path fill="#0F9D58" d="M14.5 2H6C4.9 2 4 2.9 4 4v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V7.5L14.5 2z"/><path fill="#87CEAB" d="M14 2v6h6L14 2z"/><path fill="#FFFFFF" d="M7 11h10v7H7zm1.5 1.5v1.5h3v-1.5zm4.5 0v1.5h2.5v-1.5zm-4.5 2.5v1.5h3V15zm4.5 0v1.5h2.5V15z"/></svg>`;
    }
    if (d === 'slack') {
      return `<svg class="domain-icon domain-icon-slack" width="${size}" height="${size}" viewBox="0 0 24 24"><path fill="#E01E5A" d="M5.04 14.5a2.5 2.5 0 1 0-2.5 2.5h2.5v-2.5zm1.25 0a2.5 2.5 0 0 0 5 0v-6.25a2.5 2.5 0 0 0-5 0v6.25z"/><path fill="#36C5F0" d="M9.5 5.04a2.5 2.5 0 1 0-2.5-2.5v2.5h2.5zm0 1.25a2.5 2.5 0 0 0 0 5h6.25a2.5 2.5 0 0 0 0-5H9.5z"/><path fill="#2EB67D" d="M18.96 9.5a2.5 2.5 0 1 0 2.5-2.5h-2.5v2.5zm-1.25 0a2.5 2.5 0 0 0-5 0v6.25a2.5 2.5 0 0 0 5 0V9.5z"/><path fill="#ECB22E" d="M14.5 18.96a2.5 2.5 0 1 0 2.5 2.5v-2.5h-2.5zm0-1.25a2.5 2.5 0 0 0 0-5H8.25a2.5 2.5 0 0 0 0 5H14.5z"/></svg>`;
    }
    if (d === 'research' || d === 'search') {
      return `<svg class="domain-icon domain-icon-research" width="${size}" height="${size}" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l3.66-2.85z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/></svg>`;
    }
    return `<svg class="domain-icon domain-icon-general" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none"><path d="M12 2L14.4 7.6L20 10L14.4 12.4L12 18L9.6 12.4L4 10L9.6 7.6L12 2Z" fill="#38bdf8"/><path d="M18 16L19.2 18.8L22 20L19.2 21.2L18 24L16.8 21.2L14 20L16.8 18.8L18 16Z" fill="#818cf8"/></svg>`;
  };

  const DOMAIN_META = {
    email: { icon: getDomainIconSvg('email', 15), label: 'Email' },
    gmail: { icon: getDomainIconSvg('gmail', 15), label: 'Email' },
    calendar: { icon: getDomainIconSvg('calendar', 15), label: 'Calendar' },
    docs: { icon: getDomainIconSvg('docs', 15), label: 'Docs' },
    sheets: { icon: getDomainIconSvg('sheets', 15), label: 'Sheets' },
    slack: { icon: getDomainIconSvg('slack', 15), label: 'Slack' },
    research: { icon: getDomainIconSvg('research', 15), label: 'Research' },
    general: { icon: getDomainIconSvg('general', 15), label: 'Operations' },
  };
  const escapeHtml = str => String(str).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  const GOOGLE_DOCS_ICON_SVG = `<svg class="doc-brand-icon" width="16" height="16" viewBox="0 0 24 24"><path fill="#4285F4" d="M14.5 2H6C4.9 2 4 2.9 4 4v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V7.5L14.5 2z"/><path fill="#A1C2FA" d="M14 2v6h6L14 2z"/><path fill="#FFFFFF" d="M7 11h10v1.6H7zm0 3.2h10v1.6H7zm0 3.2h6.5v1.6H7z"/></svg>`;

  const renderMarkdown = raw => {
    const source = String(raw == null ? '' : raw).replace(/\r\n/g, '\n');
    const inline = line => {
      let text = escapeHtml(line);
      // Markdown links: [title](url) -> open in new tab (or dedicated View in Doc button for Google Docs)
      text = text.replace(/\[([^\]]+?)\]\((https?:\/\/[^\s)]+)\)/g, (match, title, url) => {
        if (url.includes('docs.google.com/document/d/')) {
          return `<a class="btn-view-in-doc" href="${url}" target="_blank" rel="noopener noreferrer">${GOOGLE_DOCS_ICON_SVG}<span>View in Doc</span><span class="doc-btn-arrow">↗</span></a>`;
        }
        return `<a href="${url}" target="_blank" rel="noopener noreferrer">${title}</a>`;
      });
      text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
      text = text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
      // Auto-link standalone URLs not inside an href attribute
      text = text.replace(/(^|[\s(])(https?:\/\/[^\s<>"')]+)/g, (match, prefix, url) => {
        if (url.includes('docs.google.com/document/d/')) {
          return `${prefix}<a class="btn-view-in-doc" href="${url}" target="_blank" rel="noopener noreferrer">${GOOGLE_DOCS_ICON_SVG}<span>View in Doc</span><span class="doc-btn-arrow">↗</span></a>`;
        }
        return `${prefix}<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`;
      });
      return text;
    };

    let html = '';
    let listType = null;
    let codeBlockOpen = false;
    let codeBlockLang = '';
    let codeBlockContent = '';
    let inSlackFeed = false;
    const extractedSources = [];

    const closeList = () => { if (listType) { html += `</${listType}>`; listType = null; } };
    const closeSlackFeed = () => { if (inSlackFeed) { html += '</div>'; inSlackFeed = false; } };
    const closeCodeBlock = () => {
      if (codeBlockOpen) {
        const langClass = codeBlockLang ? ` class="language-${escapeHtml(codeBlockLang)}"` : '';
        html += `<pre><code${langClass}>${escapeHtml(codeBlockContent)}</code></pre>`;
        codeBlockOpen = false;
        codeBlockLang = '';
        codeBlockContent = '';
      }
    };

    // Extract citations / sources for footer cards
    const urlMatches = source.matchAll(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|(?:\b)(https?:\/\/[^\s<>"')]+)/g);
    for (const match of urlMatches) {
      const url = match[2] || match[3];
      const title = match[1] || url;
      if (url && !extractedSources.some(s => s.url === url)) {
        try {
          const domain = new URL(url).hostname.replace(/^www\./, '');
          extractedSources.push({ url, title, domain });
        } catch (e) {
          extractedSources.push({ url, title, domain: 'source' });
        }
      }
    }

    const lines = source.split('\n');
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];

      // Handle code fences
      if (line.match(/^```/)) {
        closeList();
        closeSlackFeed();
        if (codeBlockOpen) {
          closeCodeBlock();
        } else {
          const match = line.match(/^```(.*)$/);
          codeBlockLang = match ? match[1].trim() : '';
          codeBlockOpen = true;
          codeBlockContent = '';
          i++;
          while (i < lines.length && !lines[i].match(/^```/)) {
            codeBlockContent += (codeBlockContent ? '\n' : '') + lines[i];
            i++;
          }
          closeCodeBlock();
        }
        i++;
        continue;
      }

      // Handle tables (pipe-separated)
      if (line.includes('|') && (i + 1 < lines.length) && lines[i + 1].match(/^\s*\|?\s*[-:| ]+\|[-:| ]*$/)) {
        closeList();
        closeSlackFeed();
        closeCodeBlock();
        const parseRow = l => {
          let s = l.trim();
          if (s.startsWith('|')) s = s.slice(1);
          if (s.endsWith('|')) s = s.slice(0, -1);
          return s.split('|').map(c => c.trim());
        };
        const headerRow = parseRow(line);
        const tableRows = [headerRow];

        let j = i + 2;
        while (j < lines.length && lines[j].includes('|')) {
          const row = parseRow(lines[j]);
          if (row.length > 0 && row.some(cell => cell)) {
            tableRows.push(row);
          }
          j++;
        }

        if (tableRows.length > 1) {
          html += '<div class="table-wrapper"><table><thead><tr>';
          tableRows[0].forEach(cell => {
            html += `<th>${inline(cell)}</th>`;
          });
          html += '</tr></thead><tbody>';
          for (let k = 1; k < tableRows.length; k++) {
            html += '<tr>';
            for (let l = 0; l < tableRows[0].length; l++) {
              html += `<td>${inline(tableRows[k][l] || '')}</td>`;
            }
            html += '</tr>';
          }
          html += '</tbody></table></div>';
          i = j;
          continue;
        }
      }

      // Handle headings
      const headingMatch = line.match(/^(#{1,6})\s+(.*)$/);
      if (headingMatch) {
        closeList();
        closeSlackFeed();
        closeCodeBlock();
        const level = headingMatch[1].length;
        html += `<h${level}>${inline(headingMatch[2])}</h${level}>`;
        i++;
        continue;
      }

      // Handle blockquotes
      if (line.match(/^>\s/)) {
        closeList();
        closeSlackFeed();
        closeCodeBlock();
        html += '<blockquote>';
        while (i < lines.length && lines[i].match(/^>\s/)) {
          const quoteLine = lines[i].replace(/^>\s*/, '');
          html += `<p>${inline(quoteLine)}</p>`;
          i++;
        }
        html += '</blockquote>';
        continue;
      }

      // Handle horizontal rules
      if (line.match(/^\s*([-*_])\s*\1\s*\1[\s\1]*$/) || line.match(/^---+$/) || line.match(/^\*\*\*+$/)) {
        closeList();
        closeSlackFeed();
        closeCodeBlock();
        html += '<hr>';
        i++;
        continue;
      }

      // Handle Slack activity feed items (e.g. "*   **2026-08-31 21:48:45 PKT** (DM with Arsalan): 'Hello, Arsalan!'")
      const slackMatch = line.match(/^\s*[-*•▪▫]?\s*(?:\*\*)?(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s+[A-Z]{2,4})?)(?:\*\*)?\s+\(([^)]+)\):\s*["“']?([\s\S]+?)["”']?$/);
      if (slackMatch) {
        closeList();
        closeCodeBlock();
        if (!inSlackFeed) {
          html += '<div class="slack-activity-feed">';
          inSlackFeed = true;
        }
        const timeStr = escapeHtml(slackMatch[1].trim());
        const channelStr = escapeHtml(slackMatch[2].trim());
        const contentStr = inline(slackMatch[3].trim());
        html += `<div class="slack-activity-item"><div class="slack-activity-header"><span class="slack-channel-badge">${channelStr}</span><span class="slack-time-badge">${timeStr}</span></div><div class="slack-activity-body">${contentStr}</div></div>`;
        i++;
        continue;
      }

      // Handle bullet lists (supporting -, *, and unicode bullets •, ▪, ▫)
      const bullet = line.match(/^\s*[-*•▪▫]\s+(.*)/);
      if (bullet) {
        closeSlackFeed();
        closeCodeBlock();
        if (listType !== 'ul') { closeList(); html += '<ul>'; listType = 'ul'; }
        html += `<li>${inline(bullet[1])}</li>`;
        i++;
        continue;
      }

      // Handle numbered lists
      const numbered = line.match(/^\s*\d+\.\s+(.*)/);
      if (numbered) {
        closeSlackFeed();
        closeCodeBlock();
        if (listType !== 'ol') { closeList(); html += '<ol>'; listType = 'ol'; }
        html += `<li>${inline(numbered[1])}</li>`;
        i++;
        continue;
      }

      // Handle paragraphs
      closeSlackFeed();
      closeCodeBlock();
      if (!line.trim()) {
        closeList();
        html += '<br>';
      } else {
        closeList();
        html += `<p>${inline(line)}</p>`;
      }
      i++;
    }

    closeList();
    closeSlackFeed();
    closeCodeBlock();

    // Render interactive source / citation cards footer if sources were cited
    if (extractedSources.length > 0) {
      const chipsHtml = extractedSources.map(s => {
        const isDoc = s.url.includes('docs.google.com/document/d/');
        const icon = isDoc ? GOOGLE_DOCS_ICON_SVG : '<span class="source-icon">🔗</span>';
        const label = isDoc ? 'View in Doc' : escapeHtml(s.title.length > 30 ? s.title.slice(0, 30) + '…' : s.title);
        const extraClass = isDoc ? ' source-chip-doc' : '';
        return `
          <a class="source-chip${extraClass}" href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(s.title)}">
            ${icon}
            <span class="source-title">${label}</span>
            <span class="source-domain">${escapeHtml(s.domain)}</span>
            <span class="source-arrow">↗</span>
          </a>
        `;
      }).join('');
      html += `
        <div class="sources-container">
          <div class="sources-heading">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
            Sources & References
          </div>
          <div class="sources-grid">${chipsHtml}</div>
        </div>
      `;
    }

    return html;
  };

  const formatEventTime = value => {
    if (!value) return '';
    const date = new Date(value);
    if (isNaN(date)) return escapeHtml(value);
    return new Intl.DateTimeFormat([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(date);
  };

  const renderCalendarApproval = args => {
    const attendees = Array.isArray(args.attendees) ? args.attendees.map(a => (typeof a === 'string' ? a : a.email)).filter(Boolean) : [];
    const start = args.start_time && !isNaN(new Date(args.start_time)) ? new Date(args.start_time) : null;
    return `
      <div class="calendar-approval">
        <div class="calendar-date-badge">
          <span class="cal-month">${start ? new Intl.DateTimeFormat([], { month: 'short' }).format(start) : '—'}</span>
          <span class="cal-day">${start ? start.getDate() : '?'}</span>
        </div>
        <div class="calendar-details">
          <h4>${escapeHtml(args.summary || 'Untitled event')}</h4>
          <div class="calendar-time">${formatEventTime(args.start_time)}${args.end_time ? ' – ' + formatEventTime(args.end_time) : ''}</div>
          ${args.location ? `<div class="calendar-location">📍 ${escapeHtml(args.location)}</div>` : ''}
          ${attendees.length ? `<div class="calendar-attendees">${attendees.map(a => `<span class="chip">${escapeHtml(a)}</span>`).join('')}</div>` : ''}
          ${args.description ? `<p class="calendar-description">${escapeHtml(args.description)}</p>` : ''}
        </div>
      </div>`;
  };

  const renderEmailApproval = args => {
    const to = Array.isArray(args.to) ? args.to : (args.to ? [args.to] : []);
    return `
      <div class="email-approval">
        <div class="email-approval-row">
          <span class="email-approval-label">To</span>
          <span class="email-approval-value">${to.length ? to.map(a => `<span class="chip">${escapeHtml(a)}</span>`).join('') : '<span class="empty-value">—</span>'}</span>
        </div>
        ${args.subject ? `<div class="email-approval-subject">${escapeHtml(args.subject)}</div>` : ''}
        ${args.body ? `<div class="email-approval-body">${escapeHtml(truncateText(args.body, 800))}</div>` : ''}
      </div>`;
  };

  const renderSlackApproval = args => `
    <div class="slack-approval">
      ${args.channel ? `<span class="chip chip-slack">#${escapeHtml(String(args.channel).replace(/^#/, ''))}</span>` : ''}
      ${args.message ? `<div class="slack-approval-bubble">${escapeHtml(truncateText(args.message, 800))}</div>` : ''}
    </div>`;

  const renderDocsApproval = args => {
    const title = args.title || args.document_id || 'Google Document';
    const content = args.content || args.text || args.body || args.insert_text || '';
    return `
      <div class="docs-approval">
        <div class="docs-approval-meta">
          <span class="docs-chip">📄 ${escapeHtml(title)}</span>
          ${args.document_id && args.title ? `<span class="sheets-range-badge">ID: ${escapeHtml(truncateText(args.document_id, 20))}</span>` : ''}
        </div>
        ${content ? `<div class="docs-approval-body">${escapeHtml(truncateText(content, 800))}</div>` : ''}
      </div>`;
  };

  const renderSheetsApproval = args => {
    const name = args.spreadsheet_name || args.spreadsheet_id || 'Google Spreadsheet';
    const sheet = args.sheet_name || args.sheet || '';
    const range = args.range || '';
    const values = args.values || args.rows || args.data || '';
    let valDisplay = '';
    if (Array.isArray(values)) {
      valDisplay = values.map(row => (Array.isArray(row) ? row.join(' | ') : stringifyApprovalValue(row))).join('\n');
    } else if (values) {
      valDisplay = stringifyApprovalValue(values);
    }
    return `
      <div class="sheets-approval">
        <div class="sheets-approval-meta">
          <span class="sheets-chip">📊 ${escapeHtml(name)}</span>
          ${sheet ? `<span class="sheets-range-badge">Tab: ${escapeHtml(sheet)}</span>` : ''}
          ${range ? `<span class="sheets-range-badge">Range: ${escapeHtml(range)}</span>` : ''}
        </div>
        ${valDisplay ? `<div class="sheets-approval-body">${escapeHtml(truncateText(valDisplay, 800))}</div>` : ''}
      </div>`;
  };

  // Docs/Sheets and any tool we don't have a dedicated layout for fall back
  // to a generic field list — but values can be objects/arrays (not just
  // strings) or very long doc/cell content, so stringify and cap them
  // instead of letting a giant or "[object Object]" value break the card.
  const stringifyApprovalValue = value => {
    if (Array.isArray(value)) return value.map(v => (v && typeof v === 'object' ? JSON.stringify(v) : String(v))).join(', ');
    if (value && typeof value === 'object') return JSON.stringify(value);
    return String(value);
  };

  const renderApprovalBody = approval => {
    const args = approval.args || {};
    if (approval.domain === 'calendar' && (args.summary || args.start_time)) return renderCalendarApproval(args);
    if (approval.domain === 'email' && (args.to || args.subject || args.body)) return renderEmailApproval(args);
    if (approval.domain === 'slack' && (args.channel || args.message)) return renderSlackApproval(args);
    if (approval.domain === 'docs' && (args.title || args.content || args.text || args.document_id)) return renderDocsApproval(args);
    if (approval.domain === 'sheets' && (args.spreadsheet_id || args.values || args.rows || args.range)) return renderSheetsApproval(args);
    const fields = Object.entries(args)
      .filter(([key, value]) => value != null && value !== '' && !SKIPPED_APPROVAL_KEYS.has(key))
      .map(([key, value]) => `<dt>${APPROVAL_FIELD_LABELS[key] || titleCase(key)}</dt><dd>${escapeHtml(truncateText(stringifyApprovalValue(value), 600))}</dd>`)
      .join('');
    return fields ? `<dl class="approval-fields">${fields}</dl>` : '';
  };

  const renderApprovalEditFields = approval => {
    const domain = (approval && approval.domain) || 'general';
    const args = (approval && approval.args) || {};
    let fieldsHtml = '';

    if (domain === 'email') {
      const toVal = Array.isArray(args.to) ? args.to.join(', ') : (args.to || '');
      fieldsHtml = `
        <div class="approval-field-group">
          <label>Recipient(s)</label>
          <input type="text" class="approval-edit-input" data-arg-key="to" data-original-val="${escapeHtml(toVal)}" value="${escapeHtml(toVal)}" placeholder="recipient@example.com">
        </div>
        <div class="approval-field-group">
          <label>Subject</label>
          <input type="text" class="approval-edit-input" data-arg-key="subject" data-original-val="${escapeHtml(args.subject || '')}" value="${escapeHtml(args.subject || '')}" placeholder="Email subject">
        </div>
        <div class="approval-field-group">
          <label>Body</label>
          <textarea class="approval-edit-textarea" data-arg-key="body" data-original-val="${escapeHtml(args.body || '')}" rows="4" placeholder="Email body content">${escapeHtml(args.body || '')}</textarea>
        </div>`;
    } else if (domain === 'slack') {
      fieldsHtml = `
        <div class="approval-field-group">
          <label>Channel / User</label>
          <input type="text" class="approval-edit-input" data-arg-key="channel" data-original-val="${escapeHtml(args.channel || '')}" value="${escapeHtml(args.channel || '')}" placeholder="#general">
        </div>
        <div class="approval-field-group">
          <label>Message</label>
          <textarea class="approval-edit-textarea" data-arg-key="message" data-original-val="${escapeHtml(args.message || '')}" rows="3" placeholder="Message to send">${escapeHtml(args.message || '')}</textarea>
        </div>`;
    } else if (domain === 'calendar') {
      const attendeesVal = Array.isArray(args.attendees) ? args.attendees.map(a => (typeof a === 'string' ? a : a.email || '')).join(', ') : (args.attendees || '');
      fieldsHtml = `
        <div class="approval-field-group">
          <label>Event Title</label>
          <input type="text" class="approval-edit-input" data-arg-key="summary" data-original-val="${escapeHtml(args.summary || '')}" value="${escapeHtml(args.summary || '')}" placeholder="Meeting title">
        </div>
        <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 8px;">
          <div class="approval-field-group">
            <label>Start Time</label>
            <input type="text" class="approval-edit-input" data-arg-key="start_time" data-original-val="${escapeHtml(args.start_time || '')}" value="${escapeHtml(args.start_time || '')}" placeholder="YYYY-MM-DDTHH:MM:SS">
          </div>
          <div class="approval-field-group">
            <label>End Time</label>
            <input type="text" class="approval-edit-input" data-arg-key="end_time" data-original-val="${escapeHtml(args.end_time || '')}" value="${escapeHtml(args.end_time || '')}" placeholder="YYYY-MM-DDTHH:MM:SS">
          </div>
        </div>
        <div class="approval-field-group">
          <label>Location</label>
          <input type="text" class="approval-edit-input" data-arg-key="location" data-original-val="${escapeHtml(args.location || '')}" value="${escapeHtml(args.location || '')}" placeholder="Meeting link or location">
        </div>
        <div class="approval-field-group">
          <label>Attendees</label>
          <input type="text" class="approval-edit-input" data-arg-key="attendees" data-original-val="${escapeHtml(attendeesVal)}" value="${escapeHtml(attendeesVal)}" placeholder="alice@example.com, bob@example.com">
        </div>
        <div class="approval-field-group">
          <label>Description</label>
          <textarea class="approval-edit-textarea" data-arg-key="description" data-original-val="${escapeHtml(args.description || '')}" rows="2" placeholder="Event notes">${escapeHtml(args.description || '')}</textarea>
        </div>`;
    } else if (domain === 'docs') {
      const content = args.content || args.text || args.body || args.insert_text || '';
      fieldsHtml = `
        <div class="approval-field-group">
          <label>Document Title</label>
          <input type="text" class="approval-edit-input" data-arg-key="title" data-original-val="${escapeHtml(args.title || '')}" value="${escapeHtml(args.title || '')}" placeholder="Document Title">
        </div>
        <div class="approval-field-group">
          <label>Content</label>
          <textarea class="approval-edit-textarea" data-arg-key="content" data-original-val="${escapeHtml(content)}" rows="4" placeholder="Document content">${escapeHtml(content)}</textarea>
        </div>`;
    } else {
      const entries = Object.entries(args).filter(([key]) => !SKIPPED_APPROVAL_KEYS.has(key));
      fieldsHtml = entries.map(([key, val]) => {
        const strVal = stringifyApprovalValue(val);
        return `
          <div class="approval-field-group">
            <label>${APPROVAL_FIELD_LABELS[key] || titleCase(key)}</label>
            <input type="text" class="approval-edit-input" data-arg-key="${escapeHtml(key)}" data-original-val="${escapeHtml(strVal)}" value="${escapeHtml(strVal)}">
          </div>`;
      }).join('');
    }

    return fieldsHtml;
  };

  window.PersonalOps = {
    renderMarkdown,
    bindTheme: initThemeToggle,
    bindLogin() {
      initThemeToggle();
      const form = document.querySelector('#login-form');
      if (!form) return;
      form.addEventListener('submit', async event => {
        event.preventDefault();
        localStorage.removeItem('ops_access');
        localStorage.removeItem('ops_refresh');
        localStorage.removeItem('ops_user');
        const submitBtn = form.querySelector('#btn-login-submit') || form.querySelector('button[type="submit"]');
        const progressEl = document.querySelector('#login-progress');
        const errTarget = document.querySelector('#form-error');
        if (errTarget) errTarget.textContent = '';
        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.classList.add('is-loading');
        }
        if (progressEl) progressEl.classList.remove('hidden');
        try {
          const data = await api('/login/', { method: 'POST', body: JSON.stringify(formData(event.currentTarget)), public: true, skipRefresh: true });
          localStorage.setItem('ops_access', data.access);
          localStorage.setItem('ops_refresh', data.refresh || data.refersh);
          localStorage.setItem('ops_user', data.username);
          window.location = '/';
        } catch (error) {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.classList.remove('is-loading');
          }
          if (progressEl) progressEl.classList.add('hidden');
          showError(error);
        }
      });
    },
    bindRegister() {
      initThemeToggle();
      const regForm = document.querySelector('#register-form');
      if (!regForm) return;

      const pwdInput = document.querySelector('#register-password');
      const genBtn = document.querySelector('#btn-generate-password');
      const toggleBtn = document.querySelector('#btn-toggle-password');

      if (genBtn && pwdInput) {
        genBtn.addEventListener('click', () => {
          const generated = generateClientPassword();
          pwdInput.value = generated;
          pwdInput.type = 'text';
          if (toggleBtn) toggleBtn.textContent = '🙈';
        });
      }

      if (toggleBtn && pwdInput) {
        toggleBtn.addEventListener('click', () => {
          if (pwdInput.type === 'password') {
            pwdInput.type = 'text';
            toggleBtn.textContent = '🙈';
          } else {
            pwdInput.type = 'password';
            toggleBtn.textContent = '👁️';
          }
        });
      }

      regForm.addEventListener('submit', async event => {
        event.preventDefault();
        const errTarget = document.querySelector('#form-error');
        if (errTarget) errTarget.textContent = '';

        const submitBtn = regForm.querySelector('button[type="submit"]');
        const progressEl = document.querySelector('#register-progress');
        const formInputs = regForm.querySelectorAll('input, button');
        const originalBtnHtml = submitBtn ? submitBtn.innerHTML : 'Start workspace <span>-></span>';

        // Extract form data BEFORE disabling inputs, otherwise FormData omits disabled elements!
        const payload = formData(event.currentTarget);

        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.classList.add('is-loading');
          submitBtn.innerHTML = '<span class="btn-spinner"></span> Creating workspace… <span>⏳</span>';
        }
        if (progressEl) progressEl.classList.remove('hidden');
        formInputs.forEach(el => { if (el !== submitBtn) el.disabled = true; });

        try {
          const data = await api('/registration/', {
            method: 'POST',
            body: JSON.stringify(payload),
            public: true,
            skipRefresh: true
          });

          const stepForm = document.querySelector('#register-step-form');
          const stepCreds = document.querySelector('#register-step-credentials');

          if (stepCreds && stepForm) {
            stepForm.classList.add('hidden');
            stepCreds.classList.remove('hidden');

            const usernameEl = document.querySelector('#cred-username');
            const emailEl = document.querySelector('#cred-email');
            const codeEl = document.querySelector('#cred-recovery-code');

            if (usernameEl) usernameEl.textContent = data.username || '';
            if (emailEl) emailEl.textContent = data.email || '';
            if (codeEl) codeEl.textContent = data.recovery_code || 'PO-SAVED';

            const copyBtn = document.querySelector('#btn-copy-code');
            if (copyBtn) {
              copyBtn.onclick = async () => {
                if (data.recovery_code) {
                  await navigator.clipboard.writeText(data.recovery_code).catch(() => { });
                  copyBtn.textContent = 'Copied!';
                  setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
                }
              };
            }

            const downloadBtn = document.querySelector('#btn-download-creds');
            if (downloadBtn) {
              downloadBtn.onclick = () => {
                const now = new Date().toISOString();
                const fileContent = `====================================================\nPERSONAL OPS - WORKSPACE RECOVERY CREDENTIALS\n====================================================\nUsername:               ${data.username || ''}\nEmail:                  ${data.email || ''}\nEmergency Recovery OTP: ${data.recovery_code || ''}\nGenerated At:           ${now}\n====================================================\nIMPORTANT: Keep this file secure. If you ever forget\nyour password, this recovery OTP is required to restore\naccess and force-generate new credentials.\n====================================================\n`;
                downloadTextFile(`personal-ops-credentials-${data.username || 'user'}.txt`, fileContent);
              };
            }
          } else {
            window.location = '/signin/';
          }
        } catch (error) {
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.classList.remove('is-loading');
            submitBtn.innerHTML = originalBtnHtml;
          }
          if (progressEl) progressEl.classList.add('hidden');
          formInputs.forEach(el => el.disabled = false);
          showError(error);
        }
      });
    },
    bindResetPassword() {
      initThemeToggle();
      let recoveryEmail = '';
      let recoveryOtp = '';

      // Stage 1: Email Form
      const formEmail = document.querySelector('#form-forgot-email');
      if (formEmail) {
        formEmail.addEventListener('submit', async event => {
          event.preventDefault();
          const errEl = document.querySelector('#forgot-error');
          if (errEl) errEl.textContent = '';

          const emailInput = document.querySelector('#input-recovery-email');
          const submitBtn = formEmail.querySelector('button[type="submit"]');
          const originalBtnHtml = submitBtn ? submitBtn.innerHTML : 'Continue to Recovery OTP <span>-></span>';
          recoveryEmail = emailInput ? emailInput.value.trim() : '';

          if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.classList.add('is-loading');
            submitBtn.innerHTML = '<span class="btn-spinner"></span> Checking account… <span>⏳</span>';
          }
          if (emailInput) emailInput.disabled = true;

          try {
            await api('/api/auth/forgot-password/', {
              method: 'POST',
              body: JSON.stringify({ email: recoveryEmail }),
              public: true,
              skipRefresh: true
            });

            document.querySelector('#stage-email').classList.add('hidden');
            document.querySelector('#stage-otp').classList.remove('hidden');
            const displayEmail = document.querySelector('#display-recovery-email');
            if (displayEmail) displayEmail.value = recoveryEmail;
          } catch (error) {
            if (submitBtn) {
              submitBtn.disabled = false;
              submitBtn.classList.remove('is-loading');
              submitBtn.innerHTML = originalBtnHtml;
            }
            if (emailInput) emailInput.disabled = false;
            if (errEl) errEl.textContent = error.message;
          }
        });
      }

      // Back to Email button
      const backBtn = document.querySelector('#btn-back-to-email');
      if (backBtn) {
        backBtn.addEventListener('click', () => {
          document.querySelector('#stage-otp').classList.add('hidden');
          document.querySelector('#stage-email').classList.remove('hidden');
        });
      }

      // Stage 2: Verify OTP Form
      const formOtp = document.querySelector('#form-verify-otp');
      if (formOtp) {
        formOtp.addEventListener('submit', async event => {
          event.preventDefault();
          const errEl = document.querySelector('#otp-error');
          if (errEl) errEl.textContent = '';

          const otpInput = document.querySelector('#input-recovery-otp');
          const submitBtn = formOtp.querySelector('button[type="submit"]');
          const originalBtnHtml = submitBtn ? submitBtn.innerHTML : 'Verify OTP <span>-></span>';
          recoveryOtp = otpInput ? otpInput.value.trim() : '';

          if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.classList.add('is-loading');
            submitBtn.innerHTML = '<span class="btn-spinner"></span> Verifying OTP… <span>⏳</span>';
          }
          if (otpInput) otpInput.disabled = true;

          try {
            await api('/api/auth/verify-otp/', {
              method: 'POST',
              body: JSON.stringify({ email: recoveryEmail, otp: recoveryOtp }),
              public: true,
              skipRefresh: true
            });

            document.querySelector('#stage-otp').classList.add('hidden');
            document.querySelector('#stage-password').classList.remove('hidden');
          } catch (error) {
            if (submitBtn) {
              submitBtn.disabled = false;
              submitBtn.classList.remove('is-loading');
              submitBtn.innerHTML = originalBtnHtml;
            }
            if (otpInput) otpInput.disabled = false;
            if (errEl) errEl.textContent = error.message;
          }
        });
      }

      // Stage 3: New Password Form
      const formReset = document.querySelector('#form-reset-password');
      if (formReset) {
        const pwdInput = document.querySelector('#input-new-password');
        const confirmInput = document.querySelector('#input-confirm-password');
        const genBtn = document.querySelector('#btn-generate-reset-password');
        const toggleBtn = document.querySelector('#btn-toggle-new-password');
        const submitBtn = formReset.querySelector('button[type="submit"]');
        const originalBtnHtml = submitBtn ? submitBtn.innerHTML : 'Update Password & Rotate OTP <span>-></span>';

        if (genBtn && pwdInput && confirmInput) {
          genBtn.addEventListener('click', () => {
            const generated = generateClientPassword();
            pwdInput.value = generated;
            confirmInput.value = generated;
            pwdInput.type = 'text';
            if (toggleBtn) toggleBtn.textContent = '🙈';
          });
        }

        if (toggleBtn && pwdInput) {
          toggleBtn.addEventListener('click', () => {
            if (pwdInput.type === 'password') {
              pwdInput.type = 'text';
              toggleBtn.textContent = '🙈';
            } else {
              pwdInput.type = 'password';
              toggleBtn.textContent = '👁️';
            }
          });
        }

        formReset.addEventListener('submit', async event => {
          event.preventDefault();
          const errEl = document.querySelector('#reset-error');
          if (errEl) errEl.textContent = '';

          const newPassword = pwdInput ? pwdInput.value : '';
          const confirmPassword = confirmInput ? confirmInput.value : '';

          if (newPassword !== confirmPassword) {
            if (errEl) errEl.textContent = 'Passwords do not match.';
            return;
          }

          if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.classList.add('is-loading');
            submitBtn.innerHTML = '<span class="btn-spinner"></span> Updating password & rotating OTP… <span>⏳</span>';
          }
          if (pwdInput) pwdInput.disabled = true;
          if (confirmInput) confirmInput.disabled = true;

          try {
            const data = await api('/api/auth/reset-password/', {
              method: 'POST',
              body: JSON.stringify({
                email: recoveryEmail,
                otp: recoveryOtp,
                new_password: newPassword,
                confirm_password: confirmPassword
              }),
              public: true,
              skipRefresh: true
            });

            document.querySelector('#stage-password').classList.add('hidden');
            document.querySelector('#stage-success').classList.remove('hidden');

            const newEmailEl = document.querySelector('#new-cred-email');
            const newCodeEl = document.querySelector('#new-cred-recovery-code');

            if (newEmailEl) newEmailEl.textContent = data.email || recoveryEmail;
            if (newCodeEl) newCodeEl.textContent = data.recovery_code || '';

            const copyNewBtn = document.querySelector('#btn-copy-new-code');
            if (copyNewBtn) {
              copyNewBtn.onclick = async () => {
                if (data.recovery_code) {
                  await navigator.clipboard.writeText(data.recovery_code).catch(() => { });
                  copyNewBtn.textContent = 'Copied!';
                  setTimeout(() => { copyNewBtn.textContent = 'Copy'; }, 2000);
                }
              };
            }

            const downloadNewBtn = document.querySelector('#btn-download-new-creds');
            if (downloadNewBtn) {
              downloadNewBtn.onclick = () => {
                const now = new Date().toISOString();
                const fileContent = `====================================================\nPERSONAL OPS - UPDATED RECOVERY CREDENTIALS\n====================================================\nUsername:            ${data.username || ''}\nEmail:               ${data.email || recoveryEmail}\nNEW Recovery OTP/Key: ${data.recovery_code || ''}\nReset At:            ${now}\n====================================================\nNOTICE: Your previous recovery credentials have been\ninvalidated. Please keep this file in a secure place.\n====================================================\n`;
                downloadTextFile(`personal-ops-updated-credentials-${data.username || 'user'}.txt`, fileContent);
              };
            }
          } catch (error) {
            if (errEl) errEl.textContent = error.message;
          }
        });
      }
    },
    initSettings() {
      initThemeToggle();
      if (!localStorage.getItem('ops_access')) { window.location = '/signin/'; return; }
      const errorTarget = document.querySelector('#settings-error');
      const setError = error => { errorTarget.textContent = error.message; };
      const updateCard = (service, connected) => {
        const card = document.querySelector(`[data-account="${service}"]`);
        if (!card) return;
        const stateEl = card.querySelector('.account-state');
        stateEl.textContent = connected ? 'Connected' : 'Not connected';
        stateEl.classList.toggle('connected', connected);
        card.querySelector('.connect-account').classList.toggle('hidden', connected);
        card.querySelector('.revoke-account').classList.toggle('hidden', !connected);
      };
      const loadStatus = async () => {
        const data = await api('/api/integrations/status/');
        const connected = new Set((data.integrations || []).filter(item => item.enabled).map(item => item.service));
        ['gmail', 'calendar', 'docs', 'sheets', 'slack'].forEach(service => updateCard(service, connected.has(service)));
      };
      document.querySelectorAll('.connect-account').forEach(button => button.onclick = async () => {
        try { const data = await api(`/api/integrations/${button.dataset.service}/connect/`); window.location.href = data.authorization_url; } catch (error) { setError(error); }
      });
      document.querySelectorAll('.revoke-account').forEach(button => button.onclick = async () => {
        try { await api(`/api/integrations/${button.dataset.service}/disconnect/`, { method: 'POST' }); updateCard(button.dataset.service, false); } catch (error) { setError(error); }
      });
      document.querySelector('#settings-logout').onclick = () => { localStorage.clear(); window.location = '/signin/'; };
      loadStatus().catch(setError);

      // --- Security: Rotate Recovery OTP ---
      const formRotateOtp = document.querySelector('#form-settings-rotate-otp');
      if (formRotateOtp) {
        formRotateOtp.addEventListener('submit', async event => {
          event.preventDefault();
          const errEl = document.querySelector('#rotate-otp-error');
          if (errEl) errEl.textContent = '';
          const pwdInput = document.querySelector('#input-rotate-otp-password');
          const submitBtn = formRotateOtp.querySelector('button[type="submit"]');
          const progressEl = document.querySelector('#rotate-otp-progress');
          const originalBtnHtml = submitBtn ? submitBtn.innerHTML : 'Generate New Recovery Code <span>→</span>';
          const password = pwdInput ? pwdInput.value : '';

          if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.classList.add('is-loading');
            submitBtn.innerHTML = '<span class="btn-spinner"></span> Generating Key… <span>⏳</span>';
          }
          if (pwdInput) pwdInput.disabled = true;
          if (progressEl) progressEl.classList.remove('hidden');

          try {
            const data = await api('/api/auth/otp-generate/', {
              method: 'POST',
              body: JSON.stringify({ password })
            });

            if (pwdInput) {
              pwdInput.value = '';
              pwdInput.disabled = false;
            }
            if (submitBtn) {
              submitBtn.disabled = false;
              submitBtn.classList.remove('is-loading');
              submitBtn.innerHTML = originalBtnHtml;
            }
            if (progressEl) progressEl.classList.add('hidden');

            const resultBox = document.querySelector('#settings-otp-result');
            if (resultBox) {
              resultBox.classList.remove('hidden');
              resultBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }

            const codeEl = document.querySelector('#settings-new-code');
            if (codeEl) codeEl.textContent = data.recovery_code || '';

            const copyBtn = document.querySelector('#btn-settings-copy-code');
            if (copyBtn) {
              copyBtn.onclick = async () => {
                if (data.recovery_code) {
                  await navigator.clipboard.writeText(data.recovery_code).catch(() => { });
                  copyBtn.textContent = 'Copied!';
                  setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
                }
              };
            }

            const downloadBtn = document.querySelector('#btn-settings-download-code');
            if (downloadBtn) {
              downloadBtn.onclick = () => {
                const now = new Date().toISOString();
                const content = `====================================================\nPERSONAL OPS - UPDATED EMERGENCY RECOVERY CODE\n====================================================\nUsername:               ${data.username || localStorage.getItem('ops_user') || ''}\nEmail:                  ${data.email || ''}\nNEW Emergency Recovery: ${data.recovery_code || ''}\nRotated At:             ${now}\nStatus:                 ACTIVE ✅ (Previous code is INVALID ❌)\n====================================================\nIMPORTANT: Keep this recovery key in a safe place.\n====================================================\n`;
                downloadTextFile(`personal-ops-recovery-key-${data.username || 'user'}.txt`, content);
              };
            }
          } catch (error) {
            if (pwdInput) pwdInput.disabled = false;
            if (submitBtn) {
              submitBtn.disabled = false;
              submitBtn.classList.remove('is-loading');
              submitBtn.innerHTML = originalBtnHtml;
            }
            if (progressEl) progressEl.classList.add('hidden');
            if (errEl) errEl.textContent = error.message;
          }
        });
      }

      // --- Security: Change Password ---
      const formChangePass = document.querySelector('#form-settings-change-password');
      if (formChangePass) {
        const radioPass = document.querySelector('#radio-method-pass');
        const radioOtp = document.querySelector('#radio-method-otp');
        const fieldPass = document.querySelector('#field-current-password');
        const fieldOtp = document.querySelector('#field-recovery-otp');
        const curPassInput = document.querySelector('#input-change-cur-pass');
        const curOtpInput = document.querySelector('#input-change-cur-otp');
        const newPassInput = document.querySelector('#input-change-new-pass');
        const confirmPassInput = document.querySelector('#input-change-confirm-pass');
        const genBtn = document.querySelector('#btn-gen-settings-pass');
        const successEl = document.querySelector('#change-pass-success');
        const errEl = document.querySelector('#change-pass-error');
        const submitBtn = formChangePass.querySelector('button[type="submit"]');
        const progressEl = document.querySelector('#change-pass-progress');
        const originalBtnHtml = submitBtn ? submitBtn.innerHTML : 'Update Password <span>→</span>';

        if (radioPass && radioOtp) {
          radioPass.addEventListener('change', () => {
            fieldPass.classList.remove('hidden');
            fieldOtp.classList.add('hidden');
          });
          radioOtp.addEventListener('change', () => {
            fieldPass.classList.add('hidden');
            fieldOtp.classList.remove('hidden');
          });
        }

        if (genBtn && newPassInput && confirmPassInput) {
          genBtn.addEventListener('click', () => {
            const generated = generateClientPassword();
            newPassInput.value = generated;
            confirmPassInput.value = generated;
            newPassInput.type = 'text';
            confirmPassInput.type = 'text';
          });
        }

        formChangePass.addEventListener('submit', async event => {
          event.preventDefault();
          if (errEl) errEl.textContent = '';
          if (successEl) { successEl.textContent = ''; successEl.classList.add('hidden'); }

          const isUsingOtp = radioOtp && radioOtp.checked;
          const currentPassword = curPassInput ? curPassInput.value : '';
          const otp = curOtpInput ? curOtpInput.value.trim() : '';
          const newPassword = newPassInput ? newPassInput.value : '';
          const confirmPassword = confirmPassInput ? confirmPassInput.value : '';

          if (newPassword !== confirmPassword) {
            if (errEl) errEl.textContent = 'Passwords do not match.';
            return;
          }

          if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.classList.add('is-loading');
            submitBtn.innerHTML = '<span class="btn-spinner"></span> Updating… <span>⏳</span>';
          }
          if (progressEl) progressEl.classList.remove('hidden');

          try {
            const data = await api('/api/auth/change-password/', {
              method: 'POST',
              body: JSON.stringify({
                current_password: isUsingOtp ? '' : currentPassword,
                otp: isUsingOtp ? otp : '',
                new_password: newPassword,
                confirm_password: confirmPassword
              })
            });

            if (curPassInput) curPassInput.value = '';
            if (curOtpInput) curOtpInput.value = '';
            if (newPassInput) newPassInput.value = '';
            if (confirmPassInput) confirmPassInput.value = '';

            if (submitBtn) {
              submitBtn.disabled = false;
              submitBtn.classList.remove('is-loading');
              submitBtn.innerHTML = originalBtnHtml;
            }
            if (progressEl) progressEl.classList.add('hidden');

            if (successEl) {
              successEl.textContent = data.detail || 'Password updated successfully.';
              successEl.classList.remove('hidden');
            }

            if (data.rotated_otp && data.recovery_code) {
              const rotatedBox = document.querySelector('#settings-pass-rotated-otp');
              if (rotatedBox) rotatedBox.classList.remove('hidden');
              const codeEl = document.querySelector('#settings-pass-new-code');
              if (codeEl) codeEl.textContent = data.recovery_code;

              const copyBtn = document.querySelector('#btn-settings-pass-copy-code');
              if (copyBtn) {
                copyBtn.onclick = async () => {
                  await navigator.clipboard.writeText(data.recovery_code).catch(() => { });
                  copyBtn.textContent = 'Copied!';
                  setTimeout(() => { copyBtn.textContent = 'Copy'; }, 2000);
                };
              }

              const downloadBtn = document.querySelector('#btn-settings-pass-download-code');
              if (downloadBtn) {
                downloadBtn.onclick = () => {
                  const now = new Date().toISOString();
                  const content = `====================================================\nPERSONAL OPS - UPDATED EMERGENCY RECOVERY CODE\n====================================================\nUsername:               ${localStorage.getItem('ops_user') || ''}\nNEW Emergency Recovery: ${data.recovery_code}\nRotated At:             ${now}\nStatus:                 ACTIVE ✅ (Previous code is INVALID ❌)\n====================================================\n`;
                  downloadTextFile(`personal-ops-recovery-key-updated.txt`, content);
                };
              }
            }
          } catch (error) {
            if (submitBtn) {
              submitBtn.disabled = false;
              submitBtn.classList.remove('is-loading');
              submitBtn.innerHTML = originalBtnHtml;
            }
            if (progressEl) progressEl.classList.add('hidden');
            if (errEl) errEl.textContent = error.message;
          }
        });
      }
    },


    initDashboard() {
      if (!localStorage.getItem('ops_access')) { window.location = '/signin/'; return; }
      const state = {
        threadId: null,
        pendingResultCard: null,
        threads: [],
        initialThreadPicked: false,
        connectedServices: new Set(),
        messageCount: 0,
        sending: false,
        pendingApproval: null,
        streamRequestId: 0,
        abortController: null,
        sessionTokens: 0,
        sessionStats: { promptTokens: 0, completionTokens: 0, cachedTokens: 0, totalTokens: 0, llmCalls: 0, domains: {} },
        sessionTurns: [],
        sessionHudOpen: false,
        sessionHudMode: 'compact',
        sessionHudTab: 'overview',
        lastUserQuery: '',
      };
      const transcript = document.querySelector('#transcript');
      const input = document.querySelector('#message-input');
      const title = document.querySelector('#thread-title');
      const sendButton = document.querySelector('.send-button');
      const threadList = document.querySelector('#thread-list');
      const threadSearchInput = document.querySelector('#thread-search-input');
      const threadSearchClear = document.querySelector('#thread-search-clear');
      const threadCountBadge = document.querySelector('#thread-count-badge');
      const composerNote = document.querySelector('#composer-note');
      const banner = document.querySelector('#error-banner');
      const bannerMsg = document.querySelector('#error-banner-msg');
      const bannerRetry = document.querySelector('#error-banner-retry');
      const bannerDismiss = document.querySelector('#error-banner-dismiss');
      const currentUser = localStorage.getItem('ops_user') || 'Workspace online';
      document.querySelector('#user-label').textContent = currentUser;
      const avatar = document.querySelector('#user-avatar');
      if (avatar) avatar.textContent = currentUser.charAt(0).toUpperCase();

      // Theme toggle support (Button + Keyboard shortcut Ctrl+Shift+T)
      const themeToggleBtn = document.querySelector('#theme-toggle');
      const toggleTheme = () => {
        const current = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        if (next === 'dark') {
          document.documentElement.classList.add('theme-dark');
        } else {
          document.documentElement.classList.remove('theme-dark');
        }
        localStorage.setItem('ops_theme', next);
      };
      if (themeToggleBtn) {
        themeToggleBtn.onclick = () => toggleTheme();
      }
      window.addEventListener('keydown', e => {
        if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === 'T' || e.key === 't')) {
          e.preventDefault();
          toggleTheme();
        }
      });

      // Mobile Drawer Toggle
      const mobileMenuToggle = document.querySelector('#mobile-menu-toggle');
      const sidebarCloseBtn = document.querySelector('#sidebar-close-btn');
      const sidebar = document.querySelector('.sidebar');
      const sidebarBackdrop = document.querySelector('#sidebar-backdrop');

      const setMobileSidebar = (open) => {
        if (!sidebar) return;
        const willOpen = open !== undefined ? open : !sidebar.classList.contains('mobile-open');
        sidebar.classList.toggle('mobile-open', willOpen);
        if (sidebarBackdrop) sidebarBackdrop.classList.toggle('visible', willOpen);
        document.body.classList.toggle('drawer-open', willOpen);
      };

      if (mobileMenuToggle) {
        mobileMenuToggle.onclick = (e) => {
          e.stopPropagation();
          setMobileSidebar();
        };
      }
      if (sidebarCloseBtn) {
        sidebarCloseBtn.onclick = () => setMobileSidebar(false);
      }
      if (sidebarBackdrop) {
        sidebarBackdrop.onclick = () => setMobileSidebar(false);
      }
      window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && sidebar?.classList.contains('mobile-open')) {
          setMobileSidebar(false);
        }
      });

      const newThreadBtn = document.querySelector('#new-thread');
      if (newThreadBtn) {
        newThreadBtn.addEventListener('click', () => {
          if (window.innerWidth <= 768) setMobileSidebar(false);
        });
      }
      if (threadList) {
        threadList.addEventListener('click', (e) => {
          if (e.target.closest('.thread-item') && window.innerWidth <= 768) {
            setMobileSidebar(false);
          }
        });
      }

      // Interactive User Walkthrough Tour Controller
      const tourOverlay = document.querySelector('#tour-overlay');
      const tourCard = document.querySelector('#tour-card');
      const tourArrow = document.querySelector('#tour-arrow');
      const tourTitle = document.querySelector('#tour-step-title');
      const tourDesc = document.querySelector('#tour-step-desc');
      const tourBadge = document.querySelector('#tour-badge');
      const tourDotsContainer = document.querySelector('#tour-dots');
      const btnTourPrev = document.querySelector('#btn-tour-prev');
      const btnTourNext = document.querySelector('#btn-tour-next');
      const btnTourClose = document.querySelector('#btn-tour-close');
      const btnOpenTour = document.querySelector('#btn-open-tour');

      const tourSteps = [
        {
          id: 'step-connections',
          selector: '.connections',
          title: '1. Connect Your Workspace Tools',
          desc: 'Connect Gmail, Google Calendar, Slack, Docs, Sheets, and live Web Search. Personal Ops seamlessly executes multi-domain workflows across all your integrated tools.',
          placement: 'right',
        },
        {
          id: 'step-new-thread',
          selector: '.sidebar-action-wrap',
          title: '2. Conversations & Context Threads',
          desc: 'Start dedicated threads for different projects or tasks. Personal Ops preserves cross-domain state, action history, and links automatically across the conversation.',
          placement: 'right',
        },
        {
          id: 'step-composer',
          selector: '.composer-input-container',
          title: '3. Natural Language Command Desk',
          desc: 'Give autonomous multi-step instructions like "Schedule a sync with Omer tomorrow at 4:00 PM and send him the Google Meet link on Slack".',
          placement: 'top',
        },
        {
          id: 'step-hud',
          selector: '#session-token-wrapper',
          title: '4. Token Intelligence & Safety Approvals',
          desc: 'Live token consumption tracking and safety guardrails. High-impact write actions (sending emails, scheduling events) require your interactive card approval.',
          placement: 'bottom',
        }
      ];

      let currentTourIndex = 0;
      let highlightedEl = null;

      const clearTourHighlight = () => {
        if (highlightedEl) {
          highlightedEl.classList.remove('tour-target-highlight');
          highlightedEl = null;
        }
      };

      const positionTourCard = (targetEl, placement) => {
        if (!tourCard || !tourArrow) return;
        if (!targetEl || window.innerWidth <= 768) {
          tourCard.style.top = '';
          tourCard.style.left = '';
          tourCard.style.right = '';
          tourCard.style.bottom = '';
          tourArrow.className = 'tour-arrow tour-arrow-up';
          return;
        }

        const rect = targetEl.getBoundingClientRect();
        const cardWidth = 340;
        const cardHeight = 220;
        const margin = 16;

        tourArrow.className = 'tour-arrow';

        if (placement === 'right') {
          tourCard.style.left = `${Math.min(window.innerWidth - cardWidth - 20, rect.right + margin)}px`;
          tourCard.style.top = `${Math.max(20, Math.min(window.innerHeight - cardHeight - 20, rect.top + (rect.height / 2) - (cardHeight / 2)))}px`;
          tourCard.style.bottom = '';
          tourCard.style.right = '';
          tourArrow.classList.add('tour-arrow-left');
        } else if (placement === 'top') {
          tourCard.style.left = `${Math.max(20, Math.min(window.innerWidth - cardWidth - 20, rect.left + (rect.width / 2) - (cardWidth / 2)))}px`;
          tourCard.style.top = `${Math.max(20, rect.top - cardHeight - margin)}px`;
          tourCard.style.bottom = '';
          tourCard.style.right = '';
          tourArrow.classList.add('tour-arrow-down');
        } else {
          tourCard.style.left = `${Math.max(20, Math.min(window.innerWidth - cardWidth - 20, rect.left + (rect.width / 2) - (cardWidth / 2)))}px`;
          tourCard.style.top = `${Math.min(window.innerHeight - cardHeight - 20, rect.bottom + margin)}px`;
          tourCard.style.bottom = '';
          tourCard.style.right = '';
          tourArrow.classList.add('tour-arrow-up');
        }
      };

      const renderTourStep = (index) => {
        if (!tourOverlay) return;
        clearTourHighlight();

        currentTourIndex = index;
        const step = tourSteps[index];
        if (!step) return;

        const isMobile = window.innerWidth <= 768;
        if (isMobile) {
          document.body.classList.add('tour-active-mobile');
          if (step.id === 'step-connections' || step.id === 'step-new-thread') {
            setMobileSidebar(true);
          } else {
            setMobileSidebar(false);
          }
        }

        setTimeout(() => {
          const targetEl = document.querySelector(step.selector);
          if (targetEl) {
            targetEl.classList.add('tour-target-highlight');
            highlightedEl = targetEl;
            if (targetEl.scrollIntoViewIfNeeded) {
              targetEl.scrollIntoViewIfNeeded({ behavior: 'smooth', block: 'center' });
            } else if (targetEl.scrollIntoView) {
              targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
          }
          if (tourTitle) tourTitle.textContent = step.title;
          if (tourDesc) tourDesc.textContent = step.desc;
          if (tourBadge) tourBadge.textContent = `Step ${index + 1} of ${tourSteps.length}`;

          const tourBar = document.querySelector('#tour-integrations-bar');
          if (tourBar) {
            if (step.id === 'step-connections') {
              tourBar.innerHTML = `
                <span class="tour-icon-badge">${getDomainIconSvg('gmail', 14)} Gmail</span>
                <span class="tour-icon-badge">${getDomainIconSvg('calendar', 14)} Calendar</span>
                <span class="tour-icon-badge">${getDomainIconSvg('slack', 14)} Slack</span>
                <span class="tour-icon-badge">${getDomainIconSvg('docs', 14)} Docs</span>
                <span class="tour-icon-badge">${getDomainIconSvg('sheets', 14)} Sheets</span>
                <span class="tour-icon-badge">${getDomainIconSvg('research', 14)} Search</span>
              `;
            } else {
              tourBar.innerHTML = '';
            }
          }

          if (btnTourPrev) {
            btnTourPrev.style.visibility = index === 0 ? 'hidden' : 'visible';
          }
          if (btnTourNext) {
            btnTourNext.textContent = index === tourSteps.length - 1 ? 'Finish Tour 🎉' : 'Next Step →';
          }

          if (tourDotsContainer) {
            tourDotsContainer.innerHTML = tourSteps.map((_, i) =>
              `<span class="tour-dot ${i === index ? 'active' : ''}"></span>`
            ).join('');
          }

          positionTourCard(targetEl, step.placement);
        }, isMobile ? 220 : 90);
      };

      const startTour = () => {
        if (!tourOverlay) return;
        tourOverlay.classList.remove('hidden');
        tourOverlay.setAttribute('aria-hidden', 'false');
        renderTourStep(0);
      };

      const closeTour = () => {
        if (!tourOverlay) return;
        clearTourHighlight();
        document.body.classList.remove('tour-active-mobile');
        tourOverlay.classList.add('hidden');
        tourOverlay.setAttribute('aria-hidden', 'true');
        localStorage.setItem('ops_tour_seen', 'true');
        if (window.innerWidth <= 768) setMobileSidebar(false);
      };

      if (btnTourNext) {
        btnTourNext.onclick = () => {
          if (currentTourIndex < tourSteps.length - 1) {
            renderTourStep(currentTourIndex + 1);
          } else {
            closeTour();
          }
        };
      }
      if (btnTourPrev) {
        btnTourPrev.onclick = () => {
          if (currentTourIndex > 0) {
            renderTourStep(currentTourIndex - 1);
          }
        };
      }
      if (btnTourClose) {
        btnTourClose.onclick = () => closeTour();
      }
      if (btnOpenTour) {
        btnOpenTour.onclick = () => startTour();
      }

      // Auto-start for first-time visitors
      if (!localStorage.getItem('ops_tour_seen')) {
        setTimeout(() => {
          if (!localStorage.getItem('ops_tour_seen')) {
            startTour();
          }
        }, 1200);
      }

      // Network & slow internet feedback toasts
      const networkToast = document.querySelector('#network-toast');
      const networkMsg = document.querySelector('#network-toast-msg');
      let netTimer = null;
      const showNetToast = (msg, persistent = false) => {
        if (!networkToast) return;
        if (networkMsg) networkMsg.textContent = msg;
        networkToast.classList.remove('hidden');
        if (netTimer) clearTimeout(netTimer);
        if (!persistent) {
          netTimer = setTimeout(() => { networkToast.classList.add('hidden'); }, 4000);
        }
      };
      const hideNetToast = () => {
        if (networkToast) networkToast.classList.add('hidden');
      };

      window.addEventListener('offline', () => {
        showNetToast('Internet connection lost. Waiting for connection…', true);
      });
      window.addEventListener('online', () => {
        showNetToast('Internet restored! Workspace is back online.');
      });

      const recordTurnMetrics = (metrics, userQuery = '', messageEl = null) => {
        if (!metrics || typeof metrics !== 'object') return;
        const total = Number(metrics.total_tokens) || ((metrics.input_tokens || 0) + (metrics.output_tokens || 0));
        const prompt = Number(metrics.input_tokens) || 0;
        const completion = Number(metrics.output_tokens) || 0;
        const cached = Number(metrics.cached_tokens) || 0;
        const calls = Number(metrics.llm_calls) || (metrics.breakdown ? metrics.breakdown.length : 1);
        const latency = metrics.latency_s != null ? metrics.latency_s : (metrics.latency_ms ? (metrics.latency_ms / 1000).toFixed(2) : '1.0');

        state.sessionTokens += total;
        state.sessionStats.promptTokens += prompt;
        state.sessionStats.completionTokens += completion;
        state.sessionStats.cachedTokens += cached;
        state.sessionStats.totalTokens += total;
        state.sessionStats.llmCalls += calls;

        const breakdown = metrics.breakdown || [];
        const turnDomains = new Set();
        breakdown.forEach(step => {
          const name = step.name || 'General';
          const domain = name.split(' ')[0] || 'General';
          turnDomains.add(domain);
          state.sessionStats.domains[domain] = (state.sessionStats.domains[domain] || 0) + (step.total_tokens || 0);
        });

        const turnNumber = state.sessionTurns.length + 1;
        const turnRecord = {
          turnNumber,
          query: userQuery || (state.lastUserQuery || `Turn #${turnNumber}`),
          totalTokens: total,
          promptTokens: prompt,
          completionTokens: completion,
          cachedTokens: cached,
          latency,
          calls,
          domains: Array.from(turnDomains),
          metrics,
          messageEl,
        };
        state.sessionTurns.push(turnRecord);

        const el = document.querySelector('#session-tokens');
        if (el) el.textContent = state.sessionTokens.toLocaleString();

        if (state.sessionHudOpen) {
          renderSessionHud();
        }
      };

      const updateSessionTokens = (count) => {
        state.sessionTokens = (state.sessionTokens || 0) + (Number(count) || 0);
        const el = document.querySelector('#session-tokens');
        if (el) el.textContent = state.sessionTokens.toLocaleString();
      };

      const closeSessionHud = () => {
        state.sessionHudOpen = false;
        const pill = document.querySelector('#session-token-pill');
        const panel = document.querySelector('#session-hud-panel');
        if (pill) {
          pill.classList.remove('active');
          pill.setAttribute('aria-expanded', 'false');
        }
        if (panel) panel.classList.add('hidden');
      };

      const toggleSessionHud = () => {
        state.sessionHudOpen = !state.sessionHudOpen;
        const pill = document.querySelector('#session-token-pill');
        const panel = document.querySelector('#session-hud-panel');
        if (!pill || !panel) return;

        document.querySelectorAll('.message-metrics.is-open').forEach(el => el.classList.remove('is-open'));

        if (state.sessionHudOpen) {
          pill.classList.add('active');
          pill.setAttribute('aria-expanded', 'true');
          panel.classList.remove('hidden');
          renderSessionHud();
        } else {
          pill.classList.remove('active');
          pill.setAttribute('aria-expanded', 'false');
          panel.classList.add('hidden');
        }
      };

      const renderSessionHud = () => {
        const panel = document.querySelector('#session-hud-panel');
        if (!panel) return;
        const stats = state.sessionStats;
        const turns = state.sessionTurns;
        const mode = state.sessionHudMode;
        const tab = state.sessionHudTab;
        const totalTok = stats.totalTokens || state.sessionTokens || 0;
        const cacheHitPct = (stats.promptTokens + stats.cachedTokens > 0)
          ? ((stats.cachedTokens / (stats.promptTokens + stats.cachedTokens)) * 100).toFixed(1)
          : '0';

        let bodyContent = '';

        if (tab === 'overview') {
          bodyContent = `
            <div class="popover-stats-grid">
              <div class="stat-card">
                <span class="stat-label">Session Tokens</span>
                <span class="stat-value highlight">${totalTok.toLocaleString()}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">Total LLM Calls</span>
                <span class="stat-value">${stats.llmCalls || 0}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">Input / Prompt</span>
                <span class="stat-value">${stats.promptTokens.toLocaleString()}</span>
              </div>
              <div class="stat-card">
                <span class="stat-label">Output / Gen</span>
                <span class="stat-value">${stats.completionTokens.toLocaleString()}</span>
              </div>
            </div>
            ${stats.cachedTokens > 0 ? `
              <div class="cached-tokens-row">
                <span>⚡ Prompt Cache Saved</span>
                <span class="cache-val">${stats.cachedTokens.toLocaleString()} tok (${cacheHitPct}%)</span>
              </div>
            ` : ''}
            <div class="context-window-wrap">
              <div class="context-window-header">
                <span>Session Efficiency</span>
                <span class="context-percent">${turns.length} Turn${turns.length === 1 ? '' : 's'} Recorded</span>
              </div>
              <div class="context-window-meta">
                <span>Avg ${(turns.length ? Math.round(totalTok / turns.length) : 0).toLocaleString()} tok/turn</span>
                <span>Zero-cost Router Active</span>
              </div>
            </div>
          `;
        } else if (tab === 'turns') {
          if (!turns.length) {
            bodyContent = '<div style="font-size:10px; color:#788c7f; text-align:center; padding:16px 0;">No turns recorded yet in this session.</div>';
          } else {
            bodyContent = `
              <div class="metrics-subheading">Turn History (Click to navigate)</div>
              <div style="max-height: 240px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px;">
                ${turns.map((t, idx) => `
                  <div class="turn-nav-item" data-turn-idx="${idx}" role="button" tabindex="0" title="Jump to Turn #${t.turnNumber}">
                    <div class="turn-nav-left">
                      <span class="turn-badge">#${t.turnNumber}</span>
                      <span class="turn-preview">${escapeHtml(t.query)}</span>
                    </div>
                    <div class="turn-nav-right">
                      <span>${t.totalTokens.toLocaleString()} tok</span>
                      <span class="dot">•</span>
                      <span>${t.latency}s</span>
                    </div>
                  </div>
                `).join('')}
              </div>
            `;
          }
        } else if (tab === 'domains') {
          const domainKeys = Object.keys(stats.domains);
          if (!domainKeys.length) {
            bodyContent = '<div style="font-size:10px; color:#788c7f; text-align:center; padding:16px 0;">No agent domains active yet.</div>';
          } else {
            bodyContent = `
              <div class="metrics-subheading">Domain Token Distribution</div>
              <div class="metrics-breakdown-list">
                ${domainKeys.map(d => {
              const tok = stats.domains[d];
              const pct = totalTok > 0 ? Math.round((tok / totalTok) * 100) : 0;
              return `
                    <div class="metrics-breakdown-item" style="cursor:default;">
                      <div class="metrics-breakdown-main">
                        <div class="item-title">
                          <span class="step-name">${escapeHtml(d)} Agent</span>
                        </div>
                        <div class="item-stats">
                          <span>${tok.toLocaleString()} tok</span>
                          <span class="dot">•</span>
                          <span>${pct}%</span>
                        </div>
                      </div>
                    </div>
                  `;
            }).join('')}
              </div>
            `;
          }
        }

        panel.innerHTML = `
          <div class="popover-header">
            <div class="popover-title">
              <span>⚡</span> Session Intelligence HUD
            </div>
            <div class="hud-header-actions">
              <button class="hud-action-btn" id="session-hud-mode-btn" title="Toggle view density">${mode === 'compact' ? 'Expand' : 'Compact'}</button>
              <button class="hud-close-btn" id="session-hud-close-btn" title="Close HUD" aria-label="Close">×</button>
            </div>
          </div>
          <div class="hud-tab-nav">
            <button class="hud-tab-btn ${tab === 'overview' ? 'active' : ''}" data-tab="overview">Overview</button>
            <button class="hud-tab-btn ${tab === 'turns' ? 'active' : ''}" data-tab="turns">Turns (${turns.length})</button>
            <button class="hud-tab-btn ${tab === 'domains' ? 'active' : ''}" data-tab="domains">Domains</button>
          </div>
          <div class="session-hud-body">
            ${bodyContent}
          </div>
          <div class="popover-footer">
            <span>Active Session • ${turns.length} Turn${turns.length === 1 ? '' : 's'}</span>
            <button class="hud-action-btn" id="session-hud-scroll-latest" style="font-size:8.5px;">Jump to Latest</button>
          </div>
        `;

        const closeBtn = panel.querySelector('#session-hud-close-btn');
        if (closeBtn) {
          closeBtn.onclick = (e) => {
            e.stopPropagation();
            closeSessionHud();
          };
        }

        const modeBtn = panel.querySelector('#session-hud-mode-btn');
        if (modeBtn) {
          modeBtn.onclick = (e) => {
            e.stopPropagation();
            state.sessionHudMode = state.sessionHudMode === 'compact' ? 'expanded' : 'compact';
            renderSessionHud();
          };
        }

        const scrollLatest = panel.querySelector('#session-hud-scroll-latest');
        if (scrollLatest) {
          scrollLatest.onclick = (e) => {
            e.stopPropagation();
            transcript.scrollTo({ top: transcript.scrollHeight, behavior: 'smooth' });
          };
        }

        panel.querySelectorAll('.hud-tab-btn').forEach(btn => {
          btn.onclick = (e) => {
            e.stopPropagation();
            state.sessionHudTab = btn.dataset.tab;
            renderSessionHud();
          };
        });

        panel.querySelectorAll('.turn-nav-item').forEach(item => {
          item.onclick = (e) => {
            e.stopPropagation();
            const idx = Number(item.dataset.turnIdx);
            const turn = state.sessionTurns[idx];
            if (turn && turn.messageEl) {
              turn.messageEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
              turn.messageEl.classList.remove('turn-highlight-flash');
              void turn.messageEl.offsetWidth;
              turn.messageEl.classList.add('turn-highlight-flash');
            }
          };
        });
      };

      const sessionPill = document.querySelector('#session-token-pill');
      if (sessionPill) {
        sessionPill.onclick = (e) => {
          e.stopPropagation();
          toggleSessionHud();
        };
      }

      const showBanner = (message, retry) => {
        bannerMsg.textContent = message;
        bannerRetry.classList.toggle('hidden', !retry);
        bannerRetry.onclick = retry ? () => { hideBanner(); retry(); } : null;
        banner.classList.remove('hidden');
      };
      const hideBanner = () => banner.classList.add('hidden');
      bannerDismiss.onclick = hideBanner;

      // Single source of truth for whether the composer should accept input —
      // it must stay locked while a message is sending, while an approval is
      // still pending (a new chat message would race the paused, interrupted
      // graph run), and while the browser is offline.
      const refreshComposerState = () => {
        const busy = state.sending || !!state.pendingApproval || !navigator.onLine;
        input.disabled = busy;
        sendButton.disabled = busy;
        if (!navigator.onLine) {
          composerNote.textContent = "You're offline — reconnect to send messages";
        } else if (state.pendingApproval) {
          composerNote.textContent = 'Review the action above before continuing';
        } else {
          const count = state.connectedServices.size;
          composerNote.textContent = count
            ? `Enter to send • ${count} tool${count === 1 ? '' : 's'} connected`
            : 'Enter to send • No tools connected yet — try Gmail first';
        }
      };

      const loadIntegrations = async () => {
        try {
          const data = await api('/api/integrations/status/');
          state.connectedServices = new Set((data.integrations || []).filter(item => item.enabled).map(item => item.service));
          ['gmail', 'calendar', 'docs', 'sheets', 'slack'].forEach(service => {
            const dot = document.querySelector(`#${service}-dot`);
            const stateEl = document.querySelector(`#${service}-state`);
            const connected = state.connectedServices.has(service);
            if (dot) {
              dot.classList.toggle('dot-on', connected);
              dot.classList.toggle('dot-off', !connected);
            }
            if (stateEl) {
              stateEl.textContent = connected ? 'Connected' : 'Connect';
              stateEl.classList.toggle('off', !connected);
            }
          });
          refreshComposerState();
        } catch (error) {
          console.warn('Could not load integrations', error);
        }
      };

      document.querySelectorAll('[data-integration]').forEach(button => {
        button.onclick = async () => {
          const service = button.dataset.integration;
          try {
            const data = await api(`/api/integrations/${service}/connect/`);
            window.location.href = data.authorization_url;
          } catch (error) {
            showBanner(error.message);
          }
        };
      });

      const renderMetricsBadge = (metrics) => {
        if (!metrics || typeof metrics !== 'object') return '';
        const latency = metrics.latency_s != null ? metrics.latency_s : (metrics.latency_ms ? (metrics.latency_ms / 1000).toFixed(2) : '1.0');
        const totalTokens = metrics.total_tokens || ((metrics.input_tokens || 0) + (metrics.output_tokens || 0));
        const inputTokens = metrics.input_tokens || 0;
        const outputTokens = metrics.output_tokens || 0;
        const cachedTokens = metrics.cached_tokens || 0;
        const contextLimit = metrics.context_limit || 128000;
        const contextPct = metrics.context_used_pct != null ? metrics.context_used_pct : Math.min(100, ((totalTokens / contextLimit) * 100).toFixed(2));
        const rawModel = metrics.model || 'openai/gpt-oss-120b';
        const modelClean = rawModel.split('/').pop();
        const calls = metrics.llm_calls || (metrics.breakdown ? metrics.breakdown.length : 1);
        const breakdown = metrics.breakdown || [];

        let breakdownHtml = '';
        if (breakdown.length > 0) {
          breakdownHtml = `
            <div class="metrics-breakdown-section">
              <div class="metrics-subheading">Pipeline Execution Breakdown</div>
              <div class="metrics-breakdown-list">
                ${breakdown.map((item, idx) => `
                  <div class="metrics-breakdown-item">
                    <div class="item-title">
                      <span class="step-num">${idx + 1}</span>
                      <span class="step-name">${escapeHtml(item.name || 'LLM Call')}</span>
                      <span class="step-model">${escapeHtml(item.model ? item.model.split('/').pop() : '')}</span>
                    </div>
                    <div class="item-stats">
                      <span>${(item.total_tokens || ((item.input_tokens || 0) + (item.output_tokens || 0))).toLocaleString()} tok</span>
                      <span class="dot">•</span>
                      <span>${item.latency_ms ? item.latency_ms + 'ms' : (item.latency_s ? item.latency_s + 's' : '')}</span>
                    </div>
                  </div>
                `).join('')}
              </div>
            </div>
          `;
        }

        return `
          <div class="message-metrics" tabindex="0" role="region" aria-label="Token and performance metrics">
            <div class="metrics-badge" role="button" tabindex="0" title="Click to inspect Turn Performance & Tokens">
              <span class="metric-icon">⚡</span>
              <span class="metric-time">${latency}s</span>
              <span class="metric-dot">•</span>
              <span class="metric-count">${totalTokens.toLocaleString()} tok</span>
              <span class="hud-expand-caret">▾</span>
            </div>
            <div class="metrics-popover">
              <div class="popover-header">
                <div class="popover-title"><span>⚡</span> Turn Performance & Tokens</div>
                <span class="popover-model-badge">${escapeHtml(modelClean)}</span>
              </div>
              <div class="popover-stats-grid">
                <div class="stat-card">
                  <span class="stat-label">Response Latency</span>
                  <span class="stat-value">${latency}s</span>
                </div>
                <div class="stat-card">
                  <span class="stat-label">Total Tokens</span>
                  <span class="stat-value highlight">${totalTokens.toLocaleString()}</span>
                </div>
                <div class="stat-card">
                  <span class="stat-label">Input (Prompt)</span>
                  <span class="stat-value">${inputTokens.toLocaleString()}</span>
                </div>
                <div class="stat-card">
                  <span class="stat-label">Output (Gen)</span>
                  <span class="stat-value">${outputTokens.toLocaleString()}</span>
                </div>
              </div>
              ${cachedTokens > 0 ? `
                <div class="cached-tokens-row">
                  <span>⚡ Prompt Cache Hit</span>
                  <span class="cache-val">${cachedTokens.toLocaleString()} tokens cached</span>
                </div>
              ` : ''}
              <div class="context-window-wrap">
                <div class="context-window-header">
                  <span>Context Window Utilization</span>
                  <span class="context-percent">${contextPct}% of ${(contextLimit / 1000).toFixed(0)}k limit</span>
                </div>
                <div class="context-progress-bar">
                  <div class="context-progress-fill" style="width: ${Math.max(1.5, Math.min(100, contextPct))}%"></div>
                </div>
                <div class="context-window-meta">
                  <span>${totalTokens.toLocaleString()} used</span>
                  <span>${contextLimit.toLocaleString()} capacity</span>
                </div>
              </div>
              ${breakdownHtml}
              <div class="popover-footer">
                <span>LLM Calls: <strong>${calls}</strong></span>
                <span class="cache-tag">Zero-cost reference router</span>
              </div>
            </div>
          </div>
        `;
      };

      const addMessage = (role, content, pending = false, metrics = null, resultCard = null) => {
        const item = document.createElement('article');
        item.className = `message ${role} ${pending ? 'pending' : ''}`;
        const labelHtml = role === 'user'
          ? `<span class="message-label"><span class="message-label-left">You</span></span>`
          : `<span class="message-label">
               <span class="message-label-left">
                 <span class="message-label-avatar agent">⚡</span>
                 <span>Personal Ops</span>
               </span>
               <span class="message-actions">
                 <button class="copy-msg-btn" type="button" title="Copy response">
                   <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                   <span>Copy</span>
                 </button>
               </span>
             </span>`;
        item.innerHTML = `${labelHtml}<div class="message-content"></div>`;
        const copyBtn = item.querySelector('.copy-msg-btn');
        if (copyBtn) {
          copyBtn.onclick = (e) => {
            e.stopPropagation();
            const rawContent = (typeof content === 'string' ? content : (item.querySelector('.message-content') ? item.querySelector('.message-content').innerText : '')).replace(/<suggested_title>[\s\S]*?(?:<\/suggested_title>|$)/gi, '').trim();
            navigator.clipboard.writeText(rawContent).then(() => {
              const span = copyBtn.querySelector('span') || copyBtn;
              span.textContent = 'Copied!';
              setTimeout(() => { span.textContent = 'Copy'; }, 1800);
            }).catch(() => { });
          };
        }
        const body = item.querySelector('.message-content');
        const text = role === 'agent' ? cleanMessageContent(content) : safeText(content);
        if (pending) {
          body.innerHTML = `<div class="pending-agent"><span class="dot-flash"><i></i><i></i><i></i></span><span class="pending-domain-icon">${getDomainIconSvg('general', 16)}</span><span>Operations agent thinking…</span></div>`;
        } else if (role === 'user') {
          body.textContent = text;
        } else {
          const cardToRender = resultCard || (role === 'agent' ? state.pendingResultCard : null);
          if (cardToRender && cardToRender === state.pendingResultCard) {
            state.pendingResultCard = null;
          }

          if (role === 'agent' && cardToRender && typeof renderResultCard === 'function') {
            const cardEl = renderResultCard(cardToRender);
            const cardType = cardToRender.type;
            const cleanedText = cleanRetiredCardMarkdown(text, cardType);
            const order = (cardToRender.presentation && cardToRender.presentation.order) || 'text_first';

            if (order === 'card_first') {
              const sentences = cleanedText.match(/[^.!?]+[.!?]+/g) || (cleanedText ? [cleanedText] : []);
              const takeaway = sentences.slice(0, 2).join(' ').trim();
              const rest = cleanedText.slice(takeaway.length).trim();

              if (takeaway) {
                const textWrap = document.createElement('div');
                textWrap.className = 'markdown-body';
                textWrap.innerHTML = renderMarkdown(takeaway);
                body.appendChild(textWrap);
              }
              if (cardEl) {
                body.appendChild(cardEl);
              }
              if (rest) {
                const details = document.createElement('details');
                details.className = 'rc-details';
                details.innerHTML = `<summary>More details</summary><div class="rc-details-body markdown-body">${renderMarkdown(rest)}</div>`;
                body.appendChild(details);
              }
            } else {
              // text_first
              if (cleanedText) {
                const textWrap = document.createElement('div');
                textWrap.className = 'markdown-body';
                textWrap.innerHTML = renderMarkdown(cleanedText);
                body.appendChild(textWrap);
              }
              if (cardEl) {
                body.appendChild(cardEl);
              }
            }
          } else {
            const textWrap = document.createElement('div');
            textWrap.className = 'markdown-body';
            textWrap.innerHTML = renderMarkdown(text);
            body.appendChild(textWrap);
          }
          if (metrics && typeof metrics === 'object') {
            recordTurnMetrics(metrics, state.lastUserQuery || 'Agent Response', item);
            const metricsWrap = document.createElement('div');
            metricsWrap.className = 'message-metrics-container';
            metricsWrap.innerHTML = renderMetricsBadge(metrics);
            const badgeEl = metricsWrap.querySelector('.metrics-badge');
            if (badgeEl) {
              badgeEl.onclick = (e) => {
                e.stopPropagation();
                const metricsContainer = badgeEl.closest('.message-metrics');
                if (metricsContainer) {
                  const wasOpen = metricsContainer.classList.contains('is-open');
                  document.querySelectorAll('.message-metrics.is-open').forEach(el => el.classList.remove('is-open'));
                  if (!wasOpen) metricsContainer.classList.add('is-open');
                }
              };
            }
            item.appendChild(metricsWrap);
          }
        }
        transcript.appendChild(item);
        transcript.scrollTop = transcript.scrollHeight;
        state.messageCount += 1;
        return item;
      };

      const morphCardToCompleted = (card, approved, instruction, modifiedArgs, domainLabel = '') => {
        if (!card || !card.isConnected) return;
        card.classList.remove('is-busy');
        card.classList.add('is-completed', 'is-collapsed');
        if (!approved && !instruction) {
          card.classList.add('is-cancelled');
        }
        const editToggle = card.querySelector('.approval-edit-toggle-bar');
        if (editToggle) editToggle.remove();
        const editDrawer = card.querySelector('.approval-edit-drawer');
        if (editDrawer) editDrawer.remove();
        const instructWrap = card.querySelector('.approval-instruction-wrap');
        if (instructWrap) instructWrap.remove();

        const domain = card.dataset.domain || 'general';
        const label = domainLabel || (DOMAIN_META[domain] ? DOMAIN_META[domain].label : titleCase(domain));
        const badge = card.querySelector('.approval-badge');
        if (badge) {
          badge.classList.remove('is-processing');
          const icon = (DOMAIN_META[domain] && DOMAIN_META[domain].icon) || '✓';
          badge.innerHTML = `${icon} <span>${escapeHtml(label)} Agent: ${approved ? 'Completed' : (instruction ? 'Revised' : 'Cancelled')}</span>`;
        }

        const currentPlanStep = card.querySelector('.approval-plan-step.is-step-current');
        if (currentPlanStep) {
          currentPlanStep.classList.remove('is-step-current');
          currentPlanStep.classList.add(approved ? 'is-step-done' : 'is-step-pending');
          const stepIcon = currentPlanStep.querySelector('.plan-step-icon');
          if (stepIcon) stepIcon.textContent = approved ? '✓' : '✕';
          const stepTag = currentPlanStep.querySelector('.plan-step-tag');
          if (stepTag) stepTag.textContent = approved ? 'Done' : (instruction ? 'Revised' : 'Cancelled');
        }

        const actions = card.querySelector('.approval-actions');
        if (actions) {
          const badgeText = approved
            ? (modifiedArgs ? 'Approved with edits' : `${label} Agent: Completed`)
            : (instruction ? 'Revised by instruction' : 'Cancelled');
          const badgeClass = approved ? 'badge-executed' : (instruction ? 'badge-revised' : 'badge-cancelled');
          const icon = approved ? '✓' : (instruction ? '✎' : '✕');
          actions.innerHTML = `<div class="approval-resolved-badge ${badgeClass}"><span class="badge-icon">${icon}</span> <span>${escapeHtml(badgeText)}</span></div>`;
        }

        const status = card.querySelector('.approval-status');
        if (status) {
          status.classList.remove('hidden', 'status-error');
          status.classList.add('status-success');
          const statusText = approved
            ? `${label} Agent: Completed`
            : (instruction ? 'Instruction processed' : 'Action cancelled');
          status.innerHTML = `<span class="status-icon">✓</span> <span class="status-text">${escapeHtml(statusText)}</span>`;
        }

        const collapseBtn = card.querySelector('.btn-approval-collapse');
        if (collapseBtn) {
          collapseBtn.title = 'Expand details';
        }
      };

      const renderHistoricalCard = cardData => {
        const domain = (cardData && cardData.domain) || 'general';
        const meta = DOMAIN_META[domain] || { icon: '⚙️', label: titleCase(domain) };
        const card = document.createElement('div');
        card.className = 'approval-card is-completed is-collapsed';
        if (!cardData.approved && cardData.status === 'cancelled') {
          card.classList.add('is-cancelled');
        }
        card.dataset.domain = domain;
        const heading = cardData.heading || `${meta.label} action`;
        const approved = !!cardData.approved;
        const statusText = approved ? 'Completed' : (cardData.status === 'revised' ? 'Revised' : 'Cancelled');
        const badgeIcon = meta.icon || '✓';
        const badgeClass = approved ? 'badge-executed' : (cardData.status === 'revised' ? 'badge-revised' : 'badge-cancelled');
        const actionIcon = approved ? '✓' : (cardData.status === 'revised' ? '✎' : '✕');

        card.innerHTML = `
          <div class="approval-progress-track"><div class="approval-progress-fill"></div></div>
          <div style="flex:1; min-width:0;">
            <div class="approval-header">
              <div class="approval-header-top">
                <span class="approval-badge">${badgeIcon} <span>${meta.label} Agent: ${statusText}</span></span>
                <button type="button" class="btn-approval-collapse" title="Expand details" aria-label="Toggle details">
                  <svg class="chevron-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="6 9 12 15 18 9"></polyline>
                  </svg>
                </button>
              </div>
            </div>
            <h3>${escapeHtml(heading)}</h3>
            <div class="approval-status status-success" aria-live="polite">
              <span class="status-icon">✓</span> <span class="status-text">${meta.label} Agent: ${statusText}</span>
            </div>
            <div class="approval-body-collapsible">
              ${renderApprovalBody({ domain, args: cardData.args || {} })}
            </div>
          </div>
          <div class="approval-actions">
            <div class="approval-resolved-badge ${badgeClass}"><span class="badge-icon">${actionIcon}</span> <span>${meta.label} Agent: ${statusText}</span></div>
          </div>`;

        const collapseBtn = card.querySelector('.btn-approval-collapse');
        if (collapseBtn) {
          collapseBtn.onclick = e => {
            e.stopPropagation();
            const isCollapsed = card.classList.toggle('is-collapsed');
            collapseBtn.title = isCollapsed ? 'Expand details' : 'Collapse details';
          };
        }
        return card;
      };

      const renderPlanPipeline = (plan, currentDomain) => {
        if (!plan || !Array.isArray(plan) || plan.length <= 1) return '';

        // Disambiguate exactly one current step index to prevent multiple steps or
        // repeated domains from falsely claiming "current":
        let currentStepIdx = plan.findIndex(s => s && s.status === 'in_progress');
        if (currentStepIdx === -1) {
          currentStepIdx = plan.findIndex(s => s && s.status !== 'completed' && (s.domain || 'general') === currentDomain);
        }
        if (currentStepIdx === -1) {
          currentStepIdx = plan.findIndex(s => s && s.status !== 'completed');
        }

        const stepsHtml = plan.map((step, idx) => {
          const stepDomain = (step && step.domain) || 'general';
          const meta = DOMAIN_META[stepDomain] || { icon: '⚙️', label: titleCase(stepDomain) };
          const isDone = step && step.status === 'completed';
          const isCurrent = !isDone && (idx === currentStepIdx);
          const stateClass = isDone ? 'is-step-done' : (isCurrent ? 'is-step-current' : 'is-step-pending');
          const stepIcon = isDone ? '✓' : (isCurrent ? meta.icon : '○');
          const statusText = isDone ? 'Done' : (isCurrent ? 'Current' : 'Next');

          return `
            <div class="approval-plan-step ${stateClass}" data-step-index="${idx}">
              <span class="plan-step-icon">${stepIcon}</span>
              <span class="plan-step-label">Step ${idx + 1}: ${meta.label}</span>
              <span class="plan-step-tag">${statusText}</span>
            </div>
          `;
        }).join('<span class="plan-step-separator">→</span>');

        return `
          <div class="approval-plan-pipeline">
            <div class="approval-plan-title">
              <span>⚡ Workflow Plan (${plan.length} Steps)</span>
            </div>
            <div class="approval-plan-steps-track">
              ${stepsHtml}
            </div>
          </div>
        `;
      };

      const addApprovalCard = approval => {
        const domain = (approval && approval.domain) || 'email';
        const meta = DOMAIN_META[domain] || { icon: '⚙️', label: titleCase(domain) };
        const card = document.createElement('div');
        card.className = 'approval-card';
        card.dataset.domain = domain;
        const threadId = (approval && (approval.thread_id || approval.threadId)) || state.threadId || (typeof sessionStorage !== 'undefined' ? sessionStorage.getItem('ops_active_thread') : '') || '';
        if (threadId) {
          card.dataset.threadId = String(threadId);
        }
        const plan = (approval && Array.isArray(approval.plan)) ? approval.plan : [];
        const heading = approval && approval.is_duplicate
          ? `Re-run this ${meta.label.toLowerCase()} action?`
          : (approval && approval.message) || `Approve ${meta.label.toLowerCase()} action`;
        card.innerHTML = `
          <div class="approval-progress-track"><div class="approval-progress-fill"></div></div>
          <div style="flex:1; min-width:0;">
            <div class="approval-header">
              <div class="approval-header-top">
                <span class="approval-badge">${meta.icon} <span>Waiting on you — ${meta.label}</span></span>
                <button type="button" class="btn-approval-collapse" title="Collapse details" aria-label="Toggle details">
                  <svg class="chevron-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="6 9 12 15 18 9"></polyline>
                  </svg>
                </button>
              </div>
            </div>
            <h3>${escapeHtml(heading)}</h3>
            ${renderPlanPipeline(plan, domain)}
            <div class="approval-status hidden" aria-live="polite"></div>
            <div class="approval-body-collapsible">
              ${renderApprovalBody(approval || {})}
              <div class="approval-edit-toggle-bar">
                <button type="button" class="btn-approval-toggle-edit" data-action="toggle-edit">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
                  <span>Edit fields</span>
                </button>
              </div>
              <div class="approval-edit-drawer hidden">
                ${renderApprovalEditFields(approval || {})}
              </div>
              <div class="approval-instruction-wrap">
                <input type="text" class="approval-instruction-input" placeholder="Or instruct agent to revise (e.g. change time, revise text)...">
                <button type="button" class="btn-instruct-action" data-action="instruct">
                  <span>Instruct Agent</span>
                </button>
              </div>
            </div>
          </div>
          <div class="approval-actions">
            <button class="btn btn-primary btn-approve" data-action="approve">
              <span>Approve</span>
            </button>
            <button class="btn btn-ghost btn-cancel" data-action="cancel">
              <span>Cancel</span>
            </button>
          </div>`;

        const collapseBtn = card.querySelector('.btn-approval-collapse');
        if (collapseBtn) {
          collapseBtn.onclick = (e) => {
            e.stopPropagation();
            const isCollapsed = card.classList.toggle('is-collapsed');
            collapseBtn.title = isCollapsed ? 'Expand details' : 'Collapse details';
          };
        }

        const toggleBtn = card.querySelector('[data-action="toggle-edit"]');
        const editDrawer = card.querySelector('.approval-edit-drawer');
        if (toggleBtn && editDrawer) {
          toggleBtn.onclick = () => {
            const isHidden = editDrawer.classList.toggle('hidden');
            const span = toggleBtn.querySelector('span');
            if (span) span.textContent = isHidden ? 'Edit fields' : 'Hide edit fields';
          };
        }

        const instructBtn = card.querySelector('[data-action="instruct"]');
        const instructInput = card.querySelector('.approval-instruction-input');
        if (instructBtn && instructInput) {
          const triggerInstruction = () => {
            const instruction = instructInput.value.trim();
            if (!instruction) {
              instructInput.focus();
              return;
            }
            approve(false, card, instruction);
          };
          instructBtn.onclick = triggerInstruction;
          instructInput.onkeydown = (e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              triggerInstruction();
            }
          };
        }

        const approveButton = card.querySelector('[data-action="approve"]');
        const cancelButton = card.querySelector('[data-action="cancel"]');
        approveButton.onclick = () => approve(true, card);
        cancelButton.onclick = () => approve(false, card);
        transcript.appendChild(card);
        transcript.scrollTop = transcript.scrollHeight;
        state.pendingApproval = card;
        refreshComposerState();
        return card;
      };

      const renderThreadSkeleton = () => { threadList.innerHTML = '<div class="thread-skeleton"><div class="sk-line"></div><div class="sk-line"></div><div class="sk-line"></div></div>'; };

      const cancelActiveStream = () => {
        if (state.abortController) {
          state.abortController.abort();
          state.abortController = null;
        }
      };

      const getDynamicGreeting = (username = '') => {
        const user = username || localStorage.getItem('ops_user') || '';
        const suffix = user ? `, ${user}` : '';
        const currentHour = new Date().getHours();
        if (currentHour < 5) {
          return `Working late${suffix}?`;
        } else if (currentHour < 12) {
          return `Good morning${suffix}.`;
        } else if (currentHour < 18) {
          return `Good afternoon${suffix}.`;
        } else {
          return `Good evening${suffix}.`;
        }
      };

      const bindPromptButtons = () => {
        transcript.querySelectorAll('[data-prompt]').forEach(button => {
          button.onclick = () => {
            input.value = button.dataset.prompt;
            autoGrowTextarea(input);
            input.focus();
            const composerBox = document.querySelector('.composer-input-container');
            if (composerBox) {
              composerBox.classList.remove('composer-pulse');
              void composerBox.offsetWidth; // trigger reflow
              composerBox.classList.add('composer-pulse');
            }
            if (window.innerWidth <= 768) {
              input.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
          };
        });

        const welcomeTourBtn = document.querySelector('#btn-welcome-tour');
        if (welcomeTourBtn) {
          welcomeTourBtn.onclick = () => startTour();
        }
      };

      const renderWelcomeState = () => {
        const user = localStorage.getItem('ops_user') || '';
        const greeting = getDynamicGreeting(user);
        transcript.innerHTML = `
          <div class="welcome-hub">
            <div class="welcome-badge"><i class="status-dot"></i>Personal Ops Assistant</div>
            <h2 class="welcome-heading">${escapeHtml(greeting)}</h2>
            <p class="welcome-sub">Autonomous multi-domain assistant for your daily operations. Connect services, run cross-tool workflows, or choose a starter card below.</p>
            
            <div class="welcome-tour-banner">
              <div class="tour-banner-content">
                <span class="tour-banner-icon">🚀</span>
                <div class="tour-banner-text">
                  <strong>New to Personal Ops?</strong>
                  <span>Take a 30-second interactive guided walkthrough with visual pointers.</span>
                </div>
              </div>
              <button type="button" id="btn-welcome-tour" class="welcome-tour-btn">Start Quick Tour 💡</button>
            </div>

            <div class="quick-cards-header">
              <span class="quick-cards-label">Quick Starter Workflows</span>
              <span class="quick-cards-hint">Click any card to pre-fill</span>
            </div>

            <div class="starter-grid">
              <button type="button" class="starter-card" data-prompt="Schedule a 30-minute sync with Omer tomorrow at 4:00 PM about Jarvis project discussion and inform him on Slack to confirm availability">
                <div class="starter-card-top">
                  <span class="starter-tag tag-multi">Calendar + Slack</span>
                  <span class="starter-arrow">→</span>
                </div>
                <div class="starter-card-main">
                  <span class="starter-icon">${getDomainIconSvg('calendar', 20)} ${getDomainIconSvg('slack', 20)}</span>
                  <div class="starter-info">
                    <span class="starter-title">Schedule & Notify</span>
                    <span class="starter-desc">Book meeting on Google Calendar and ping attendee on Slack to confirm</span>
                  </div>
                </div>
              </button>

              <button type="button" class="starter-card" data-prompt="Give me a quick overview of my unread emails from today and summarize action items">
                <div class="starter-card-top">
                  <span class="starter-tag tag-gmail">Gmail</span>
                  <span class="starter-arrow">→</span>
                </div>
                <div class="starter-card-main">
                  <span class="starter-icon">${getDomainIconSvg('gmail', 22)}</span>
                  <div class="starter-info">
                    <span class="starter-title">Inbox Digest</span>
                    <span class="starter-desc">Summarize latest unread messages and highlight urgent requests</span>
                  </div>
                </div>
              </button>

              <button type="button" class="starter-card" data-prompt="Search the web for the latest breakthroughs in AI agents and draft a summary note in Google Docs">
                <div class="starter-card-top">
                  <span class="starter-tag tag-research">Research + Docs</span>
                  <span class="starter-arrow">→</span>
                </div>
                <div class="starter-card-main">
                  <span class="starter-icon">${getDomainIconSvg('research', 20)} ${getDomainIconSvg('docs', 20)}</span>
                  <div class="starter-info">
                    <span class="starter-title">Live Research & Doc</span>
                    <span class="starter-desc">Search current web facts and synthesize findings into a Google Doc</span>
                  </div>
                </div>
              </button>

              <button type="button" class="starter-card" data-prompt="Read key metrics from my latest active Google Sheet and summarize highlights">
                <div class="starter-card-top">
                  <span class="starter-tag tag-sheets">Sheets</span>
                  <span class="starter-arrow">→</span>
                </div>
                <div class="starter-card-main">
                  <span class="starter-icon">${getDomainIconSvg('sheets', 22)}</span>
                  <div class="starter-info">
                    <span class="starter-title">Inspect Spreadsheets</span>
                    <span class="starter-desc">Extract tabular data and summarize key numbers or table rows</span>
                  </div>
                </div>
              </button>

              <button type="button" class="starter-card" data-prompt="Draft a project milestone progress update to share with the team on Slack">
                <div class="starter-card-top">
                  <span class="starter-tag tag-slack">Slack</span>
                  <span class="starter-arrow">→</span>
                </div>
                <div class="starter-card-main">
                  <span class="starter-icon">${getDomainIconSvg('slack', 22)}</span>
                  <div class="starter-info">
                    <span class="starter-title">Team Broadcast</span>
                    <span class="starter-desc">Compose and post concise progress updates or status announcements</span>
                  </div>
                </div>
              </button>

              <button type="button" class="starter-card" data-prompt="What operations tasks, tools, and cross-domain workflows can you assist me with?">
                <div class="starter-card-top">
                  <span class="starter-tag tag-general">Capabilities</span>
                  <span class="starter-arrow">→</span>
                </div>
                <div class="starter-card-main">
                  <span class="starter-icon">${getDomainIconSvg('general', 22)}</span>
                  <div class="starter-info">
                    <span class="starter-title">Explore Capabilities</span>
                    <span class="starter-desc">Discover connected tools, safety approvals, and autonomous planning</span>
                  </div>
                </div>
              </button>
            </div>
          </div>
        `;
        bindPromptButtons();
      };

      const selectThread = async id => {
        cancelActiveStream();
        state.threadId = id;
        try {
          sessionStorage.setItem('ops_active_thread', String(id));
          window.history.replaceState(null, '', `/?thread=${encodeURIComponent(id)}`);
        } catch (e) { }
        state.messageCount = 0;
        state.pendingApproval = null;
        state.sending = false;
        refreshComposerState();

        const currentThread = (state.threads || []).find(t => String(t.id) === String(id));
        const threadName = currentThread && currentThread.name && currentThread.name !== 'New Thread'
          ? currentThread.name
          : (currentThread && currentThread.name === 'New Thread' ? 'New Conversation' : 'Workspace Chat');
        title.textContent = threadName;

        state.sessionTurns = [];
        state.sessionTokens = 0;
        state.sessionStats = { promptTokens: 0, completionTokens: 0, cachedTokens: 0, totalTokens: 0, llmCalls: 0, domains: {} };
        const sessionCountEl = document.querySelector('#session-tokens');
        if (sessionCountEl) sessionCountEl.textContent = '0';

        document.querySelectorAll('.thread-item').forEach(item => item.classList.toggle('active', item.dataset.id == id));
        transcript.innerHTML = '<div class="loading-line"><span class="btn-spinner spinner-dark"></span> Loading conversation history…</div>';
        try {
          const data = await api(`/api/thread/${encodeURIComponent(id)}/messages/`);
          const messages = Array.isArray(data) ? data : data.results || [];
          transcript.innerHTML = '';
          state.messageCount = 0;
          if (!messages.length) {
            renderWelcomeState();
          } else {
            messages.forEach(message => {
              try {
                const role = message.role === 'assistant' ? 'agent' : message.role;
                if (role === 'user') {
                  state.lastUserQuery = message.content;
                }
                let msgResultCard = null;
                if (message.cards && Array.isArray(message.cards)) {
                  message.cards.forEach(cardData => {
                    if (cardData && typeof cardData === 'object') {
                      if (cardData.kind === 'result') {
                        msgResultCard = cardData;
                      } else {
                        const cardEl = renderHistoricalCard(cardData);
                        transcript.appendChild(cardEl);
                      }
                    }
                  });
                }
                const metrics = message.metrics || null;
                addMessage(
                  role,
                  message.content,
                  false,
                  metrics,
                  msgResultCard
                );
              } catch (err) {
                console.error('Failed to render message:', err);
              }
            });
          }
        } catch (error) {
          transcript.innerHTML = '';
          showBanner("Couldn't load that thread.", () => selectThread(id));
        }
      };

      const deleteThread = async threadId => {
        if (!threadId) return;
        const confirmed = window.confirm('Delete this thread?');
        if (!confirmed) return;

        try {
          await api(`/api/thread/${encodeURIComponent(threadId)}/delete/`, { method: 'DELETE' });
          if (state.threadId === threadId) {
            state.threadId = null;
            state.pendingApproval = null;
            try {
              sessionStorage.removeItem('ops_active_thread');
              window.history.replaceState(null, '', '/');
            } catch (e) { }
            title.textContent = getDynamicGreeting();
            renderWelcomeState();
            document.querySelectorAll('.thread-item').forEach(item => item.classList.remove('active'));
          }
          await loadThreads({ autoResume: !state.threadId });
        } catch (error) {
          showBanner(error.message || "Couldn't delete that thread.", () => deleteThread(threadId));
        }
      };

      const renderThreadItems = (threadsToRender, filterQuery = '') => {
        threadList.innerHTML = '';
        if (!threadsToRender || threadsToRender.length === 0) {
          if (filterQuery) {
            threadList.innerHTML = `
              <div class="rail-no-results">
                <div>No conversations matching "<strong>${escapeHtml(filterQuery)}</strong>"</div>
                <span>Try a different search term</span>
                <button type="button" class="btn btn-ghost" style="padding:4px 10px;font-size:11px;margin-top:4px;" id="reset-search-btn">Clear search</button>
              </div>`;
            const resetBtn = threadList.querySelector('#reset-search-btn');
            if (resetBtn) {
              resetBtn.onclick = () => {
                if (threadSearchInput) threadSearchInput.value = '';
                filterThreads('');
                if (threadSearchInput) threadSearchInput.focus();
              };
            }
          } else {
            threadList.innerHTML = '<div class="rail-empty">No threads yet.<br>Start with a question.</div>';
          }
          return;
        }

        threadsToRender.forEach(thread => {
          const item = document.createElement('button');
          item.className = 'thread-item';
          if (state.threadId && thread.id == state.threadId) {
            item.classList.add('active');
          }
          item.dataset.id = thread.id;
          item.type = 'button';

          const titleText = thread.name || 'New Thread';
          let titleHtml = escapeHtml(titleText);
          if (filterQuery) {
            const qLower = filterQuery.toLowerCase();
            const idx = titleText.toLowerCase().indexOf(qLower);
            if (idx !== -1) {
              const before = titleText.slice(0, idx);
              const match = titleText.slice(idx, idx + filterQuery.length);
              const after = titleText.slice(idx + filterQuery.length);
              titleHtml = `${escapeHtml(before)}<mark class="thread-match-highlight">${escapeHtml(match)}</mark>${escapeHtml(after)}`;
            }
          }

          const rawDate = thread.updated_at || thread.created_at;
          const relativeTime = formatRelativeTime(rawDate);
          const fullDateTooltip = rawDate ? new Intl.DateTimeFormat([], { dateStyle: 'full', timeStyle: 'short' }).format(new Date(rawDate)) : '';

          item.innerHTML = `
            <div class="thread-item-main">
              <span class="thread-item-title">${titleHtml}</span>
              <span class="thread-item-time" title="${escapeHtml(fullDateTooltip)}">${escapeHtml(relativeTime)}</span>
            </div>
            <div class="thread-item-actions">
              <button type="button" class="thread-delete-btn" aria-label="Delete thread" title="Delete conversation">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                  <polyline points="3 6 5 6 21 6"></polyline>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                  <line x1="10" y1="11" x2="10" y2="17"></line>
                  <line x1="14" y1="11" x2="14" y2="17"></line>
                </svg>
              </button>
            </div>`;

          const deleteButton = item.querySelector('.thread-delete-btn');
          deleteButton.onclick = event => {
            event.preventDefault();
            event.stopPropagation();
            deleteThread(thread.id);
          };
          item.onclick = () => selectThread(thread.id);
          threadList.appendChild(item);
        });
      };

      const filterThreads = query => {
        state.threadSearchQuery = (query || '').trim();
        if (threadSearchClear) {
          threadSearchClear.classList.toggle('hidden', !state.threadSearchQuery);
        }
        if (!state.threadSearchQuery) {
          if (threadCountBadge) threadCountBadge.classList.add('hidden');
          renderThreadItems(state.threads);
          return;
        }
        const q = state.threadSearchQuery.toLowerCase();
        const filtered = state.threads.filter(t => (t.name || 'New Thread').toLowerCase().includes(q));
        if (threadCountBadge) {
          threadCountBadge.textContent = `${filtered.length} of ${state.threads.length}`;
          threadCountBadge.classList.remove('hidden');
        }
        renderThreadItems(filtered, state.threadSearchQuery);
      };

      if (threadSearchInput) {
        threadSearchInput.oninput = e => {
          filterThreads(e.target.value);
        };
        threadSearchInput.onkeydown = e => {
          if (e.key === 'Escape') {
            threadSearchInput.value = '';
            filterThreads('');
            threadSearchInput.blur();
          }
        };
      }

      if (threadSearchClear) {
        threadSearchClear.onclick = () => {
          if (threadSearchInput) threadSearchInput.value = '';
          filterThreads('');
          if (threadSearchInput) threadSearchInput.focus();
        };
      }

      const loadThreads = async ({ autoResume = false } = {}) => {
        renderThreadSkeleton();
        try {
          const data = await api('/api/list_thread/');
          const threads = Array.isArray(data) ? data : data.results || [];
          state.threads = threads;
          filterThreads(threadSearchInput ? threadSearchInput.value : '');
          if (autoResume && !state.initialThreadPicked) {
            state.initialThreadPicked = true;
            let resumeThreadId = null;
            try {
              const urlParams = new URLSearchParams(window.location.search);
              resumeThreadId = urlParams.get('thread') || sessionStorage.getItem('ops_active_thread');
            } catch (e) { }

            if (resumeThreadId && threads.some(t => String(t.id) === String(resumeThreadId))) {
              selectThread(resumeThreadId);
            } else {
              try {
                sessionStorage.removeItem('ops_active_thread');
                window.history.replaceState(null, '', '/');
              } catch (e) { }
              state.threadId = null;
              title.textContent = getDynamicGreeting();
              renderWelcomeState();
            }
          }
          return threads;
        } catch (error) {
          threadList.innerHTML = '';
          showBanner("Couldn't load your threads.", () => loadThreads({ autoResume }));
          return [];
        }
      };

      // The backend names a thread once it sees more than 2 turns; re-pull the list so the
      // sidebar and header pick up the generated name without a full page reload.
      const syncThreadName = async () => {
        if (!state.threadId) return;
        try {
          const data = await api('/api/list_thread/');
          const threads = Array.isArray(data) ? data : data.results || [];
          state.threads = threads;
          const current = threads.find(thread => thread.id == state.threadId);
          if (!current || !current.name || current.name === 'New Thread') return;
          title.textContent = current.name;
          const item = threadList.querySelector(`.thread-item[data-id="${current.id}"] .thread-item-title`);
          if (item) item.textContent = current.name;
          else await loadThreads();
        } catch (error) { /* non-critical, next send will retry */ }
      };

      const send = async message => {
        if (state.sending || state.pendingApproval || !navigator.onLine) return;
        const streamId = ++state.streamRequestId;
        const requestController = new AbortController();
        state.abortController = requestController;
        state.sending = true;
        refreshComposerState();

        const pending = addMessage('agent', '', true);
        const body = pending.querySelector('.message-content');
        const setPendingStatus = (label, domainOrNode) => {
          if (!body) return;
          let d = domainOrNode;
          if (d && typeof d === 'string' && d.endsWith('_agent')) d = d.replace('_agent', '');
          if (!d && typeof label === 'string') {
            const l = label.toLowerCase();
            if (l.includes('email') || l.includes('gmail')) d = 'gmail';
            else if (l.includes('calendar')) d = 'calendar';
            else if (l.includes('slack')) d = 'slack';
            else if (l.includes('doc')) d = 'docs';
            else if (l.includes('sheet')) d = 'sheets';
            else if (l.includes('research') || l.includes('search')) d = 'research';
            else if (l.includes('planning') || l.includes('coordinating') || l.includes('ops') || l.includes('connecting')) d = 'general';
          }
          const cleanLabel = (label || '').replace(/^[\u{1F300}-\u{1F9FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{1F600}-\u{1F64F}\u{1F680}-\u{1F6FF}]\s*/u, '');
          const iconHtml = `<span class="pending-domain-icon">${getDomainIconSvg(d || 'general', 16)}</span>`;
          body.innerHTML = `<div class="pending-agent"><span class="dot-flash"><i></i><i></i><i></i></span>${iconHtml}<span>${escapeHtml(cleanLabel || label)}</span></div>`;
        };
        const currentThreadId = state.threadId;
        let assistantText = '';
        let completedHandled = false;
        let hasReceivedTokens = false;
        const slowTimer = setTimeout(() => {
          if (!hasReceivedTokens && state.sending) {
            setPendingStatus('Connecting across tools… network is slow, still processing', 'general');
          }
        }, 5500);

        const handlePayload = data => {
          if (!data || typeof data !== 'object') return;
          if (slowTimer) clearTimeout(slowTimer);
          const status = data.status || data.type || '';
          const token = data.token ?? data.delta ?? data.chunk ?? data.content ?? data.text ?? '';
          const response = data.response ?? data.result ?? data.output ?? data.content ?? data.text ?? '';
          const approval = data.approval ?? data.tool_approval ?? data.action ?? data.request ?? {};
          const message = data.message ?? '';

          // Handle new explicit status protocol from backend
          if (data.type === 'status') {
            if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
            // Display the backend-provided status message with authentic integration icon
            if (message) {
              setPendingStatus(message, data.node || data.agent);
            }
            return;
          }

          // Handle live thread title updates
          if (data.type === 'thread_name' && data.thread_name) {
            if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
            if (data.thread_name !== 'New Thread') {
              title.textContent = data.thread_name;
              const item = threadList.querySelector(`.thread-item[data-id="${data.thread_id || currentThreadId}"] .thread-item-title`);
              if (item) item.textContent = data.thread_name;
              const tr = (state.threads || []).find(t => String(t.id) === String(data.thread_id || currentThreadId));
              if (tr) tr.name = data.thread_name;
            }
            return;
          }

          // Handle token streaming - transition from status to actual response
          if (data.type === 'token') {
            if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
            // On first token, clear any pending status and start accumulating response
            hasReceivedTokens = true;
            assistantText += safeText(token);
            pending.classList.remove('pending');
            body.textContent = assistantText.replace(/<suggested_title>[\s\S]*?(?:<\/suggested_title>|$)/gi, '').trim();
            transcript.scrollTop = transcript.scrollHeight;
            return;
          }

          // Fallback for older backend payloads that use status field for tokens
          if (data.type === 'chunk' || data.type === 'delta' || status === 'in_progress') {
            if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
            hasReceivedTokens = true;
            assistantText += safeText(token);
            pending.classList.remove('pending');
            body.textContent = assistantText;
            transcript.scrollTop = transcript.scrollHeight;
            return;
          }

          // Fallback: if we see old status values without type field, display as status
          if (!data.type && (status === 'thinking' || status === 'searching' || status === 'generating')) {
            if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
            const label = status === 'thinking' ? 'Thinking…' : status === 'searching' ? 'Looking up information…' : status === 'generating' ? 'Generating response…' : 'Processing request…';
            if (message) {
              setPendingStatus(message);
            } else {
              setPendingStatus(label);
            }
            return;
          }

          if (data.type === 'result_card') {
            if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
            state.pendingResultCard = data.card;
            return;
          }

          if (data.type === 'approval_required' || status === 'approval_required') {
            if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
            if (!state.threadId && data.thread_id) {
              state.threadId = data.thread_id;
              try {
                sessionStorage.setItem('ops_active_thread', String(data.thread_id));
                window.history.replaceState(null, '', `/?thread=${encodeURIComponent(data.thread_id)}`);
              } catch (e) { }
              loadThreads();
            }
            setPendingStatus('Waiting for approval…');
            pending.remove();
            addApprovalCard({ ...(approval || {}), thread_id: data.thread_id || state.threadId });
            return 'approval';
          }

          if (data.type === 'completed' || status === 'completed') {
            if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
            completedHandled = true;
            const rawResponse = typeof response === 'string' ? response : response && typeof response === 'object' ? (response.content || response.text || JSON.stringify(response)) : assistantText;
            const finalText = safeText(rawResponse).replace(/<suggested_title>[\s\S]*?(?:<\/suggested_title>|$)/gi, '').trim();
            assistantText = finalText;
            pending.remove();
            const metrics = data.metrics || null;
            addMessage('agent', assistantText, false, metrics);
            if (!state.threadId && data.thread_id) {
              state.threadId = data.thread_id;
              try {
                sessionStorage.setItem('ops_active_thread', String(data.thread_id));
                window.history.replaceState(null, '', `/?thread=${encodeURIComponent(data.thread_id)}`);
              } catch (e) { }
              loadThreads();
            }
            if (data.thread_name && data.thread_name !== 'New Thread') {
              title.textContent = data.thread_name;
              const item = threadList.querySelector(`.thread-item[data-id="${data.thread_id || currentThreadId}"] .thread-item-title`);
              if (item) item.textContent = data.thread_name;
              const tr = (state.threads || []).find(t => String(t.id) === String(data.thread_id || currentThreadId));
              if (tr) tr.name = data.thread_name;
            } else if (state.messageCount >= 2) {
              syncThreadName();
            }
            return 'completed';
          }

          if (data.type === 'error' || status === 'error') {
            if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
            setPendingStatus('Something went wrong…');
            throw new Error(message || data.message || 'Agent request failed.');
          }

          // Fallback: legacy message-only payloads
          if (typeof message === 'string' && !data.type && !data.status) {
            if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
            assistantText += message;
            pending.classList.remove('pending');
            body.textContent = assistantText;
          }
        };

        const processSseBlock = block => {
          if (!block || !block.trim()) return;
          const lines = block.split(/\r?\n/);
          let payload = '';
          for (const line of lines) {
            if (!line || line.startsWith(':')) continue;
            if (line.toLowerCase().startsWith('data:')) {
              payload += line.slice(5).trim();
            } else if (line.toLowerCase().startsWith('event:')) {
              continue;
            }
          }
          if (!payload) return;
          try {
            const data = JSON.parse(payload);
            return handlePayload(data);
          } catch {
            return;
          }
        };

        setPendingStatus('Starting…');

        try {
          const userTimezone = (() => {
            try {
              return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
            } catch {
              return 'UTC';
            }
          })();
          const endpoint = state.threadId ? `/api/thread/${state.threadId}/chat/` : '/api/chat/';
          const response = await fetchWithAuth(endpoint, {
            method: 'POST',
            headers: { 'Accept': 'text/event-stream' },
            body: JSON.stringify({ message, timezone: userTimezone }),
            signal: requestController.signal,
          });

          if (!response.ok) {
            let messageText = `Request failed with status ${response.status}`;
            if (response.status === 400) messageText = 'The request could not be processed.';
            else if (response.status === 401) messageText = 'Your session expired. Please sign in again.';
            else if (response.status === 403) messageText = 'You do not have permission to do that.';
            else if (response.status === 404) messageText = 'This chat endpoint was not found.';
            else if (response.status === 409) messageText = 'This request conflicts with the current thread state.';
            else if (response.status === 429) messageText = 'Too many requests. Please wait a moment and try again.';
            else if (response.status >= 500) messageText = 'The server hit an error while processing your message.';
            throw new Error(messageText);
          }

          if (!response.body) {
            throw new Error('Streaming is not supported by this browser.');
          }

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';

          while (true) {
            const { value, done } = await reader.read();
            if (value) buffer += decoder.decode(value, { stream: true });
            if (!done) {
              const blocks = buffer.split(/\r?\n\r?\n/);
              buffer = blocks.pop() || '';
              for (const block of blocks) {
                if (block.trim() === '') continue;
                const result = processSseBlock(block);
                if (result === 'approval') return;
                if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
              }
              continue;
            }
            buffer += decoder.decode();
            const remaining = buffer.trim();
            if (remaining) {
              const result = processSseBlock(remaining);
              if (result === 'approval') return;
            }
            // Handle stream ending: if we never got a completed event but have assistant text, save it
            if (!completedHandled && assistantText) {
              pending.remove();
              addMessage('agent', assistantText);
            } else if (!completedHandled) {
              pending.remove();
            }
            break;
          }
        } catch (error) {
          if (error && error.name === 'AbortError') return;
          if (!completedHandled && assistantText) {
            pending.remove();
            addMessage('agent', assistantText);
          } else {
            pending.remove();
          }
          const messageText = error && error.message ? error.message : 'Something went wrong while sending the message.';
          if (streamId === state.streamRequestId && currentThreadId === state.threadId) showBanner(messageText, () => send(message));
        } finally {
          if (state.abortController && state.abortController.signal.aborted) {
            state.abortController = null;
          }
          state.sending = false;
          refreshComposerState();
          input.focus();
        }
      };

      document.querySelector('#chat-form').addEventListener('submit', event => {
        event.preventDefault();
        const message = input.value.trim();
        if (!message || state.sending || state.pendingApproval) return;
        addMessage('user', message);
        input.value = '';
        autoGrowTextarea(input);
        send(message);
      });
      input.addEventListener('input', () => autoGrowTextarea(input));
      input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); document.querySelector('#chat-form').requestSubmit(); } });
      document.querySelector('#new-thread').onclick = () => {
        if (state.pendingApproval) return; // don't abandon an open approval mid-decision
        cancelActiveStream();
        state.threadId = null;
        try {
          sessionStorage.removeItem('ops_active_thread');
          window.history.replaceState(null, '', '/');
        } catch (e) { }
        state.messageCount = 0;
        state.sending = false;
        state.sessionTurns = [];
        state.sessionTokens = 0;
        state.sessionStats = { promptTokens: 0, completionTokens: 0, cachedTokens: 0, totalTokens: 0, llmCalls: 0, domains: {} };
        const sessionCountEl = document.querySelector('#session-tokens');
        if (sessionCountEl) sessionCountEl.textContent = '0';
        title.textContent = getDynamicGreeting();
        renderWelcomeState();
        document.querySelectorAll('.thread-item').forEach(item => item.classList.remove('active'));
        refreshComposerState();
        input.focus();
      };
      document.querySelector('#logout').onclick = () => { localStorage.removeItem('ops_access'); localStorage.removeItem('ops_refresh'); localStorage.removeItem('ops_user'); window.location = '/signin/'; };

      async function approve(value, card, instruction = null) {
        if (!card || card.dataset.busy === 'true') return;
        state.pendingApproval = card;

        const actions = card.querySelector('.approval-actions');
        const statusEl = card.querySelector('.approval-status');
        const instructBtn = card.querySelector('[data-action="instruct"]');
        const approveBtn = card.querySelector('[data-action="approve"]');
        const cancelBtn = card.querySelector('[data-action="cancel"]');
        const domain = card.dataset.domain || 'general';
        const meta = DOMAIN_META[domain] || { icon: '⚙️', label: titleCase(domain) };

        card.dataset.busy = 'true';
        card.classList.add('is-busy');

        // Disable input edits and instruction fields so parameters cannot be changed mid-flight
        card.querySelectorAll('.approval-edit-input, .approval-instruction-input').forEach(input => input.disabled = true);

        // Gather modified args if approving
        let modifiedArgs = null;
        if (value) {
          card.querySelectorAll('[data-arg-key]').forEach(input => {
            const key = input.dataset.argKey;
            const original = (input.dataset.originalVal ?? '').trim();
            const current = (input.value || '').trim();
            if (current !== original) {
              if (!modifiedArgs) modifiedArgs = {};
              if (key === 'to' || key === 'attendees') {
                modifiedArgs[key] = current.includes(',')
                  ? current.split(',').map(s => s.trim()).filter(Boolean)
                  : (current ? [current] : []);
              } else {
                modifiedArgs[key] = current;
              }
            }
          });
        }

        const threadId = card.dataset.threadId || state.threadId || (typeof sessionStorage !== 'undefined' ? sessionStorage.getItem('ops_active_thread') : null);
        if (!threadId) {
          if (statusEl) {
            statusEl.classList.remove('hidden', 'status-success');
            statusEl.classList.add('status-error');
            statusEl.innerHTML = `<span class="status-icon">⚠</span> <span class="status-text">This approval is no longer attached to an active thread.</span>`;
          }
          delete card.dataset.busy;
          card.classList.remove('is-busy');
          return;
        }
        if (!state.threadId) {
          state.threadId = threadId;
        }

        // Show initial status on card
        const updateCardStatus = (text) => {
          if (!statusEl) return;
          statusEl.classList.remove('hidden', 'status-error', 'status-success');
          statusEl.innerHTML = `<span class="status-pulse-dot"></span> <span class="status-text">${escapeHtml(text)}</span>`;
        };

        updateCardStatus(value ? `Connecting to ${meta.label} service…` : (instruction ? 'Forwarding revision…' : 'Cancelling…'));

        const badge = card.querySelector('.approval-badge');
        if (badge) {
          badge.classList.add('is-processing');
          badge.innerHTML = `<span class="status-pulse-dot"></span> Executing ${meta.label} action…`;
        }

        if (value) {
          if (approveBtn) {
            approveBtn.disabled = true;
            approveBtn.classList.add('btn-loading');
            approveBtn.innerHTML = `<span>Executing ${meta.label}…</span><span class="btn-spinner"></span>`;
          }
          if (cancelBtn) cancelBtn.disabled = true;
          if (instructBtn) instructBtn.disabled = true;
        } else if (instruction) {
          if (instructBtn) {
            instructBtn.disabled = true;
            instructBtn.classList.add('btn-loading');
            instructBtn.innerHTML = `<span>Revising…</span><span class="btn-spinner"></span>`;
          }
          if (approveBtn) approveBtn.disabled = true;
          if (cancelBtn) cancelBtn.disabled = true;
        } else {
          if (cancelBtn) {
            cancelBtn.disabled = true;
            cancelBtn.classList.add('btn-loading');
            cancelBtn.innerHTML = `<span>Cancelling…</span><span class="btn-spinner"></span>`;
          }
          if (approveBtn) approveBtn.disabled = true;
          if (instructBtn) instructBtn.disabled = true;
        }

        const payload = { approved: value };
        if (modifiedArgs && Object.keys(modifiedArgs).length > 0) {
          payload.modified_args = modifiedArgs;
        }
        if (instruction) {
          payload.instruction = instruction;
        }

        // Create a thinking bubble message for streaming agent tokens
        let thinkingBubble = null;
        let thinkingBody = null;
        let agentText = '';

        const ensureThinkingBubble = () => {
          if (thinkingBubble) return;
          thinkingBubble = addMessage('agent', '', true);
          thinkingBody = thinkingBubble.querySelector('.message-content');
        };

        const setThinkingStatus = (label, domainOrNode) => {
          ensureThinkingBubble();
          if (!thinkingBody) return;
          let d = domainOrNode || domain;
          if (d && typeof d === 'string' && d.endsWith('_agent')) d = d.replace('_agent', '');
          if (!d && typeof label === 'string') {
            const l = label.toLowerCase();
            if (l.includes('email') || l.includes('gmail')) d = 'gmail';
            else if (l.includes('calendar')) d = 'calendar';
            else if (l.includes('slack')) d = 'slack';
            else if (l.includes('doc')) d = 'docs';
            else if (l.includes('sheet')) d = 'sheets';
            else if (l.includes('research') || l.includes('search')) d = 'research';
            else if (l.includes('planning') || l.includes('coordinating') || l.includes('ops') || l.includes('step')) d = 'general';
          }
          const cleanLabel = (label || '').replace(/^[\u{1F300}-\u{1F9FF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}\u{1F600}-\u{1F64F}\u{1F680}-\u{1F6FF}]\s*/u, '');
          const iconHtml = `<span class="pending-domain-icon">${getDomainIconSvg(d || 'general', 16)}</span>`;
          thinkingBody.innerHTML = `<div class="pending-agent"><span class="dot-flash"><i></i><i></i><i></i></span>${iconHtml}<span>${escapeHtml(cleanLabel || label)}</span></div>`;
        };

        // Fallback timer — shows elapsed time if backend goes quiet
        let lastEventTime = Date.now();
        const approvalStartTime = Date.now();
        const fallbackTimer = setInterval(() => {
          const sinceLast = Math.floor((Date.now() - lastEventTime) / 1000);
          const totalElapsed = Math.floor((Date.now() - approvalStartTime) / 1000);
          if (sinceLast > 4) {
            const msg = `Executing ${meta.label.toLowerCase()} action… (${totalElapsed}s)`;
            if (!cardMorphed) {
              updateCardStatus(msg);
            } else {
              setThinkingStatus(`Processing next step… (${totalElapsed}s)`);
            }
          }
        }, 2000);
        let cardMorphed = false;

        try {
          const response = await fetchWithAuth(`/api/thread/${threadId}/tool-approval/`, {
            method: 'POST',
            headers: { 'Accept': 'text/event-stream' },
            body: JSON.stringify(payload),
          });

          if (!response.ok) {
            let errorText = `Request failed (${response.status})`;
            try { const d = await response.json(); errorText = extractErrorMessage(d) || errorText; } catch {}
            throw new Error(errorText);
          }

          if (!response.body) {
            throw new Error('Streaming not supported by this browser.');
          }

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          let streamCompleted = false;

          const processApprovalSseBlock = (block) => {
            if (!block || !block.trim()) return;
            const lines = block.split(/\r?\n/);
            let ssePayload = '';
            for (const line of lines) {
              if (!line || line.startsWith(':')) continue;
              if (line.toLowerCase().startsWith('data:')) {
                ssePayload += line.slice(5).trim();
              }
            }
            if (!ssePayload) return;
            let data;
            try { data = JSON.parse(ssePayload); } catch { return; }

            lastEventTime = Date.now();

            // The card's own agent node — status stays on the card
            const cardAgentNode = `${domain}_agent`;

            // Detect domain transition: when we see a node that is NOT the card's
            // own agent (e.g. advance_plan, nlp, or a different *_agent), it means
            // the card's step is done — morph the card and switch to thinking bubble
            const morphCardIfNeeded = (nodeHint) => {
              if (cardMorphed) return;
              // Only morph if this node is clearly "past" the card's own domain
              if (nodeHint && nodeHint !== cardAgentNode) {
                cardMorphed = true;
                delete card.dataset.busy;
                card.classList.remove('is-busy');
                morphCardToCompleted(card, value, instruction, modifiedArgs, meta.label);
                if (state.pendingApproval === card) {
                  state.pendingApproval = null;
                }
              }
            };

            // Status events — live feedback
            if (data.type === 'status') {
              const msg = data.message || 'Processing…';
              const node = data.node || '';

              // If the node belongs to a DIFFERENT agent or a coordinator node,
              // the card's step is done — collapse card and switch to thinking bubble
              if (node && node !== cardAgentNode) {
                morphCardIfNeeded(node);
              }

              if (!cardMorphed) {
                // Phase 1: Card is still active — show status ON the card
                updateCardStatus(msg);
              } else {
                // Phase 2: Card is done — show status in thinking bubble
                setThinkingStatus(msg, node);
              }
              return;
            }

            // Thread name updates
            if (data.type === 'thread_name' && data.thread_name) {
              if (data.thread_name !== 'New Thread') {
                title.textContent = data.thread_name;
                const item = threadList.querySelector(`.thread-item[data-id="${data.thread_id || threadId}"] .thread-item-title`);
                if (item) item.textContent = data.thread_name;
              }
              return;
            }

            // Token streaming — agent is generating text
            if (data.type === 'token') {
              const token = data.token || '';
              if (!cardMorphed) {
                // Still on our card's domain — update the card status with progress
                agentText += safeText(token);
                updateCardStatus(`${meta.label} agent is responding…`);
              } else {
                // Past our card — stream into thinking bubble
                agentText += safeText(token);
                ensureThinkingBubble();
                if (thinkingBubble) thinkingBubble.classList.remove('pending');
                if (thinkingBody) {
                  thinkingBody.textContent = agentText.replace(/<suggested_title>[\s\S]*?(?:<\/suggested_title>|$)/gi, '').trim();
                }
                transcript.scrollTop = transcript.scrollHeight;
              }
              return;
            }

            // Next approval required — chain next card
            if (data.type === 'approval_required') {
              streamCompleted = true;

              // Ensure card is morphed (it should already be, but just in case)
              if (!cardMorphed) {
                cardMorphed = true;
                delete card.dataset.busy;
                card.classList.remove('is-busy');
                morphCardToCompleted(card, value, instruction, modifiedArgs, meta.label);
                if (state.pendingApproval === card) {
                  state.pendingApproval = null;
                }
              }

              const approvalMetrics = data.metrics || null;
              if (approvalMetrics && approvalMetrics.total_tokens) {
                updateSessionTokens(approvalMetrics.total_tokens);
              }

              // Show intermediate agent result if present
              if (thinkingBubble) thinkingBubble.remove();
              const resultText = agentText.trim() || (data.result ? safeText(data.result) : '');
              if (resultText) {
                addMessage('agent', resultText.replace(/<suggested_title>[\s\S]*?(?:<\/suggested_title>|$)/gi, '').trim(), false, approvalMetrics);
              }

              // Chain next approval card
              const nextApproval = data.approval || {};
              addApprovalCard({ ...nextApproval, thread_id: data.thread_id || threadId });

              refreshComposerState();
              return 'done';
            }

            if (data.type === 'result_card') {
              state.pendingResultCard = data.card;
              return;
            }

            // Completed — graph finished
            if (data.type === 'completed') {
              streamCompleted = true;

              // Ensure card is morphed
              if (!cardMorphed) {
                cardMorphed = true;
                delete card.dataset.busy;
                card.classList.remove('is-busy');
                morphCardToCompleted(card, value, instruction, modifiedArgs, meta.label);
                if (state.pendingApproval === card) {
                  state.pendingApproval = null;
                }
              }

              const approvalMetrics = data.metrics || null;
              if (approvalMetrics && approvalMetrics.total_tokens) {
                updateSessionTokens(approvalMetrics.total_tokens);
              }

              // Show final agent message
              if (thinkingBubble) thinkingBubble.remove();
              const finalText = agentText.trim() || safeText(data.result || (value ? 'Action executed successfully.' : (instruction ? `Instruction sent: "${instruction}"` : 'Cancelled.')));
              addMessage('agent', finalText.replace(/<suggested_title>[\s\S]*?(?:<\/suggested_title>|$)/gi, '').trim(), false, approvalMetrics);

              if (data.thread_name && data.thread_name !== 'New Thread') {
                title.textContent = data.thread_name;
                const item = threadList.querySelector(`.thread-item[data-id="${data.thread_id || threadId}"] .thread-item-title`);
                if (item) item.textContent = data.thread_name;
              }

              refreshComposerState();
              return 'done';
            }

            // Error
            if (data.type === 'error') {
              throw new Error(data.message || 'Agent request failed.');
            }
          };

          while (true) {
            const { value: chunk, done } = await reader.read();
            if (chunk) buffer += decoder.decode(chunk, { stream: true });
            if (!done) {
              const blocks = buffer.split(/\r?\n\r?\n/);
              buffer = blocks.pop() || '';
              for (const block of blocks) {
                if (block.trim() === '') continue;
                const result = processApprovalSseBlock(block);
                if (result === 'done') {
                  clearInterval(fallbackTimer);
                  return;
                }
              }
              continue;
            }
            // Stream ended
            buffer += decoder.decode();
            const remaining = buffer.trim();
            if (remaining) {
              const result = processApprovalSseBlock(remaining);
              if (result === 'done') {
                clearInterval(fallbackTimer);
                return;
              }
            }
            // Stream ended without a completed/approval event — clean up gracefully
            if (!streamCompleted) {
              delete card.dataset.busy;
              card.classList.remove('is-busy');
              morphCardToCompleted(card, value, instruction, modifiedArgs, meta.label);
              if (state.pendingApproval === card) {
                state.pendingApproval = null;
              }
              if (thinkingBubble) thinkingBubble.remove();
              if (agentText.trim()) {
                addMessage('agent', agentText.trim());
              } else {
                addMessage('agent', value ? 'Action executed successfully.' : 'Action cancelled.');
              }
              refreshComposerState();
            }
            break;
          }
        } catch (error) {
          delete card.dataset.busy;
          card.classList.remove('is-busy');
          if (badge) {
            badge.classList.remove('is-processing');
            badge.innerHTML = `${meta.icon} <span>Waiting on you — ${escapeHtml(meta.label)}</span>`;
          }
          card.querySelectorAll('.approval-edit-input, .approval-instruction-input').forEach(input => input.disabled = false);
          if (approveBtn) {
            approveBtn.disabled = false;
            approveBtn.classList.remove('btn-loading');
            approveBtn.innerHTML = `<span>Approve</span>`;
          }
          if (cancelBtn) {
            cancelBtn.disabled = false;
            cancelBtn.classList.remove('btn-loading');
            cancelBtn.innerHTML = `<span>Cancel</span>`;
          }
          if (instructBtn) {
            instructBtn.disabled = false;
            instructBtn.classList.remove('btn-loading');
            instructBtn.innerHTML = `<span>Instruct Agent</span>`;
          }
          if (statusEl) {
            statusEl.classList.remove('status-success', 'hidden');
            statusEl.classList.add('status-error');
            statusEl.innerHTML = `<span class="status-icon">⚠</span> <span class="status-text">Failed: ${escapeHtml(error.message)}</span>`;
          }
          if (thinkingBubble) thinkingBubble.remove();
          showBanner('Action failed: ' + error.message);
        } finally {
          clearInterval(fallbackTimer);
        }
      }

      window.addEventListener('online', refreshComposerState);
      window.addEventListener('offline', refreshComposerState);

      document.querySelector('#clock').textContent = new Intl.DateTimeFormat([], { weekday: 'short', hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(new Date());
      const clockElement = document.querySelector('#clock');
      const updateClock = () => {
        if (clockElement) {
          clockElement.textContent = new Intl.DateTimeFormat([], { weekday: 'short', hour: 'numeric', minute: '2-digit', second: '2-digit' }).format(new Date());
        }
      };
      const clockInterval = setInterval(updateClock, 1000);
      // Cleanup interval on page unload
      window.addEventListener('beforeunload', () => clearInterval(clockInterval));
      autoGrowTextarea(input);
      const savedDraft = sessionStorage.getItem('ops_draft');
      if (savedDraft) { sessionStorage.removeItem('ops_draft'); input.value = savedDraft; autoGrowTextarea(input); }
      const setupThreadRenaming = () => {
        const editBtn = document.querySelector('#edit-thread-title-btn');
        if (!editBtn || !title) return;

        const startInlineEdit = () => {
          if (!state.threadId || title.querySelector('input')) return;
          const currentName = title.textContent.trim();
          const defaultVal = (currentName === 'New Conversation' || currentName === 'Personal Operations') ? '' : currentName;
          title.innerHTML = `<input type="text" class="title-edit-input" value="${escapeHtml(defaultVal)}" placeholder="Conversation name..." />`;
          const inputEl = title.querySelector('input');
          inputEl.focus();
          inputEl.select();

          let committed = false;
          const commitRename = async () => {
            if (committed) return;
            committed = true;
            const newName = inputEl.value.trim() || currentName;
            title.textContent = newName;
            if (newName && newName !== currentName && newName !== 'New Thread') {
              try {
                await api(`/api/thread/${encodeURIComponent(state.threadId)}/rename/`, {
                  method: 'PATCH',
                  body: JSON.stringify({ name: newName }),
                });
                const item = threadList.querySelector(`.thread-item[data-id="${state.threadId}"] .thread-item-title`);
                if (item) item.textContent = newName;
                const tr = (state.threads || []).find(t => String(t.id) === String(state.threadId));
                if (tr) tr.name = newName;
              } catch (e) {
                showBanner("Couldn't rename thread: " + e.message);
              }
            }
          };

          inputEl.onkeydown = e => {
            if (e.key === 'Enter') {
              e.preventDefault();
              commitRename();
            } else if (e.key === 'Escape') {
              committed = true;
              title.textContent = currentName;
            }
          };
          inputEl.onblur = commitRename;
        };

        editBtn.onclick = (e) => {
          e.stopPropagation();
          startInlineEdit();
        };
        title.ondblclick = (e) => {
          e.stopPropagation();
          startInlineEdit();
        };
      };

      document.addEventListener('click', (e) => {
        if (!e.target.closest('#session-token-wrapper')) {
          closeSessionHud();
        }
        if (!e.target.closest('.message-metrics')) {
          document.querySelectorAll('.message-metrics.is-open').forEach(el => el.classList.remove('is-open'));
        }
      });

      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          closeSessionHud();
          document.querySelectorAll('.message-metrics.is-open').forEach(el => el.classList.remove('is-open'));
        }
      });

      setupThreadRenaming();
      loadIntegrations();
      loadThreads({ autoResume: true });
    }
  };
})();
