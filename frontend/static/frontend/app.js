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

  const renderMarkdown = raw => {
    const source = String(raw == null ? '' : raw).replace(/\r\n/g, '\n');
    const inline = line => {
      let text = escapeHtml(line);
      text = text.replace(/\[([^\]]+?)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
      text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
      text = text.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
      return text;
    };
    
    let html = '';
    let listType = null;
    let codeBlockOpen = false;
    let codeBlockLang = '';
    let codeBlockContent = '';
    
    const closeList = () => { if (listType) { html += `</${listType}>`; listType = null; } };
    const closeCodeBlock = () => {
      if (codeBlockOpen) {
        const langClass = codeBlockLang ? ` class="language-${escapeHtml(codeBlockLang)}"` : '';
        html += `<pre><code${langClass}>${escapeHtml(codeBlockContent)}</code></pre>`;
        codeBlockOpen = false;
        codeBlockLang = '';
        codeBlockContent = '';
      }
    };
    
    const lines = source.split('\n');
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      
      // Handle code fences
      if (line.match(/^```/)) {
        closeList();
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
        closeCodeBlock();
        const headerRow = line.split('|').map(cell => cell.trim()).filter(cell => cell || (line.startsWith('|') && line.endsWith('|')));
        const separatorRow = lines[i + 1];
        const tableRows = [headerRow];
        
        let j = i + 2;
        while (j < lines.length && lines[j].includes('|')) {
          const row = lines[j].split('|').map(cell => cell.trim()).filter((cell, idx) => cell || (lines[j].startsWith('|') && lines[j].endsWith('|')) || idx < headerRow.length);
          if (row.length === headerRow.length || row.some(cell => cell)) {
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
        closeCodeBlock();
        const level = headingMatch[1].length;
        html += `<h${level}>${inline(headingMatch[2])}</h${level}>`;
        i++;
        continue;
      }
      
      // Handle blockquotes
      if (line.match(/^>\s/)) {
        closeList();
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
        closeCodeBlock();
        html += '<hr>';
        i++;
        continue;
      }
      
      // Handle bullet lists
      const bullet = line.match(/^\s*[-*]\s+(.*)/);
      if (bullet) {
        closeCodeBlock();
        if (listType !== 'ul') { closeList(); html += '<ul>'; listType = 'ul'; }
        html += `<li>${inline(bullet[1])}</li>`;
        i++;
        continue;
      }
      
      // Handle numbered lists
      const numbered = line.match(/^\s*\d+\.\s+(.*)/);
      if (numbered) {
        closeCodeBlock();
        if (listType !== 'ol') { closeList(); html += '<ol>'; listType = 'ol'; }
        html += `<li>${inline(numbered[1])}</li>`;
        i++;
        continue;
      }
      
      // Handle paragraphs
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
    closeCodeBlock();
    return html;
  };

  const renderWeatherCardFromText = text => {
    const structured = parseStructuredWeatherBlock(text);
    if (!structured) return null;

    const statEntries = structured.entries.map(([label, value]) => {
      return `<div class="weather-stat"><span class="stat-label">${escapeHtml(label)}</span><span class="stat-value">${escapeHtml(value)}</span></div>`;
    });

    const temp = structured.temp ? `${structured.temp}°${structured.unit}` : '—';
    return `
      <div class="weather-card">
        <span class="weather-icon">${weatherIcon(structured.condition)}</span>
        <div class="weather-info">
          <div class="weather-temp">${escapeHtml(temp)}</div>
          <div class="weather-meta">${escapeHtml(structured.location)} • ${escapeHtml(String(structured.condition || 'Weather'))}</div>
          ${statEntries.length ? `<div class="weather-stats">${statEntries.join('')}</div>` : ''}
        </div>
      </div>`;
  };

  const WEATHER_CONDITIONS = [
    'thunderstorm', 'thunderstorms', 'partly cloudy', 'mostly cloudy', 'overcast', 'drizzle', 'showers', 'sunny',
    'clear skies', 'clear weather', 'cloudy', 'rain', 'rainy', 'storm', 'storms', 'snow', 'snowy', 'fog', 'mist',
    'haze', 'windy', 'hail', 'humid', 'heatwave', 'hot', 'freezing', 'cold'
  ];
  const extractTemperature = text => {
    const direct = text.match(/(-?\d{1,3})\s*(?:°\s*|degrees?\s*)?([CF])\b/i) || text.match(/(-?\d{1,3})\s*degrees?\s*(?:celsius|fahrenheit)\b/i);
    if (direct) {
      if (direct[2]) return { temp: direct[1], unit: direct[2].toUpperCase() };
      return { temp: direct[1], unit: /fahrenheit/i.test(direct[0]) ? 'F' : 'C' };
    }
    return null;
  };
  const detectWeather = text => {
    const raw = safeText(text);
    if (!raw) return null;
    const lower = raw.toLowerCase();
    const temp = extractTemperature(raw);
    if (!temp) return null;

    // Check for weather context signals
    const hasWeatherContext = /(weather|forecast|today|tomorrow|day\s*\d+|mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|condition|temperature|celsius|fahrenheit)/i.test(lower);
    if (!hasWeatherContext) return null;

    // Try to extract location - be more flexible with patterns
    let location = null;
    const locationMatch = raw.match(/(?:weather\s+(?:in|for)\s+|in\s+|for\s+)([A-Za-z][A-Za-z\s,.'-]+?)(?:\s*(?:–|[-–]|weather|forecast|is|will|today|tomorrow|on|for|$))/i)
      || raw.match(/([A-Z][a-z]+(?:\s*,\s*[A-Za-z]+)?)\s*(?:–|weather|forecast|[:])/i);
    
    if (locationMatch) {
      location = locationMatch[1].replace(/\s+/g, ' ').trim().replace(/[.,\-–]+$/, '');
    }

    // Extract condition if mentioned
    const condition = WEATHER_CONDITIONS.find(c => lower.includes(c));
    
    return {
      temp: temp.temp,
      unit: temp.unit,
      condition: condition ? titleCase(condition) : null,
      location: location || 'Current location',
    };
  };
  const weatherIcon = condition => {
    const c = (condition || '').toLowerCase();
    if (c.includes('sun') || c.includes('clear')) return '☀️';
    if (c.includes('cloud') || c.includes('overcast')) return '☁️';
    if (c.includes('rain') || c.includes('shower') || c.includes('drizzle')) return '🌧️';
    if (c.includes('storm') || c.includes('thunder')) return '⛈️';
    if (c.includes('snow') || c.includes('hail')) return '❄️';
    if (c.includes('fog') || c.includes('mist') || c.includes('haze')) return '🌫️';
    if (c.includes('wind')) return '💨';
    if (c.includes('humid') || c.includes('heatwave') || c.includes('hot')) return '🥵';
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
  const DAY_HEADER_RE = /^\s*#{0,3}\s*(?:\*{0,2})\s*(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)|Today|Tomorrow|Day\s*\d+|(?:[A-Z][a-z]+,\s*[A-Z][a-z]+\s+\d{1,2})|(?:\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))|(?:[A-Z][a-z]+\s+\d{1,2}(?:st|nd|rd|th)?))\s*(?:\*{0,2})\s*:??\s*$/i;
  const parseMultiDayForecast = text => {
    const raw = safeText(text);
    const lower = raw.toLowerCase();
    if (!/(forecast|today|tomorrow|day\s*\d+|mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)/i.test(lower)) return null;
    const lines = raw.split(/\r?\n/);
    const sections = [];
    let current = null;
    lines.forEach(line => {
      const headerMatch = line.match(DAY_HEADER_RE);
      if (headerMatch) {
        current = { day: line.replace(/^\s*#{0,3}\s*(?:\*{0,2})\s*/, '').replace(/\s*(?:\*{0,2})\s*:??\s*$/, '').trim(), lines: [] };
        sections.push(current);
      } else if (current) {
        current.lines.push(line);
      }
    });
    if (sections.length < 2) return null;
    const days = sections.map(section => {
      const bodyText = section.lines.join('\n');
      const tempMatch = extractTemperature(bodyText) || extractTemperature(section.day);
      if (!tempMatch) return null;
      const condition = WEATHER_CONDITIONS.find(word => bodyText.toLowerCase().includes(word) || section.day.toLowerCase().includes(word));
      return { day: section.day, temp: tempMatch.temp, unit: tempMatch.unit, condition: condition ? titleCase(condition) : null };
    });
    if (days.some(day => !day)) return null;
    return days;
  };

  const parseStructuredWeatherBlock = text => {
    const raw = safeText(text);
    if (!raw) return null;
    
    // First try to extract temperature (always required for weather card)
    const tempMatch = extractTemperature(raw);
    if (!tempMatch) return null;
    
    // Check if text contains weather context
    const hasWeatherContext = /(weather|forecast|today|tomorrow|condition|temperature|°[cf])/i.test(raw);
    if (!hasWeatherContext) return null;

    // Parse key-value pairs from structured format (if present)
    const rows = [];
    raw.split(/\r?\n/).forEach(line => {
      const cleaned = line.replace(/^\s*[|\-•*\s]+/, '').replace(/\s*[|]\s*$/, '').trim();
      if (!cleaned) return;
      
      // Try pipe-separated format
      const match = cleaned.match(/^([^|]+?)\s*[|]\s*(.+)$/);
      if (match) {
        const label = match[1].replace(/^\s*I\s+/i, '').replace(/\s*[:=-]$/, '').trim();
        const value = match[2].replace(/\s*I\s*$/i, '').trim();
        if (label && value) rows.push([label, value]);
        return;
      }
      
      // Try key: value format
      const kv = cleaned.match(/^(temperature|feels like|high|low|condition|humidity|wind|pressure|precipitation|rain)\s*[:=-]\s*(.+)$/i);
      if (kv) rows.push([kv[1], kv[2].trim()]);
    });
    
    const statMap = {};
    rows.forEach(([label, value]) => {
      const key = label.toLowerCase();
      statMap[key] = value;
    });

    // Extract location from text
    let location = null;
    const locMatch = raw.match(/([A-Z][a-z]+(?:\s*,\s*[A-Za-z]+)?)\s*(?:–|weather|forecast|[:])/);
    if (locMatch) {
      location = locMatch[1].trim();
    }
    if (!location) {
      const locMatch2 = raw.match(/(?:in|for)\s+([A-Za-z][A-Za-z\s,.'-]+?)(?:\s*(?:–|weather|forecast|is|will|$))/i);
      if (locMatch2) location = locMatch2[1].trim();
    }

    // Determine condition
    const condition = WEATHER_CONDITIONS.find(c => raw.toLowerCase().includes(c)) || 
                     (statMap.condition ? statMap.condition : null);

    // Collect relevant fields to display
    const relevantFields = [];
    if (tempMatch.temp) {
      relevantFields.push(['Temperature', `${tempMatch.temp}°${tempMatch.unit}`]);
    }
    if (statMap['feels like']) relevantFields.push(['Feels like', statMap['feels like']]);
    if (statMap['high']) relevantFields.push(['High', statMap['high']]);
    if (statMap['low']) relevantFields.push(['Low', statMap['low']]);
    if (statMap['humidity']) relevantFields.push(['Humidity', statMap['humidity']]);
    if (statMap['wind']) relevantFields.push(['Wind', statMap['wind']]);
    if (statMap['precipitation'] || statMap['rain']) {
      relevantFields.push(['Rain', statMap['precipitation'] || statMap['rain']]);
    }

    return {
      temp: tempMatch.temp,
      unit: tempMatch.unit,
      condition: condition ? titleCase(condition) : 'Weather',
      location: location || 'Current location',
      entries: relevantFields.slice(0, 6)
    };
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
      const state = { threadId: null, initialThreadPicked: false, connectedServices: new Set(), messageCount: 0, sending: false, pendingApproval: null, streamRequestId: 0, abortController: null };
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
        if (pending) {
          body.innerHTML = '<div class="pending-agent"><span class="dot-flash"><i></i><i></i><i></i></span><span>Processing request…</span></div>';
        } else if (role === 'user') {
          body.textContent = text;
        } else {
          const forecast = parseMultiDayForecast(text);
          const report = !forecast ? parseWeatherReport(text) : null;
          const weather = !forecast && !report ? detectWeather(text) : null;
          const structuredWeather = !forecast && !report && !weather ? renderWeatherCardFromText(text) : null;

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
          } else if (structuredWeather) {
            body.innerHTML = structuredWeather;
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

      const selectThread = async id => {
        cancelActiveStream();
        state.threadId = id;
        state.messageCount = 0;
        state.pendingApproval = null;
        state.sending = false;
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

      const deleteThread = async threadId => {
        if (!threadId) return;
        const confirmed = window.confirm('Delete this thread?');
        if (!confirmed) return;

        try {
          await api(`/api/thread/${encodeURIComponent(threadId)}/delete/`, { method: 'DELETE' });
          if (state.threadId === threadId) {
            state.threadId = null;
            state.pendingApproval = null;
            title.textContent = 'Good morning.';
            transcript.innerHTML = '';
            document.querySelectorAll('.thread-item').forEach(item => item.classList.remove('active'));
          }
          await loadThreads({ selectFirst: !state.threadId });
        } catch (error) {
          showBanner(error.message || "Couldn't delete that thread.", () => deleteThread(threadId));
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
            item.type = 'button';
            item.innerHTML = `<span class="thread-item-title"></span><span class="thread-item-meta"><span class="thread-item-time"></span><span class="thread-delete" aria-label="Delete thread" title="Delete thread">×</span></span>`;
            item.querySelector('.thread-item-title').textContent = thread.name || `Thread ${thread.id}`;
            item.querySelector('.thread-item-time').textContent = thread.updated_at ? new Intl.DateTimeFormat([], { hour: 'numeric', minute: '2-digit' }).format(new Date(thread.updated_at)) : '';
            const deleteButton = item.querySelector('.thread-delete');
            deleteButton.onclick = event => {
              event.preventDefault();
              event.stopPropagation();
              deleteThread(thread.id);
            };
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
    if (state.sending || state.pendingApproval || !navigator.onLine) return;
    const streamId = ++state.streamRequestId;
    const requestController = new AbortController();
    state.abortController = requestController;
    state.sending = true;
    refreshComposerState();

    const pending = addMessage('agent', '', true);
    const body = pending.querySelector('.message-content');
    const setPendingStatus = label => {
      if (!body) return;
      body.innerHTML = `<div class="pending-agent"><span class="dot-flash"><i></i><i></i><i></i></span><span>${escapeHtml(label)}</span></div>`;
    };
    const currentThreadId = state.threadId;
    let assistantText = '';
    let completedHandled = false;
    let hasReceivedTokens = false;

    const handlePayload = data => {
      if (!data || typeof data !== 'object') return;
      const status = data.status || data.type || '';
      const token = data.token ?? data.delta ?? data.chunk ?? data.content ?? data.text ?? '';
      const response = data.response ?? data.result ?? data.output ?? data.content ?? data.text ?? '';
      const approval = data.approval ?? data.tool_approval ?? data.action ?? data.request ?? {};
      const message = data.message ?? '';

      // Handle new explicit status protocol from backend
      if (data.type === 'status') {
        if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
        // Display the backend-provided status message in the pending agent message
        // Use the backend message as authoritative
        if (message) {
          setPendingStatus(message);
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
        body.textContent = assistantText;
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

      if (data.type === 'approval_required' || status === 'approval_required') {
        if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
        setPendingStatus('Waiting for approval…');
        pending.remove();
        addApprovalCard(approval || {});
        return 'approval';
      }

      if (data.type === 'completed' || status === 'completed') {
        if (streamId !== state.streamRequestId || currentThreadId !== state.threadId) return;
        completedHandled = true;
        const finalText = safeText(typeof response === 'string' ? response : response && typeof response === 'object' ? (response.content || response.text || JSON.stringify(response)) : assistantText);
        assistantText = finalText;
        pending.remove();
        addMessage('agent', assistantText);
        if (state.messageCount >= 3) syncThreadName();
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
      const endpoint = state.threadId ? `/api/thread/${state.threadId}/chat/` : '/api/chat/';
      const response = await fetchWithAuth(endpoint, {
        method: 'POST',
        headers: { 'Accept': 'text/event-stream' },
        body: JSON.stringify({ message }),
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
      document.querySelectorAll('[data-prompt]').forEach(button => button.onclick = () => { input.value = button.dataset.prompt; autoGrowTextarea(input); input.focus(); });
      document.querySelector('#new-thread').onclick = () => {
        if (state.pendingApproval) return; // don't abandon an open approval mid-decision
        cancelActiveStream();
        state.threadId = null;
        state.messageCount = 0;
        state.sending = false;
        title.textContent = 'Good morning.';
        transcript.innerHTML = '';
        document.querySelectorAll('.thread-item').forEach(item => item.classList.remove('active'));
        refreshComposerState();
        input.focus();
      };
      document.querySelector('#logout').onclick = () => { localStorage.removeItem('ops_access'); localStorage.removeItem('ops_refresh'); localStorage.removeItem('ops_user'); window.location = '/signin/'; };

      async function approve(value, card) {
        if (!card || card.dataset.busy === 'true' || state.pendingApproval !== card) return;
        const actions = card.querySelector('.approval-actions');
        const status = card.querySelector('.approval-status');
        card.dataset.busy = 'true';
        actions.querySelectorAll('button').forEach(button => button.disabled = true);
        card.classList.add('is-busy');
        status.classList.remove('hidden', 'status-error', 'status-success');
        status.innerHTML = `<span class="spinner"></span> ${value ? 'Approving' : 'Cancelling'}…`;
        try {
          const threadId = state.threadId;
          if (!threadId) {
            throw new Error('This approval is no longer attached to an active thread.');
          }
          const data = await api(`/api/thread/${threadId}/tool-approval/`, { method: 'POST', body: JSON.stringify({ approved: value }) });
          if (state.pendingApproval !== card) return;
          card.classList.remove('is-busy');
          status.classList.add('status-success');
          status.textContent = value ? '✓ Approved — sending now.' : '✓ Cancelled.';
          state.pendingApproval = null;
          refreshComposerState();
          addMessage('agent', data.result || (value ? 'Approved — sending now.' : 'Cancelled.'));
          setTimeout(() => { if (card && card.isConnected) card.remove(); }, 600);
        } catch (error) {
          card.classList.remove('is-busy');
          actions.querySelectorAll('button').forEach(button => button.disabled = false);
          status.classList.add('status-error');
          status.textContent = `Couldn't record your decision — ${error.message}`;
          delete card.dataset.busy;
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
      // Restore a message the user was mid-typing if a session refresh forced a redirect.
      const savedDraft = sessionStorage.getItem('ops_draft');
      if (savedDraft) { sessionStorage.removeItem('ops_draft'); input.value = savedDraft; autoGrowTextarea(input); }
      loadIntegrations();
      loadThreads({ selectFirst: true });
    }
  };
})();
