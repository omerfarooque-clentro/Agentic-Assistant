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

  const WEATHER_THEMES = {
    sunny: { theme: 'sunny', icon: '☀️', name: 'Sunny & Clear' },
    hot: { theme: 'hot', icon: '☀️', name: 'Hot & Sunny' },
    partly_cloudy: { theme: 'partly-cloudy', icon: '⛅', name: 'Partly Cloudy' },
    cloudy: { theme: 'cloudy', icon: '☁️', name: 'Overcast' },
    rain: { theme: 'rain', icon: '🌧️', name: 'Rainy' },
    shower: { theme: 'rain', icon: '🌦️', name: 'Showers' },
    storm: { theme: 'storm', icon: '⛈️', name: 'Thunderstorm' },
    snow: { theme: 'snow', icon: '❄️', name: 'Snow' },
    fog: { theme: 'fog', icon: '🌫️', name: 'Hazy & Mist' },
    windy: { theme: 'windy', icon: '💨', name: 'Breezy' },
    cold: { theme: 'cold', icon: '🧊', name: 'Cold' },
  };

  const resolveWeatherCondition = text => {
    const lower = (text || '').toLowerCase();
    if (/thunder|lightning|severe storm/i.test(lower)) return WEATHER_THEMES.storm;
    if (/heavy rain|downpour|torrential/i.test(lower)) return WEATHER_THEMES.rain;
    if (/rain|drizzle|shower|precipitation/i.test(lower)) return WEATHER_THEMES.shower;
    if (/blizzard|snow|flurr|sleet|hail|freezing/i.test(lower)) return WEATHER_THEMES.snow;
    if (/heatwave|extreme heat|scorching|4[0-9]\s*°/i.test(lower) || (lower.includes('hot') && !lower.includes('shot'))) return WEATHER_THEMES.hot;
    if (/partly cloudy|scattered clouds|mostly sunny|partly sunny/i.test(lower)) return WEATHER_THEMES.partly_cloudy;
    if (/mostly cloudy|overcast|cloudy|clouds/i.test(lower)) return WEATHER_THEMES.cloudy;
    if (/fog|mist|haze|smog|dust|smoke/i.test(lower)) return WEATHER_THEMES.fog;
    if (/wind|breeze|breezy|gale|gust/i.test(lower)) return WEATHER_THEMES.windy;
    if (/cold|chilly|frost/i.test(lower)) return WEATHER_THEMES.cold;
    if (/sunny|clear|fair/i.test(lower)) return WEATHER_THEMES.sunny;
    return { theme: 'default', icon: '🌡️', name: 'Weather Outlook' };
  };

  const weatherIcon = condition => resolveWeatherCondition(condition).icon;

  const extractTemperature = text => {
    const direct = text.match(/(-?\d{1,3})\s*(?:°\s*|degrees?\s*)?([CF])\b/i) || text.match(/(-?\d{1,3})\s*degrees?\s*(?:celsius|fahrenheit)\b/i);
    if (direct) {
      if (direct[2]) return { temp: direct[1], unit: direct[2].toUpperCase() };
      return { temp: direct[1], unit: /fahrenheit/i.test(direct[0]) ? 'F' : 'C' };
    }
    return null;
  };

  const cleanMetricItem = (label, rawValue) => {
    const raw = String(rawValue || '').trim();
    const lowerLabel = label.toLowerCase();
    
    let value = raw;
    let subtext = '';
    let icon = '📊';

    if (/precip|rain|chance of rain/i.test(lowerLabel)) {
      icon = '💧';
      const pctMatch = raw.match(/(\d{1,3}\s*%)/);
      if (pctMatch) {
        value = pctMatch[1];
        if (/unlikely|0%|no rain/i.test(raw)) subtext = 'Rain unlikely';
        else if (/possible|light|scattered/i.test(raw)) subtext = 'Possible showers';
        else if (/high|heavy|likely/i.test(raw)) subtext = 'Rain expected';
        else subtext = 'Precipitation chance';
      } else if (/unlikely|none|zero|no/i.test(raw)) {
        value = '0%';
        subtext = 'Clear & dry';
      } else if (raw.length > 25) {
        value = raw.slice(0, 18) + '…';
        subtext = raw;
      }
    } else if (/wind/i.test(lowerLabel)) {
      icon = '💨';
      const speedMatch = raw.match(/(\d{1,3}(?:\s*[-–]\s*\d{1,3})?\s*(?:km\/h|mph|kts|m\/s|kmh))/i);
      const dirMatch = raw.match(/(south-east|south-west|north-east|north-west|south|north|east|west|se|sw|ne|nw|s\/w|s\/e|n\/w|n\/e)\b/i);
      if (speedMatch) {
        value = speedMatch[1].replace(/\s*kmh/i, ' km/h');
        if (dirMatch) {
          const dir = dirMatch[1].toUpperCase();
          subtext = `Breeze (${dir})`;
        } else if (/light|gentle/i.test(raw)) {
          subtext = 'Light breeze';
        } else if (/moderate/i.test(raw)) {
          subtext = 'Moderate breeze';
        } else if (/strong|gust/i.test(raw)) {
          subtext = 'Strong gusts';
        } else {
          subtext = 'Wind speed';
        }
      } else if (/light|calm/i.test(raw)) {
        value = 'Calm';
        subtext = 'Light breezes';
      } else if (raw.length > 25) {
        value = raw.slice(0, 18) + '…';
        subtext = raw;
      }
    } else if (/humid/i.test(lowerLabel)) {
      icon = '💦';
      const rangeMatch = raw.match(/(\d{1,2}%?\s*(?:to|-|–)\s*\d{1,2}%?)/i) || raw.match(/(\d{1,3}\s*%)/);
      if (rangeMatch) {
        value = rangeMatch[1].replace(/\b(\d{1,2})\b(?!\s*%)/g, '$1%');
        if (/low|dry/i.test(raw)) subtext = 'Comfortable / dry';
        else if (/mid|moderate/i.test(raw)) subtext = 'Moderate humidity';
        else if (/high|oppressive|heavy/i.test(raw)) subtext = 'Humid';
        else subtext = 'Relative humidity';
      } else if (/mid-40|low-50|moderate/i.test(raw)) {
        value = '40–50%';
        subtext = 'Moderate humidity';
      } else if (raw.length > 25) {
        value = raw.slice(0, 18) + '…';
        subtext = raw;
      }
    } else if (/uv/i.test(lowerLabel)) {
      icon = '☀️';
      const levelMatch = raw.match(/\b(very high|extreme|high|moderate|low)\b/i) || raw.match(/\b(\d{1,2}(?:\s*[-–]\s*\d{1,2})?)\b/);
      if (levelMatch) {
        value = titleCase(levelMatch[1]);
        if (/sunscreen|shade|outdoors/i.test(raw)) subtext = 'Sunscreen advised';
        else if (/high|extreme/i.test(value)) subtext = 'High protection';
        else subtext = 'UV radiation';
      } else if (/high/i.test(raw)) {
        value = 'High';
        subtext = 'Sunscreen advised';
      } else if (raw.length > 25) {
        value = raw.slice(0, 18) + '…';
        subtext = raw;
      }
    } else if (/temp|feels like|high|low|pressure|visibility/i.test(lowerLabel)) {
      if (/feels/i.test(lowerLabel)) icon = '🌡️';
      else if (/pressure/i.test(lowerLabel)) icon = '⏱️';
      else if (/visib/i.test(lowerLabel)) icon = '👁️';
      else icon = '🌡️';
      if (raw.length > 25) {
        value = raw.slice(0, 20) + '…';
        subtext = raw;
      }
    }

    return { label, value, subtext, icon };
  };

  const parseMarkdownTableForecast = lines => {
    const tableLineIdxs = [];
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].includes('|')) {
        tableLineIdxs.push(i);
      }
    }
    if (tableLineIdxs.length < 3) return null;

    const tableLines = [];
    for (let i = 0; i < tableLineIdxs.length; i++) {
      if (i > 0 && tableLineIdxs[i] !== tableLineIdxs[i-1] + 1) break;
      tableLines.push(lines[tableLineIdxs[i]]);
    }
    if (tableLines.length < 3) return null;

    const cleanRow = line => {
      let c = line.trim();
      if (c.startsWith('|')) c = c.slice(1);
      if (c.endsWith('|')) c = c.slice(0, -1);
      return c.split('|').map(cell => cell.trim());
    };

    const header = cleanRow(tableLines[0]).map(h => h.toLowerCase());
    const isForecastTable = header.some(h => /day|date|forecast|condition|high|temp|rain|weather/i.test(h));
    if (!isForecastTable) return null;

    const dayCol = header.findIndex(h => /^day\b/i.test(h));
    const dateCol = header.findIndex(h => /^date\b/i.test(h));
    const condCol = header.findIndex(h => /forecast|condition|weather|sky/i.test(h));
    const tempCol = header.findIndex(h => /high\s*\/\s*low|high|temp|max\s*\/\s*min/i.test(h));
    const rainCol = header.findIndex(h => /rain|precip|chance/i.test(h));
    const windCol = header.findIndex(h => /wind/i.test(h));
    const notesCol = header.findIndex(h => /notes?|summary|details?/i.test(h));

    const days = [];
    for (let r = 2; r < tableLines.length; r++) {
      const row = cleanRow(tableLines[r]);
      if (row.length < 2) continue;
      
      const rawDay = dayCol >= 0 ? row[dayCol] : (dateCol >= 0 ? row[dateCol] : row[0]);
      if (!rawDay) continue;
      
      const dayShort = (rawDay.match(/\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*/i) || [rawDay])[0].slice(0, 3).toUpperCase();
      const dateVal = dateCol >= 0 ? row[dateCol] : (rawDay.replace(/\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b/i, '').trim());
      const condVal = condCol >= 0 ? row[condCol] : 'Clear';
      const condInfo = resolveWeatherCondition(condVal);
      
      let hi = '—', lo = '';
      if (tempCol >= 0 && row[tempCol]) {
        const rawTemp = row[tempCol];
        const tMatch = rawTemp.match(/(-?\d{1,3})\s*(?:°\s*[CF]?)?\s*(?:\/|\s+to\s+|-|–)\s*(-?\d{1,3})/i);
        if (tMatch) {
          hi = `${tMatch[1]}°`;
          lo = `${tMatch[2]}°`;
        } else {
          const singleT = rawTemp.match(/(-?\d{1,3})/);
          if (singleT) hi = `${singleT[1]}°`;
        }
      }
      
      const rainVal = rainCol >= 0 ? row[rainCol] : '';
      const rainMatch = rainVal.match(/(\d{1,3}\s*%)/);
      const rainBadge = rainMatch ? rainMatch[1] : (rainVal && rainVal !== '-' ? rainVal : '');

      const windVal = windCol >= 0 ? row[windCol] : '';
      const notesVal = notesCol >= 0 ? row[notesCol] : '';

      days.push({
        day: dayShort || rawDay,
        date: dateVal,
        condition: condVal,
        icon: condInfo.icon,
        hi,
        lo,
        rain: rainBadge,
        wind: windVal,
        notes: notesVal
      });
    }

    if (days.length >= 2) {
      return { days, tableLines };
    }
    return null;
  };

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
      const condInfo = resolveWeatherCondition(bodyText + ' ' + section.day);
      const dayShort = (section.day.match(/\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*/i) || [section.day])[0].slice(0, 3).toUpperCase();
      return {
        day: dayShort || section.day,
        date: section.day.replace(/\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b/i, '').trim(),
        condition: condInfo.name,
        icon: condInfo.icon,
        hi: `${tempMatch.temp}°`,
        lo: '',
        rain: '',
        wind: '',
        notes: ''
      };
    });
    if (days.some(day => !day)) return null;
    return days;
  };

  const parseLocationMeta = text => {
    const raw = safeText(text);
    const lines = raw.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const titleLine = lines.find(l => /weather|forecast|outlook/i.test(l) && (l.startsWith('#') || l.startsWith('**') || l.length < 100)) || lines[0] || '';
    
    let locationStr = '';
    let forecastLabel = 'Weather Outlook';
    let dateContext = '';

    const durationMatch = raw.match(/(\d+)\s*[-– ]\s*day\s+(?:weather\s+)?(?:outlook|forecast)/i);
    if (durationMatch) {
      forecastLabel = `${durationMatch[1]}-Day Forecast`;
    } else if (/forecast|outlook/i.test(raw)) {
      forecastLabel = 'Weather Forecast';
    }

    const dateMatch = raw.match(/\((?:starting\s+)?([^)]+)\)/i);
    if (dateMatch) {
      dateContext = dateMatch[1].replace(/starting\s+/i, '').trim();
    }

    const locPatterns = [
      /(?:weather\s+(?:outlook|forecast|report)?\s+(?:for|in)\s+|in\s+|for\s+)([A-Za-z0-9\s,.'-]+?)(?:\s*(?:\(|$|–|-|—|:|\n))/i,
      /(?:7-day|5-day|10-day|daily|weekly|weekend)\s*(?:weather)?\s*(?:outlook|forecast)?\s*[-–—:]\s*([A-Za-z\s,.'-]+?)(?:\s*(?:\(|$|\n))/i,
      /^#+\s*(?:[0-9]+-day\s+)?(?:weather\s+)?(?:outlook|forecast)?\s*(?:for|in)?\s*([A-Za-z\s,.'-]+?)(?:\s*(?:\(|$|\n))/i,
      /([A-Z][a-z]+(?:\s*,\s*[A-Za-z]+)+)/
    ];

    for (const pat of locPatterns) {
      const m = titleLine.match(pat) || raw.match(pat);
      if (m && m[1]) {
        let candidate = m[1].replace(/\s+/g, ' ').trim().replace(/[.,\-–:]+$/, '');
        if (candidate.length > 2 && candidate.length < 50 && !/^(the|this|today|tomorrow|starting|weather)$/i.test(candidate)) {
          locationStr = candidate;
          break;
        }
      }
    }

    if (!locationStr) {
      locationStr = 'Current Location';
    }

    let city = locationStr;
    let region = '';
    if (locationStr.includes(',')) {
      const parts = locationStr.split(',').map(s => s.trim());
      city = parts[0];
      region = parts.slice(1).join(', ');
    }

    return { city, region, fullLocation: locationStr, forecastLabel, dateContext };
  };

  const parseHeroWeather = (text, parsedDays) => {
    const raw = safeText(text);
    const tempMatch = extractTemperature(raw);
    let temp = tempMatch ? tempMatch.temp : '';
    let unit = tempMatch ? tempMatch.unit : 'C';

    if (!temp && parsedDays && parsedDays.length > 0 && parsedDays[0].hi !== '—') {
      temp = parsedDays[0].hi.replace(/°/g, '');
    }

    let high = '';
    let low = '';
    if (parsedDays && parsedDays.length > 0) {
      if (parsedDays[0].hi !== '—') high = parsedDays[0].hi;
      if (parsedDays[0].lo) low = parsedDays[0].lo;
    }
    if (!high) {
      const hlMatch = raw.match(/(?:high|max)[:\s]*(-?\d{1,3})\s*°?/i);
      if (hlMatch) high = `${hlMatch[1]}°`;
    }
    if (!low) {
      const loMatch = raw.match(/(?:low|min)[:\s]*(-?\d{1,3})\s*°?/i);
      if (loMatch) low = `${loMatch[1]}°`;
    }

    let feelsLike = '';
    const feelsMatch = raw.match(/feels\s+like[:\s]*(-?\d{1,3})\s*°?/i);
    if (feelsMatch) feelsLike = `${feelsMatch[1]}°`;

    const conditionInfo = resolveWeatherCondition(raw);

    return {
      temp: temp || '—',
      unit,
      high,
      low,
      feelsLike,
      condition: conditionInfo.name,
      icon: conditionInfo.icon,
      theme: conditionInfo.theme
    };
  };

  const parseWeatherMetrics = text => {
    const raw = safeText(text);
    const lines = raw.split(/\r?\n/);
    const bullets = [];

    lines.forEach(line => {
      const m1 = line.match(/^\s*[-*•]\s+\*\*([^*:]+):?\*\*:?\s*(.*)$/);
      if (m1) {
        bullets.push({ label: m1[1].trim(), rawValue: m1[2].replace(/\*\*/g, '').trim() });
        return;
      }
      const m2 = line.match(/^\s*\|?\s*([A-Za-z\s]+)\s*\|\s*([^|]+)\s*\|?\s*$/);
      if (m2 && !m2[1].includes('---') && !/day|date/i.test(m2[1])) {
        bullets.push({ label: m2[1].trim(), rawValue: m2[2].trim() });
        return;
      }
      const m3 = line.match(/^\s*(precipitation|rain|wind|humidity|uv(?:\s*index)?|visibility|pressure|air\s*quality)\s*[:=-]\s*(.+)$/i);
      if (m3) {
        bullets.push({ label: m3[1].trim(), rawValue: m3[2].trim() });
      }
    });

    const metrics = [];
    const metricLabelsSeen = new Set();
    let advisoryNote = '';

    bullets.forEach(b => {
      const lower = b.label.toLowerCase();
      if (/^temp/i.test(lower)) return;
      if (/note|advice|advisory|summary|outlook|tip/i.test(lower)) {
        if (!advisoryNote) advisoryNote = b.rawValue;
        return;
      }
      if (metricLabelsSeen.has(lower)) return;
      metricLabelsSeen.add(lower);

      const cleaned = cleanMetricItem(b.label, b.rawValue);
      metrics.push(cleaned);
    });

    const uvBullet = bullets.find(b => /uv/i.test(b.label));
    if (uvBullet && uvBullet.rawValue.length > 35 && !advisoryNote) {
      advisoryNote = uvBullet.rawValue;
    }

    return { metrics, advisoryNote };
  };

  const parseWeatherData = text => {
    const raw = safeText(text);
    if (!raw) return null;
    const lower = raw.toLowerCase();

    const hasWeatherSignal = /(weather|forecast|outlook|temperature|humidity|celsius|fahrenheit|°[cf]|sunny|rain|cloudy|thunderstorm|precipitation|heatwave|uv\s*index)/i.test(lower);
    if (!hasWeatherSignal) return null;

    const lines = raw.split(/\r?\n/);
    const tableForecast = parseMarkdownTableForecast(lines);
    const multiDayForecast = !tableForecast ? parseMultiDayForecast(raw) : null;
    
    const parsedDays = tableForecast ? tableForecast.days : (multiDayForecast || []);
    const location = parseLocationMeta(raw);
    const hero = parseHeroWeather(raw, parsedDays);
    const { metrics, advisoryNote } = parseWeatherMetrics(raw);

    if (hero.temp === '—' && metrics.length === 0 && parsedDays.length === 0) {
      return null;
    }

    const restLines = lines.filter(line => {
      if (tableForecast && tableForecast.tableLines.includes(line)) return false;
      if (/weather|forecast|outlook/i.test(line) && (line.startsWith('#') || line.startsWith('**') || line.length < 100)) return false;
      if (/^\s*[-*•]\s+\*\*([^*:]+):?\*\*:?\s*(.*)$/.test(line)) return false;
      return true;
    });

    const restMarkdown = restLines.join('\n').trim();

    return {
      location,
      hero,
      metrics,
      forecastDays: parsedDays,
      advisory: advisoryNote,
      restMarkdown
    };
  };

  const renderWeatherCard = data => {
    const { location, hero, metrics, forecastDays, advisory } = data;
    
    const regionHtml = location.region ? `<span class="weather-loc-region">${escapeHtml(location.region)}</span>` : '';
    const dateBadgeHtml = location.dateContext ? `<span class="weather-badge weather-date-badge">${escapeHtml(location.dateContext)}</span>` : '';

    const tempDisplay = hero.temp !== '—' ? `${escapeHtml(hero.temp)}<span class="weather-temp-unit">°${escapeHtml(hero.unit)}</span>` : `<span class="weather-temp-placeholder">Weather</span>`;
    const hiLoHtml = (hero.high || hero.low || hero.feelsLike) ? `
      <div class="weather-range-tags">
        ${hero.high ? `<span class="range-tag hi" title="High temperature">↑ ${escapeHtml(hero.high)}</span>` : ''}
        ${hero.low ? `<span class="range-tag lo" title="Low temperature">↓ ${escapeHtml(hero.low)}</span>` : ''}
        ${hero.feelsLike ? `<span class="range-tag feels" title="Feels like">Feels ${escapeHtml(hero.feelsLike)}</span>` : ''}
      </div>` : '';

    const metricsHtml = metrics.length ? `
      <div class="weather-metrics-grid">
        ${metrics.map(m => `
          <div class="weather-metric-tile">
            <div class="metric-head">
              <span class="metric-icon">${m.icon}</span>
              <span class="metric-name">${escapeHtml(m.label)}</span>
            </div>
            <div class="metric-val">${escapeHtml(m.value)}</div>
            ${m.subtext ? `<div class="metric-sub" title="${escapeHtml(m.subtext)}">${escapeHtml(m.subtext)}</div>` : ''}
          </div>
        `).join('')}
      </div>` : '';

    const forecastHtml = (forecastDays && forecastDays.length > 0) ? `
      <div class="weather-forecast-wrap">
        <div class="forecast-section-title">
          <span>${forecastDays.length}-Day Daily Outlook</span>
          <span class="forecast-hint">Scroll for more →</span>
        </div>
        <div class="weather-forecast-strip">
          ${forecastDays.map(d => `
            <div class="forecast-day-card">
              <span class="forecast-d-name">${escapeHtml(d.day)}</span>
              ${d.date ? `<span class="forecast-d-date">${escapeHtml(d.date)}</span>` : ''}
              <span class="forecast-d-icon">${d.icon}</span>
              <div class="forecast-d-temps">
                <span class="f-hi">${escapeHtml(d.hi)}</span>
                ${d.lo ? `<span class="f-lo">${escapeHtml(d.lo)}</span>` : ''}
              </div>
              ${d.rain ? `<span class="forecast-d-rain">💧 ${escapeHtml(d.rain)}</span>` : (d.condition ? `<span class="forecast-d-cond">${escapeHtml(d.condition)}</span>` : '')}
            </div>
          `).join('')}
        </div>
      </div>` : '';

    const advisoryHtml = advisory ? `
      <div class="weather-advisory-banner">
        <span class="advisory-icon">💡</span>
        <div class="advisory-content">${escapeHtml(advisory)}</div>
      </div>` : '';

    return `
      <div class="weather-card" data-theme="${escapeHtml(hero.theme)}">
        <div class="weather-card-header">
          <div class="weather-loc-box">
            <span class="weather-loc-pin">📍</span>
            <div class="weather-loc-text">
              <span class="weather-loc-city">${escapeHtml(location.city)}</span>
              ${regionHtml}
            </div>
          </div>
          <div class="weather-badge-group">
            <span class="weather-badge weather-pill-badge">${escapeHtml(location.forecastLabel)}</span>
            ${dateBadgeHtml}
          </div>
        </div>

        <div class="weather-hero-row">
          <div class="weather-hero-main">
            <div class="weather-hero-temp">${tempDisplay}</div>
            <div class="weather-hero-meta">
              <div class="weather-condition-tag">${hero.icon} ${escapeHtml(hero.condition)}</div>
              ${hiLoHtml}
            </div>
          </div>
          <div class="weather-hero-art" aria-hidden="true">${hero.icon}</div>
        </div>

        ${metricsHtml}
        ${forecastHtml}
        ${advisoryHtml}
      </div>`;
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
    parseWeatherData,
    renderWeatherCard,
    renderMarkdown,
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
          const weatherData = parseWeatherData(text);
          if (weatherData) {
            const cardWrap = document.createElement('div');
            cardWrap.innerHTML = renderWeatherCard(weatherData);
            if (cardWrap.firstElementChild) {
              body.appendChild(cardWrap.firstElementChild);
            }
            if (weatherData.restMarkdown) {
              const textWrap = document.createElement('div');
              textWrap.className = 'markdown-body';
              textWrap.innerHTML = renderMarkdown(weatherData.restMarkdown);
              body.appendChild(textWrap);
            }
          } else {
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
