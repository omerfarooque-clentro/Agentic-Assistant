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

  const api = async (path, options = {}) => {
    const request = async () => {
      let token = localStorage.getItem('ops_access');
      if (token && !options.public && isExpiringSoon(token)) {
        // Refresh ahead of time so an action in progress (like sending a
        // message) doesn't have to fail once before it can succeed.
        await refreshAccessToken();
        token = localStorage.getItem('ops_access');
      }
      const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
      if (token && !options.public) headers.Authorization = `Bearer ${token}`;
      return fetch(path, { ...options, headers });
    };
    let response = await request();
    if (response.status === 401 && !options.skipRefresh) {
      const refreshed = await refreshAccessToken();
      if (refreshed) response = await request();
      if (response.status === 401) {
        forceSignOut();
        throw new Error('Session expired. Redirecting to sign in...');
      }
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || Object.values(data).flat().join(' ') || 'Something went wrong.');
    return data;
  };
  const formData = form => Object.fromEntries(new FormData(form).entries());
  const showError = error => { const target = document.querySelector('#form-error'); if (target) target.textContent = error.message; };

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

  const APPROVAL_FIELD_LABELS = { to: 'To', subject: 'Subject', body: 'Body', channel: 'Channel', message: 'Message' };
  const SKIPPED_APPROVAL_KEYS = new Set(['type', 'tool_name', 'is_duplicate', 'domain', 'message', 'args']);
  const titleCase = key => key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  const DOMAIN_META = {
    email: { icon: '✉️', label: 'Email' },
    calendar: { icon: '📅', label: 'Calendar' },
    docs: { icon: '📄', label: 'Docs' },
    sheets: { icon: '📊', label: 'Sheets' },
    slack: { icon: '💬', label: 'Slack' },
  };
  const escapeHtml = str => String(str).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

  // Minimal, safe markdown renderer: escapes HTML first, then reintroduces a small
  // set of formatting tags so search/tool results read as lists and links instead of a raw blob.
  const renderMarkdown = raw => {
    const inline = line => line
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    let html = '';
    let listType = null;
    const closeList = () => { if (listType) { html += `</${listType}>`; listType = null; } };
    escapeHtml(raw).split(/\r?\n/).forEach(line => {
      const bullet = line.match(/^\s*[-*]\s+(.*)/);
      const numbered = line.match(/^\s*\d+\.\s+(.*)/);
      if (bullet) { if (listType !== 'ul') { closeList(); html += '<ul>'; listType = 'ul'; } html += `<li>${inline(bullet[1])}</li>`; }
      else if (numbered) { if (listType !== 'ol') { closeList(); html += '<ol>'; listType = 'ol'; } html += `<li>${inline(numbered[1])}</li>`; }
      else { closeList(); html += line.trim() === '' ? '<br>' : `<p>${inline(line)}</p>`; }
    });
    closeList();
    return html;
  };

  const WEATHER_CONDITIONS = ['thunderstorm', 'partly cloudy', 'overcast', 'drizzle', 'showers', 'sunny', 'clear', 'cloudy', 'rain', 'storm', 'snow', 'fog', 'windy', 'hail', 'humid'];
  const detectWeather = text => {
    const tempMatch = text.match(/(-?\d{1,3})\s?°\s?([CF])\b/);
    if (!tempMatch) return null;
    const lower = text.toLowerCase();
    const condition = WEATHER_CONDITIONS.find(word => lower.includes(word));
    const locationMatch = text.match(/(?:weather (?:in|for)|forecast for)\s+([A-Za-z\s,]+?)(?:[.,:]|\s+is|\s+will|\n|$)/i);
    return { temp: tempMatch[1], unit: tempMatch[2], condition: condition ? titleCase(condition) : null, location: locationMatch ? locationMatch[1].trim() : null };
  };
  const weatherIcon = condition => {
    const c = (condition || '').toLowerCase();
    if (c.includes('sun') || c.includes('clear')) return '☀️';
    if (c.includes('cloud') || c.includes('overcast')) return '☁️';
    if (c.includes('rain') || c.includes('shower') || c.includes('drizzle')) return '🌧️';
    if (c.includes('storm') || c.includes('thunder')) return '⛈️';
    if (c.includes('snow') || c.includes('hail')) return '❄️';
    if (c.includes('fog')) return '🌫️';
    if (c.includes('wind')) return '💨';
    return '🌡️';
  };

  // Tool replies often come back as a bold heading + "- **Label:** value" bullet list;
  // pull that shape into a stat card instead of leaving raw markdown bullets on screen.
  const parseWeatherReport = text => {
    const lines = text.split(/\r?\n/);
    const titleLine = lines.find(line => /weather|forecast/i.test(line) && /\*\*/.test(line));
    const bullets = [];
    lines.forEach(line => {
      const match = line.match(/^\s*[-*]\s+\*\*([^*:]+):?\*\*:?\s*(.*)$/);
      if (match) bullets.push({ label: match[1].trim(), value: match[2].replace(/\*\*/g, '').trim() });
    });
    if (!bullets.some(b => /temp/i.test(b.label))) return null;
    const rest = lines.filter(line => line !== titleLine && !/^\s*[-*]\s+\*\*/.test(line)).join('\n').trim();
    return { title: titleLine ? titleLine.replace(/\*\*/g, '').trim() : null, bullets, rest };
  };

  // Multi-day replies (e.g. "7 day forecast") come back as a series of
  // day headers ("Monday", "Day 3", "Sat 14 Jun", ...) each followed by its
  // own stats. The single-reading card above assumes there's exactly one
  // temperature in the whole message, so on a forecast it dumps every day's
  // bullets into one flat list — that's the giant, broken-looking card.
  // Detect that shape up front and render a compact horizontal day strip
  // instead; if the shape isn't confidently multi-day, this returns null and
  // the normal single-card / plain-markdown path is used, so nothing here
  // can make an ordinary reply look worse.
  const DAY_HEADER_RE = /^\s*#{0,3}\s*\*{0,2}\s*((?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*|Day\s*\d+|Today|Tomorrow|[A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?)\s*\*{0,2}\s*:?\s*$/;
  const parseMultiDayForecast = text => {
    const lines = text.split(/\r?\n/);
    const sections = [];
    let current = null;
    lines.forEach(line => {
      const headerMatch = line.match(DAY_HEADER_RE);
      if (headerMatch) {
        current = { day: headerMatch[1].replace(/\*/g, '').trim(), lines: [] };
        sections.push(current);
      } else if (current) {
        current.lines.push(line);
      }
    });
    if (sections.length < 2) return null;
    const days = sections.map(section => {
      const bodyText = section.lines.join('\n');
      const tempMatch = bodyText.match(/(-?\d{1,3})\s?°\s?([CF])\b/);
      if (!tempMatch) return null;
      const lower = bodyText.toLowerCase();
      const condition = WEATHER_CONDITIONS.find(word => lower.includes(word));
      return { day: section.day, temp: tempMatch[1], unit: tempMatch[2], condition: condition ? titleCase(condition) : null };
    });
    if (days.some(day => !day)) return null; // heuristic wasn't confident for every section — fall back safely
    return days;
  };

  const renderForecastStrip = days => `
    <div class="forecast-strip">
      ${days.map(d => `
        <div class="forecast-day">
          <span class="forecast-day-label">${escapeHtml(d.day)}</span>
          <span class="forecast-icon">${weatherIcon(d.condition)}</span>
          <span class="forecast-temp">${d.temp}°${d.unit}</span>
          ${d.condition ? `<span class="forecast-condition">${escapeHtml(d.condition)}</span>` : ''}
        </div>`).join('')}
    </div>`;

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
    const fields = Object.entries(args)
      .filter(([key, value]) => value != null && value !== '' && !SKIPPED_APPROVAL_KEYS.has(key))
      .map(([key, value]) => `<dt>${APPROVAL_FIELD_LABELS[key] || titleCase(key)}</dt><dd>${escapeHtml(truncateText(stringifyApprovalValue(value), 600))}</dd>`)
      .join('');
    return fields ? `<dl class="approval-fields">${fields}</dl>` : '';
  };

  window.PersonalOps = {
    bindLogin() {
      document.querySelector('#login-form').addEventListener('submit', async event => {
        event.preventDefault();
        localStorage.removeItem('ops_access');
        localStorage.removeItem('ops_refresh');
        localStorage.removeItem('ops_user');
        try { const data = await api('/login/', { method: 'POST', body: JSON.stringify(formData(event.currentTarget)), public: true, skipRefresh: true }); localStorage.setItem('ops_access', data.access); localStorage.setItem('ops_refresh', data.refresh || data.refersh); localStorage.setItem('ops_user', data.username); window.location = '/'; } catch (error) { showError(error); }
      });
    },
    bindRegister() {
      document.querySelector('#register-form').addEventListener('submit', async event => {
        event.preventDefault();
        try { await api('/registration/', { method: 'POST', body: JSON.stringify(formData(event.currentTarget)), public: true, skipRefresh: true }); window.location = '/signin/'; } catch (error) { showError(error); }
      });
    },
    initSettings() {
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
    },
    initDashboard() {
      if (!localStorage.getItem('ops_access')) { window.location = '/signin/'; return; }
      const state = { threadId: null, initialThreadPicked: false, connectedServices: new Set(), messageCount: 0, sending: false, pendingApproval: null };
      const transcript = document.querySelector('#transcript');
      const input = document.querySelector('#message-input');
      const title = document.querySelector('#thread-title');
      const threadList = document.querySelector('#thread-list');
      const sendButton = document.querySelector('.send-button');
      const composerNote = document.querySelector('#composer-note');
      const banner = document.querySelector('#error-banner');
      const bannerMsg = document.querySelector('#error-banner-msg');
      const bannerRetry = document.querySelector('#error-banner-retry');
      const bannerDismiss = document.querySelector('#error-banner-dismiss');
      document.querySelector('#user-label').textContent = localStorage.getItem('ops_user') || 'Workspace online';
      const avatar = document.querySelector('#user-avatar');
      if (avatar) avatar.textContent = (localStorage.getItem('ops_user') || '?').charAt(0).toUpperCase();

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
            if (dot) dot.classList.toggle('dot-on', connected), dot.classList.toggle('dot-off', !connected);
            if (stateEl) { stateEl.textContent = connected ? 'Connected' : 'Connect'; stateEl.classList.toggle('off', !connected); }
          });
          refreshComposerState();
        } catch (error) { console.warn('Could not load integrations', error); }
      };
      document.querySelectorAll('[data-integration]').forEach(button => button.onclick = async () => { const service = button.dataset.integration; try { const data = await api(`/api/integrations/${service}/connect/`); window.location.href = data.authorization_url; } catch (error) { showBanner(error.message); } });

      const addMessage = (role, content, pending = false) => {
        const item = document.createElement('article');
        item.className = `message ${role} ${pending ? 'pending' : ''}`;
        item.innerHTML = `<span class="message-label">${role === 'user' ? 'You' : 'Ops agent'}</span><div class="message-content"></div>`;
        const body = item.querySelector('.message-content');
        const text = safeText(content); // tool/message payloads aren't guaranteed to be strings
        if (pending || role === 'user') {
          body.textContent = text;
        } else {
          const forecast = parseMultiDayForecast(text);
          const report = !forecast ? parseWeatherReport(text) : null;
          const weather = !forecast && !report ? detectWeather(text) : null;
          if (forecast) {
            const strip = document.createElement('div');
            strip.innerHTML = renderForecastStrip(forecast);
            body.appendChild(strip);
            const textWrap = document.createElement('div');
            textWrap.className = 'markdown-body';
            textWrap.innerHTML = renderMarkdown(text);
            body.appendChild(textWrap);
          } else if (report) {
            const tempBullet = report.bullets.find(b => /temp/i.test(b.label));
            const conditionBullet = report.bullets.find(b => /condition/i.test(b.label));
            const tempMatch = tempBullet && tempBullet.value.match(/(-?\d{1,3})\s?°\s?([CF])/);
            const card = document.createElement('div');
            card.className = 'weather-card';
            card.innerHTML = `<span class="weather-icon">${weatherIcon(conditionBullet && conditionBullet.value)}</span>
              <div class="weather-info">
                <div class="weather-temp">${tempMatch ? `${tempMatch[1]}°${tempMatch[2]}` : ''}</div>
                <div class="weather-meta">${escapeHtml(report.title || 'Weather')}</div>
                <div class="weather-stats">${report.bullets.filter(b => b !== tempBullet).map(b => `<div class="weather-stat"><span class="stat-label">${escapeHtml(b.label)}</span><span class="stat-value">${escapeHtml(b.value)}</span></div>`).join('')}</div>
              </div>`;
            body.appendChild(card);
            if (report.rest) { const textWrap = document.createElement('div'); textWrap.className = 'markdown-body'; textWrap.innerHTML = renderMarkdown(report.rest); body.appendChild(textWrap); }
          } else {
            if (weather) {
              const card = document.createElement('div');
              card.className = 'weather-card';
              card.innerHTML = `<span class="weather-icon">${weatherIcon(weather.condition)}</span>
                <div class="weather-info">
                  <div class="weather-temp">${weather.temp}°${weather.unit}</div>
                  <div class="weather-meta">${escapeHtml([weather.location, weather.condition].filter(Boolean).join(' • '))}</div>
                </div>`;
              body.appendChild(card);
            }
            const textWrap = document.createElement('div');
            textWrap.className = 'markdown-body';
            textWrap.innerHTML = renderMarkdown(text);
            body.appendChild(textWrap);
          }
        }
        transcript.appendChild(item);
        transcript.scrollTop = transcript.scrollHeight;
        state.messageCount += 1;
        return item;
      };

      const addApprovalCard = approval => {
        const domain = (approval && approval.domain) || 'email';
        const meta = DOMAIN_META[domain] || { icon: '⚙️', label: titleCase(domain) };
        const card = document.createElement('div');
        card.className = 'approval-card';
        const heading = approval && approval.is_duplicate
          ? `Re-run this ${meta.label.toLowerCase()} action?`
          : (approval && approval.message) || `Approve ${meta.label.toLowerCase()} action`;
        card.innerHTML = `
          <div>
            <span class="eyebrow" style="color:#a1806f">${meta.icon} Waiting on you — ${meta.label}</span>
            <h3>${escapeHtml(heading)}</h3>
            ${renderApprovalBody(approval || {})}
            <div class="approval-status hidden" aria-live="polite"></div>
          </div>
          <div class="approval-actions">
            <button class="btn btn-primary" data-action="approve">Approve</button>
            <button class="btn btn-ghost" data-action="cancel">Cancel</button>
          </div>`;
        card.querySelector('[data-action="approve"]').onclick = () => approve(true, card);
        card.querySelector('[data-action="cancel"]').onclick = () => approve(false, card);
        transcript.appendChild(card);
        transcript.scrollTop = transcript.scrollHeight;
        // Lock the composer: a new chat message would race the graph run,
        // which is paused mid-execution waiting on this exact decision.
        state.pendingApproval = card;
        refreshComposerState();
        return card;
      };

      const renderThreadSkeleton = () => { threadList.innerHTML = '<div class="thread-skeleton"><div class="sk-line"></div><div class="sk-line"></div><div class="sk-line"></div></div>'; };

      const selectThread = async id => {
        state.threadId = id;
        state.messageCount = 0;
        state.pendingApproval = null;
        refreshComposerState();
        title.textContent = 'Thread ' + id;
        document.querySelectorAll('.thread-item').forEach(item => item.classList.toggle('active', item.dataset.id == id));
        transcript.innerHTML = '<div class="loading-line">Loading thread history...</div>';
        try {
          const data = await api(`/api/thread/${encodeURIComponent(id)}/messages/`);
          const messages = Array.isArray(data) ? data : data.results || [];
          transcript.innerHTML = '';
          state.messageCount = 0;
          if (!messages.length) {
            transcript.innerHTML = '<div class="empty-state empty-state-thread"><div class="empty-orbit">+</div><h2>Nothing here yet</h2><p>Send a message below to get this thread started.</p></div>';
          } else {
            messages.forEach(message => addMessage(message.role === 'assistant' ? 'agent' : message.role, message.content));
          }
        } catch (error) {
          transcript.innerHTML = '';
          showBanner("Couldn't load that thread.", () => selectThread(id));
        }
      };

      const loadThreads = async ({ selectFirst = false } = {}) => {
        renderThreadSkeleton();
        try {
          const data = await api('/api/list_thread/');
          const threads = Array.isArray(data) ? data : data.results || [];
          threadList.innerHTML = threads.length ? '' : '<div class="rail-empty">No threads yet.<br>Start with a question.</div>';
          threads.forEach(thread => {
            const item = document.createElement('button');
            item.className = 'thread-item';
            item.dataset.id = thread.id;
            item.innerHTML = `<span class="thread-item-title"></span><span class="thread-item-meta"><span class="thread-item-time"></span></span>`;
            item.querySelector('.thread-item-title').textContent = thread.name || `Thread ${thread.id}`;
            item.querySelector('.thread-item-time').textContent = thread.updated_at ? new Intl.DateTimeFormat([], { hour: 'numeric', minute: '2-digit' }).format(new Date(thread.updated_at)) : '';
            item.onclick = () => selectThread(thread.id);
            threadList.appendChild(item);
          });
          if (selectFirst && !state.initialThreadPicked && threads[0]) { state.initialThreadPicked = true; selectThread(threads[0].id); }
          return threads;
        } catch (error) {
          threadList.innerHTML = '';
          showBanner("Couldn't load your threads.", () => loadThreads({ selectFirst }));
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
          const current = threads.find(thread => thread.id == state.threadId);
          if (!current || !current.name || current.name === 'New Thread') return;
          title.textContent = current.name;
          const item = threadList.querySelector(`.thread-item[data-id="${current.id}"] .thread-item-title`);
          if (item) item.textContent = current.name;
          else await loadThreads();
        } catch (error) { /* non-critical, next send will retry */ }
      };

      const send = async message => {
        if (state.sending || state.pendingApproval) return; // ignore double-submits and racing an open approval
        state.sending = true;
        refreshComposerState();
        const pending = addMessage('agent', 'Thinking through that now...', true);
        try {
          const endpoint = state.threadId ? `/api/thread/${state.threadId}/chat/` : '/api/chat/';
          const data = await api(endpoint, { method: 'POST', body: JSON.stringify({ message }) });
          pending.remove();
          if (data.thread_id && !state.threadId) { state.threadId = data.thread_id; title.textContent = 'New thread'; await loadThreads(); }
          if (data.status === 'approval_required') addApprovalCard(data.approval);
          else addMessage('agent', data.response || 'Done.');
          if (state.messageCount >= 3) syncThreadName();
        } catch (error) {
          pending.remove();
          showBanner("Couldn't reach the server just now.", () => send(message));
        } finally {
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
      document.querySelectorAll('[data-prompt]').forEach(button => button.onclick = () => { input.value = button.dataset.prompt; autoGrowTextarea(input); input.focus(); });
      document.querySelector('#new-thread').onclick = () => {
        if (state.pendingApproval) return; // don't abandon an open approval mid-decision
        state.threadId = null;
        state.messageCount = 0;
        title.textContent = 'Good morning.';
        transcript.innerHTML = '';
        document.querySelectorAll('.thread-item').forEach(item => item.classList.remove('active'));
        input.focus();
      };
      document.querySelector('#logout').onclick = () => { localStorage.removeItem('ops_access'); localStorage.removeItem('ops_refresh'); localStorage.removeItem('ops_user'); window.location = '/signin/'; };

      async function approve(value, card) {
        const actions = card.querySelector('.approval-actions');
        const status = card.querySelector('.approval-status');
        actions.querySelectorAll('button').forEach(button => button.disabled = true);
        card.classList.add('is-busy');
        status.classList.remove('hidden', 'status-error', 'status-success');
        status.innerHTML = `<span class="spinner"></span> ${value ? 'Approving' : 'Cancelling'}…`;
        try {
          const data = await api(`/api/thread/${state.threadId}/action-email/`, { method: 'POST', body: JSON.stringify({ approved: value }) });
          card.classList.remove('is-busy');
          status.classList.add('status-success');
          status.textContent = value ? '✓ Approved — sending now.' : '✓ Cancelled.';
          state.pendingApproval = null;
          refreshComposerState();
          addMessage('agent', data.result || (value ? 'Approved — sending now.' : 'Cancelled.'));
          setTimeout(() => card.remove(), 600);
        } catch (error) {
          card.classList.remove('is-busy');
          actions.querySelectorAll('button').forEach(button => button.disabled = false);
          status.classList.add('status-error');
          status.textContent = `Couldn't record your decision — ${error.message}`;
        }
      }

      window.addEventListener('online', refreshComposerState);
      window.addEventListener('offline', refreshComposerState);

      document.querySelector('#clock').textContent = new Intl.DateTimeFormat([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date());
      autoGrowTextarea(input);
      // Restore a message the user was mid-typing if a session refresh forced a redirect.
      const savedDraft = sessionStorage.getItem('ops_draft');
      if (savedDraft) { sessionStorage.removeItem('ops_draft'); input.value = savedDraft; autoGrowTextarea(input); }
      loadIntegrations();
      loadThreads({ selectFirst: true });
    }
  };
})();
