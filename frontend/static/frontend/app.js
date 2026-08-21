(() => {
  const api = async (path, options = {}) => {
    const request = async () => {
      const token = localStorage.getItem('ops_access');
      const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
      if (token && !options.public) headers.Authorization = `Bearer ${token}`;
      return fetch(path, { ...options, headers });
    };
    let response = await request();
    if (response.status === 401 && !options.skipRefresh) {
      const refresh = localStorage.getItem('ops_refresh');
      if (refresh) {
        const refreshResponse = await fetch('/api/token/refresh/', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh })
        });
        if (refreshResponse.ok) {
          const refreshData = await refreshResponse.json();
          localStorage.setItem('ops_access', refreshData.access);
          response = await request();
        } else {
          localStorage.removeItem('ops_access');
          localStorage.removeItem('ops_refresh');
        }
      }
      if (response.status === 401) {
        // Session can't be recovered — send the user back to sign in instead of
        // leaving the workspace showing a stale/blank thread list and history.
        localStorage.removeItem('ops_access');
        localStorage.removeItem('ops_refresh');
        localStorage.removeItem('ops_user');
        window.location = '/signin/';
        throw new Error('Session expired. Redirecting to sign in...');
      }
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || Object.values(data).flat().join(' ') || 'Something went wrong.');
    return data;
  };
  const formData = form => Object.fromEntries(new FormData(form).entries());
  const showError = error => { const target = document.querySelector('#form-error'); if (target) target.textContent = error.message; };

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

  const renderApprovalBody = approval => {
    const args = approval.args || {};
    if (approval.domain === 'calendar' && (args.summary || args.start_time)) return renderCalendarApproval(args);
    const fields = Object.entries(args)
      .filter(([key, value]) => value != null && value !== '' && !SKIPPED_APPROVAL_KEYS.has(key))
      .map(([key, value]) => `<dt>${APPROVAL_FIELD_LABELS[key] || titleCase(key)}</dt><dd>${escapeHtml(Array.isArray(value) ? value.join(', ') : String(value))}</dd>`)
      .join('');
    return `<dl class="approval-fields">${fields}</dl>`;
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
      const state = { threadId: null, initialThreadPicked: false, connectedServices: new Set(), messageCount: 0 };
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

      const updateComposerNote = () => {
        const count = state.connectedServices.size;
        composerNote.textContent = count
          ? `Enter to send • ${count} tool${count === 1 ? '' : 's'} connected`
          : 'Enter to send • No tools connected yet — try Gmail first';
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
          updateComposerNote();
        } catch (error) { console.warn('Could not load integrations', error); }
      };
      document.querySelectorAll('[data-integration]').forEach(button => button.onclick = async () => { const service = button.dataset.integration; try { const data = await api(`/api/integrations/${service}/connect/`); window.location.href = data.authorization_url; } catch (error) { showBanner(error.message); } });

      const addMessage = (role, content, pending = false) => {
        const item = document.createElement('article');
        item.className = `message ${role} ${pending ? 'pending' : ''}`;
        item.innerHTML = `<span class="message-label">${role === 'user' ? 'You' : 'Ops agent'}</span><div class="message-content"></div>`;
        const body = item.querySelector('.message-content');
        if (pending || role === 'user') {
          body.textContent = content;
        } else {
          const report = parseWeatherReport(content);
          const weather = !report ? detectWeather(content) : null;
          if (report) {
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
            textWrap.innerHTML = renderMarkdown(content);
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
          </div>
          <div class="approval-actions">
            <button class="btn btn-primary">Approve</button>
            <button class="btn btn-ghost">Cancel</button>
          </div>`;
        card.querySelector('.btn-primary').onclick = () => approve(true, card);
        card.querySelector('.btn-ghost').onclick = () => approve(false, card);
        transcript.appendChild(card);
        transcript.scrollTop = transcript.scrollHeight;
        return card;
      };

      const setComposerBusy = busy => { input.disabled = busy; sendButton.disabled = busy; };

      const renderThreadSkeleton = () => { threadList.innerHTML = '<div class="thread-skeleton"><div class="sk-line"></div><div class="sk-line"></div><div class="sk-line"></div></div>'; };

      const selectThread = async id => {
        state.threadId = id;
        state.messageCount = 0;
        title.textContent = 'Thread ' + id;
        document.querySelectorAll('.thread-item').forEach(item => item.classList.toggle('active', item.dataset.id == id));
        transcript.innerHTML = '<div class="loading-line">Loading thread history...</div>';
        try {
          const messages = await api(`/api/thread/${encodeURIComponent(id)}/messages/`);
          transcript.innerHTML = '';
          state.messageCount = 0;
          (Array.isArray(messages) ? messages : messages.results || []).forEach(message => addMessage(message.role === 'assistant' ? 'agent' : message.role, message.content));
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
        setComposerBusy(true);
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
          setComposerBusy(false);
          input.focus();
        }
      };

      document.querySelector('#chat-form').addEventListener('submit', event => { event.preventDefault(); const message = input.value.trim(); if (!message) return; addMessage('user', message); input.value = ''; send(message); });
      input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); document.querySelector('#chat-form').requestSubmit(); } });
      document.querySelectorAll('[data-prompt]').forEach(button => button.onclick = () => { input.value = button.dataset.prompt; input.focus(); });
      document.querySelector('#new-thread').onclick = () => { state.threadId = null; state.messageCount = 0; title.textContent = 'Good morning.'; transcript.innerHTML = ''; document.querySelectorAll('.thread-item').forEach(item => item.classList.remove('active')); input.focus(); };
      document.querySelector('#logout').onclick = () => { localStorage.removeItem('ops_access'); localStorage.removeItem('ops_refresh'); localStorage.removeItem('ops_user'); window.location = '/signin/'; };

      async function approve(value, card) {
        const actions = card.querySelector('.approval-actions');
        actions.querySelectorAll('button').forEach(button => button.disabled = true);
        try {
          const data = await api(`/api/thread/${state.threadId}/action-email/`, { method: 'POST', body: JSON.stringify({ approved: value }) });
          card.remove();
          addMessage('agent', data.result || (value ? 'Approved — sending now.' : 'Cancelled.'));
        } catch (error) {
          actions.querySelectorAll('button').forEach(button => button.disabled = false);
          showBanner("Couldn't record your decision.", () => approve(value, card));
        }
      }

      document.querySelector('#clock').textContent = new Intl.DateTimeFormat([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date());
      loadIntegrations();
      loadThreads({ selectFirst: true });
    }
  };
})();
