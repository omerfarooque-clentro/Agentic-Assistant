/**
 * Typed Result Cards UI Library (v1)
 * Renders structured cards for weather, email lists, Slack mentions, Google Sheets, Google Calendar, and Search.
 */

(function () {
  'use strict';

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  const BRAND_ICONS = {
    gmail: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24"><path fill="#4285F4" d="M4 19h3V9.5L2 6v11.5c0 .8.7 1.5 1.5 1.5h.5z"/><path fill="#34A853" d="M20 19c.8 0 1.5-.7 1.5-1.5V6l-5 3.5V19h3.5z"/><path fill="#EA4335" d="M16.5 9.5V5c0-.8-.7-1.5-1.5-1.5H9c-.8 0-1.5.7-1.5 1.5v4.5l4.5 3.4 4.5-3.4z"/><path fill="#FBBC05" d="M2 6l5.5 4V5c0-.4.2-.8.5-1.1L2 6z"/><path fill="#C5221F" d="M22 6l-5.5 4V5c0-.4-.2-.8-.5-1.1L22 6z"/></svg>`,
    calendar: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="17" height="17" rx="3" fill="#ffffff" stroke="#4285f4" stroke-width="1.5"/><path d="M3.5 6.5C3.5 4.8 4.8 3.5 6.5 3.5H17.5C19.2 3.5 20.5 4.8 20.5 6.5V8.5H3.5V6.5Z" fill="#4285f4"/><rect x="7" y="2" width="2" height="3" rx="1" fill="#1a73e8"/><rect x="15" y="2" width="2" height="3" rx="1" fill="#1a73e8"/><text x="12" y="17" font-size="8.5" font-weight="800" fill="#1a73e8" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif">31</text></svg>`,
    slack: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24"><path fill="#E01E5A" d="M5.04 14.5a2.5 2.5 0 1 0-2.5 2.5h2.5v-2.5zm1.25 0a2.5 2.5 0 0 0 5 0v-6.25a2.5 2.5 0 0 0-5 0v6.25z"/><path fill="#36C5F0" d="M9.5 5.04a2.5 2.5 0 1 0-2.5-2.5v2.5h2.5zm0 1.25a2.5 2.5 0 0 0 0 5h6.25a2.5 2.5 0 0 0 0-5H9.5z"/><path fill="#2EB67D" d="M18.96 9.5a2.5 2.5 0 1 0 2.5-2.5h-2.5v2.5zm-1.25 0a2.5 2.5 0 0 0-5 0v6.25a2.5 2.5 0 0 0 5 0V9.5z"/><path fill="#ECB22E" d="M14.5 18.96a2.5 2.5 0 1 0 2.5 2.5v-2.5h-2.5zm0-1.25a2.5 2.5 0 0 0 0-5H8.25a2.5 2.5 0 0 0 0 5H14.5z"/></svg>`,
    sheets: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24"><path fill="#0F9D58" d="M14.5 2H6C4.9 2 4 2.9 4 4v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V7.5L14.5 2z"/><path fill="#87CEAB" d="M14 2v6h6L14 2z"/><path fill="#FFFFFF" d="M7 11h10v7H7zm1.5 1.5v1.5h3v-1.5zm4.5 0v1.5h2.5v-1.5zm-4.5 2.5v1.5h3V15zm4.5 0v1.5h2.5V15z"/></svg>`,
    search: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l3.66-2.85z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"/></svg>`,
    location: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>`,
    clock: `<svg class="rc-inline-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: -1px; margin-right: 3px;"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
    link: `<svg class="rc-inline-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: -1px; margin-right: 3px;"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
  };

  const CONDITION_ICONS = {
    clear: '☀️',
    partly_cloudy: '⛅',
    cloudy: '☁️',
    fog: '🌫️',
    drizzle: '🌦️',
    rain: '🌧️',
    thunderstorm: '⛈️',
    snow: '❄️',
  };

  function getConditionIcon(condition) {
    return CONDITION_ICONS[condition] || '🌤️';
  }

  function handleChipClick(text) {
    const input = document.querySelector('#message-input') || document.querySelector('textarea.composer-textarea');
    if (input) {
      input.value = text;
      input.focus();
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  // --- Card Renderers ---

  function renderWeatherCard(data) {
    if (!data || !data.current) return '';
    const current = data.current;
    const days = Array.isArray(data.days) ? data.days : [];
    const icon = getConditionIcon(current.condition);

    let daysHtml = '';
    days.forEach(d => {
      const dIcon = getConditionIcon(d.condition);
      const highVal = (typeof d.high === 'number' && !isNaN(d.high)) ? Math.round(d.high) + '°' : '--';
      const lowVal = (typeof d.low === 'number' && !isNaN(d.low)) ? Math.round(d.low) + '°' : '--';
      daysHtml += `
        <div class="rc-weather-day-item">
          <span class="rc-weather-day-label">${escapeHtml(d.label || d.date)}</span>
          <span class="rc-weather-day-icon">${dIcon}</span>
          <span class="rc-weather-day-temps">${highVal} <span class="rc-weather-day-low">${lowVal}</span></span>
          ${d.rain_chance > 0 ? `<span class="rc-weather-day-rain">💧${d.rain_chance}%</span>` : ''}
        </div>
      `;
    });

    return `
      <div class="rc-card rc-weather">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${BRAND_ICONS.location}</span>
            <span>${escapeHtml(data.city)}${data.country ? `, ${escapeHtml(data.country)}` : ''}</span>
          </div>
          <span class="rc-badge">${escapeHtml(data.updated_at || 'Live')}</span>
        </div>

        <div class="rc-weather-hero">
          <div class="rc-weather-hero-main">
            <div class="rc-weather-temp-row">
              <span class="rc-weather-temp">${Math.round(current.temp)}</span>
              <span class="rc-weather-unit">${escapeHtml(current.unit || '°C')}</span>
            </div>
            <div class="rc-weather-condition">
              <span>${icon}</span>
              <span>${escapeHtml(current.condition.replace('_', ' '))}</span>
            </div>
          </div>
          <div class="rc-weather-art">${icon}</div>
        </div>

        <div class="rc-weather-metrics">
          <div class="rc-weather-metric-item">
            <span class="rc-weather-metric-label">Feels like</span>
            <span class="rc-weather-metric-val">${Math.round(current.feels_like)}°</span>
          </div>
          <div class="rc-weather-metric-item">
            <span class="rc-weather-metric-label">Humidity</span>
            <span class="rc-weather-metric-val">${current.humidity}%</span>
          </div>
          <div class="rc-weather-metric-item">
            <span class="rc-weather-metric-label">Wind</span>
            <span class="rc-weather-metric-val">${escapeHtml(current.wind)}</span>
          </div>
          <div class="rc-weather-metric-item">
            <span class="rc-weather-metric-label">Precip</span>
            <span class="rc-weather-metric-val">${current.rain_chance}%</span>
          </div>
        </div>

        ${daysHtml ? `<div class="rc-weather-forecast">${daysHtml}</div>` : ''}
      </div>
    `;
  }

  function renderEmailListCard(data, presentation) {
    if (!data || !Array.isArray(data.items)) return '';
    const maxRows = (presentation && presentation.max_rows) || 5;
    const items = data.items.slice(0, maxRows);

    let itemsHtml = '';
    items.forEach(item => {
      const sender = escapeHtml(item.sender_name || item.sender_email || 'Unknown');
      const subject = escapeHtml(item.subject || '(No Subject)');
      const snippet = escapeHtml(item.snippet || '');
      const date = escapeHtml(item.received_at || '');

      itemsHtml += `
        <div class="rc-email-item">
          <div class="rc-email-item-header">
            <div class="rc-email-sender-wrap">
              ${item.unread ? '<span class="rc-dot-unread" title="Unread"></span>' : ''}
              <span class="rc-email-sender">${sender}</span>
            </div>
            <span class="rc-email-date">${date}</span>
          </div>
          <a class="rc-email-subject" href="${item.thread_url ? escapeHtml(item.thread_url) : '#'}" target="_blank" rel="noopener">${subject}</a>
          ${snippet ? `<div class="rc-email-snippet">${snippet}</div>` : ''}
          <div class="rc-email-actions">
            <button class="rc-action-chip" type="button" data-prompt="Draft a reply to &quot;${sender}&quot; regarding &quot;${subject}&quot;">Draft reply</button>
            <button class="rc-action-chip" type="button" data-prompt="Summarize the email thread for &quot;${subject}&quot;">Summarize</button>
          </div>
        </div>
      `;
    });

    return `
      <div class="rc-card rc-email-list">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${BRAND_ICONS.gmail}</span>
            <span>${escapeHtml(data.query || 'Recent Emails')}</span>
          </div>
          <span class="rc-badge">${data.total} message${data.total === 1 ? '' : 's'}${data.unread ? ` (${data.unread} unread)` : ''}</span>
        </div>
        <div class="rc-email-items">${itemsHtml}</div>
      </div>
    `;
  }

  function renderSlackMentionsCard(data, presentation) {
    if (!data || !Array.isArray(data.items)) return '';
    const maxRows = (presentation && presentation.max_rows) || 5;
    const items = data.items.slice(0, maxRows);

    let itemsHtml = '';
    items.forEach(m => {
      const channel = m.channel || 'general';
      const isDm = channel.toLowerCase().startsWith('dm');
      const channelLabel = isDm ? escapeHtml(channel) : '#' + escapeHtml(channel);
      itemsHtml += `
        <div class="rc-slack-item">
          <div class="rc-slack-item-header">
            <span class="rc-slack-channel">${channelLabel}</span>
            <span class="rc-slack-sender">${escapeHtml(m.sender)}</span>
          </div>
          <div class="rc-slack-text">${escapeHtml(m.text)}</div>
          ${m.permalink ? `<div style="margin-top: 4px;"><a class="rc-link-btn" href="${escapeHtml(m.permalink)}" target="_blank" rel="noopener">View in Slack ↗</a></div>` : ''}
        </div>
      `;
    });

    return `
      <div class="rc-card rc-slack-mentions">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${BRAND_ICONS.slack}</span>
            <span>Slack Activity</span>
          </div>
          <span class="rc-badge">${items.length} item${items.length === 1 ? '' : 's'}</span>
        </div>
        <div class="rc-slack-items">${itemsHtml}</div>
      </div>
    `;
  }

  function renderSheetCard(data, presentation, isUpdate) {
    if (!data) return '';
    const columns = Array.isArray(data.columns) ? data.columns : [];
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const highlightIdx = isUpdate && typeof data.highlight_row_index === 'number' ? data.highlight_row_index : -1;

    let thHtml = columns.map(c => `<th>${escapeHtml(c)}</th>`).join('');
    let trHtml = '';

    const maxRows = (presentation && presentation.max_rows) || 10;
    rows.slice(0, maxRows).forEach((row, idx) => {
      const isHighlighted = idx === highlightIdx;
      const cells = Array.isArray(row) ? row : [row];
      const tdHtml = cells.map(cell => `<td>${escapeHtml(cell)}</td>`).join('');
      trHtml += `<tr class="${isHighlighted ? 'rc-sheet-row-highlight' : ''}">${tdHtml}</tr>`;
    });

    return `
      <div class="rc-card rc-sheet-card">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${BRAND_ICONS.sheets}</span>
            <span>${escapeHtml(data.title || (isUpdate ? 'Sheet Updated' : 'Google Sheet'))}</span>
          </div>
          ${data.url ? `<a class="rc-link-btn" href="${escapeHtml(data.url)}" target="_blank" rel="noopener">Open Sheet ↗</a>` : ''}
        </div>
        ${columns.length || rows.length ? `
          <div class="rc-sheet-wrap">
            <table class="rc-sheet-table">
              ${thHtml ? `<thead><tr>${thHtml}</tr></thead>` : ''}
              <tbody>${trHtml}</tbody>
            </table>
          </div>
        ` : ''}
        ${isUpdate && data.row_number ? `<div style="font-size: 0.76rem; color: var(--muted); margin-top: 4px;">Row ${data.row_number} updated</div>` : ''}
      </div>
    `;
  }

  function renderCalendarCard(data) {
    if (!data || !Array.isArray(data.events)) return '';
    const events = data.events;

    let itemsHtml = '';
    events.forEach(ev => {
      let month = '—';
      let day = '?';
      if (ev.start) {
        const d = new Date(ev.start);
        if (!isNaN(d)) {
          month = d.toLocaleString('en-US', { month: 'short' });
          day = d.getDate();
        }
      }

      itemsHtml += `
        <div class="rc-calendar-item">
          <div class="rc-cal-date-badge">
            <span class="rc-cal-badge-month">${escapeHtml(month)}</span>
            <span class="rc-cal-badge-day">${escapeHtml(day)}</span>
          </div>
          <div class="rc-cal-info">
            <span class="rc-cal-title">${escapeHtml(ev.title || 'Event')}</span>
            <span class="rc-cal-time">${BRAND_ICONS.clock} ${escapeHtml(ev.start)}${ev.end ? ` – ${escapeHtml(ev.end)}` : ''}</span>
            <div style="display: flex; gap: 6px; margin-top: 6px;">
              ${ev.meet_url ? `<a class="rc-link-btn" href="${escapeHtml(ev.meet_url)}" target="_blank" rel="noopener">Join Meet ↗</a>` : ''}
              ${ev.html_link ? `<a class="rc-link-btn" href="${escapeHtml(ev.html_link)}" target="_blank" rel="noopener">Calendar ↗</a>` : ''}
            </div>
          </div>
        </div>
      `;
    });

    return `
      <div class="rc-card rc-calendar-card">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${BRAND_ICONS.calendar}</span>
            <span>Upcoming Schedule</span>
          </div>
          <span class="rc-badge">${events.length} event${events.length === 1 ? '' : 's'}</span>
        </div>
        <div class="rc-calendar-items">${itemsHtml}</div>
      </div>
    `;
  }

  function renderSearchSummaryCard(data) {
    if (!data) return '';
    const points = Array.isArray(data.points) ? data.points : [];
    const sources = Array.isArray(data.sources) ? data.sources : [];

    let pointsHtml = '';
    points.forEach(p => {
      pointsHtml += `
        <div class="rc-search-point">
          ${p.tag ? `<span class="rc-search-tag">${escapeHtml(p.tag)}</span>` : ''}
          <span>${escapeHtml(p.text)}</span>
        </div>
      `;
    });

    let sourcesHtml = '';
    sources.forEach(s => {
      sourcesHtml += `
        <a class="rc-source-pill" href="${escapeHtml(s.url)}" target="_blank" rel="noopener">
          <span>${BRAND_ICONS.link}</span>
          <span>${escapeHtml(s.domain || 'Source')}</span>
        </a>
      `;
    });

    return `
      <div class="rc-card rc-search-card">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${BRAND_ICONS.search}</span>
            <span>${escapeHtml(data.title || 'Search Overview')}</span>
          </div>
          <span class="rc-badge">${data.source_count || sources.length} source${(data.source_count || sources.length) === 1 ? '' : 's'}</span>
        </div>
        ${pointsHtml ? `<div class="rc-search-points">${pointsHtml}</div>` : ''}
        ${sourcesHtml ? `<div class="rc-sources-row">${sourcesHtml}</div>` : ''}
      </div>
    `;
  }

  // --- Main Dispatcher ---

  function renderResultCard(card) {
    if (!card || typeof card !== 'object' || !card.type || !card.data) return null;

    let html = '';
    switch (card.type) {
      case 'weather':
        html = renderWeatherCard(card.data, card.presentation);
        break;
      case 'email_list':
        html = renderEmailListCard(card.data, card.presentation);
        break;
      case 'slack_mentions':
        html = renderSlackMentionsCard(card.data, card.presentation);
        break;
      case 'sheet_view':
        html = renderSheetCard(card.data, card.presentation, false);
        break;
      case 'sheet_update':
        html = renderSheetCard(card.data, card.presentation, true);
        break;
      case 'calendar_event':
        html = renderCalendarCard(card.data, card.presentation);
        break;
      case 'search_summary':
        html = renderSearchSummaryCard(card.data, card.presentation);
        break;
      default:
        return null;
    }

    if (!html) return null;

    const wrapper = document.createElement('div');
    wrapper.className = 'rc-container';
    wrapper.innerHTML = html;

    // Attach click handlers to action chips
    wrapper.querySelectorAll('.rc-action-chip').forEach(btn => {
      btn.addEventListener('click', e => {
        e.preventDefault();
        const prompt = btn.getAttribute('data-prompt');
        if (prompt) handleChipClick(prompt);
      });
    });

    return wrapper;
  }

  // Export globally
  window.renderResultCard = renderResultCard;
})();
