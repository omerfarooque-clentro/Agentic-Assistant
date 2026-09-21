/**
 * Typed Result Cards UI Library (Result Cards Spec Compliant)
 * Renders structured, accessible cards for weather, email lists, Slack mentions,
 * Google Sheets, Google Calendar, Search, and News.
 *
 * Rules:
 * - Strictly NO emoji anywhere; clean inline SVG helpers only.
 * - Single constant DEFAULT_MAX_ROWS = 3 for list clamping with in-place toggle.
 * - Tap targets 44px min.
 * - Wrapped in try/catch; malformed payloads fallback to plain text.
 */

(function () {
  'use strict';

  const DEFAULT_MAX_ROWS = 3;

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/[\u202F\u2009\u00A0]/g, ' ')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // --- Inline SVG Icons (Zero Emoji) ---
  const SVG_ICONS = {
    gmail: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M4 19h3V9.5L2 6v11.5c0 .8.7 1.5 1.5 1.5h.5z"/><path fill="#34A853" d="M20 19c.8 0 1.5-.7 1.5-1.5V6l-5 3.5V19h3.5z"/><path fill="#EA4335" d="M16.5 9.5V5c0-.8-.7-1.5-1.5-1.5H9c-.8 0-1.5.7-1.5 1.5v4.5l4.5 3.4 4.5-3.4z"/><path fill="#FBBC05" d="M2 6l5.5 4V5c0-.4.2-.8.5-1.1L2 6z"/><path fill="#C5221F" d="M22 6l-5.5 4V5c0-.4-.2-.8-.5-1.1L22 6z"/></svg>`,
    calendar: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="3.5" width="17" height="17" rx="3" fill="#ffffff" stroke="#4285f4" stroke-width="1.5"/><path d="M3.5 6.5C3.5 4.8 4.8 3.5 6.5 3.5H17.5C19.2 3.5 20.5 4.8 20.5 6.5V8.5H3.5V6.5Z" fill="#4285f4"/><rect x="7" y="2" width="2" height="3" rx="1" fill="#1a73e8"/><rect x="15" y="2" width="2" height="3" rx="1" fill="#1a73e8"/><text x="12" y="17" font-size="8.5" font-weight="800" fill="#1a73e8" text-anchor="middle" font-family="system-ui, -apple-system, sans-serif">31</text></svg>`,
    slack: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path fill="#E01E5A" d="M5.04 14.5a2.5 2.5 0 1 0-2.5 2.5h2.5v-2.5zm1.25 0a2.5 2.5 0 0 0 5 0v-6.25a2.5 2.5 0 0 0-5 0v6.25z"/><path fill="#36C5F0" d="M9.5 5.04a2.5 2.5 0 1 0-2.5-2.5v2.5h2.5zm0 1.25a2.5 2.5 0 0 0 0 5h6.25a2.5 2.5 0 0 0 0-5H9.5z"/><path fill="#2EB67D" d="M18.96 9.5a2.5 2.5 0 1 0 2.5-2.5h-2.5v2.5zm-1.25 0a2.5 2.5 0 0 0-5 0v6.25a2.5 2.5 0 0 0 5 0V9.5z"/><path fill="#ECB22E" d="M14.5 18.96a2.5 2.5 0 1 0 2.5 2.5v-2.5h-2.5zm0-1.25a2.5 2.5 0 0 0 0-5H8.25a2.5 2.5 0 0 0 0 5H14.5z"/></svg>`,
    sheets: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path fill="#0F9D58" d="M14.5 2H6C4.9 2 4 2.9 4 4v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V7.5L14.5 2z"/><path fill="#87CEAB" d="M14 2v6h6L14 2z"/><path fill="#FFFFFF" d="M7 11h10v7H7zm1.5 1.5v1.5h3v-1.5zm4.5 0v1.5h2.5v-1.5zm-4.5 2.5v1.5h3V15zm4.5 0v1.5h2.5V15z"/></svg>`,
    search: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
    news: `<svg class="rc-brand-icon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/><path d="M10 6h8v4h-8V6Z"/></svg>`,
    location: `<svg class="rc-inline-svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>`,
    clock: `<svg class="rc-inline-svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>`,
    link: `<svg class="rc-inline-svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`,
    droplet: `<svg class="rc-inline-svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/></svg>`,
    wind: `<svg class="rc-inline-svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.59 4.59A2 2 0 1 1 11 8H2m10.59 11.41A2 2 0 1 0 14 16H2m15.73-8.27A2.5 2.5 0 1 1 19.5 12H2"/></svg>`,
    thermometer: `<svg class="rc-inline-svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z"/></svg>`,
    video: `<svg class="rc-inline-svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>`,
    chevronDown: `<svg class="rc-inline-svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>`,
    chevronUp: `<svg class="rc-inline-svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="18 15 12 9 6 15"/></svg>`,
    chevronLeft: `<svg class="rc-inline-svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg>`,
    sunrise: `<svg class="rc-inline-svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2v6"/><path d="m4.93 10.93 1.41 1.41"/><path d="M20 18h2"/><path d="m19.07 10.93-1.41 1.41"/><path d="M22 22H2"/><path d="m8 6 4-4 4 4"/><path d="M16 18a4 4 0 0 0-8 0"/></svg>`,
    sunset: `<svg class="rc-inline-svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 10V4"/><path d="m4.93 10.93 1.41 1.41"/><path d="M20 18h2"/><path d="m19.07 10.93-1.41 1.41"/><path d="M22 22H2"/><path d="m16 6-4 4-4-4"/><path d="M16 18a4 4 0 0 0-8 0"/></svg>`,
    check: `<svg class="rc-inline-svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>`,
    // Weather condition SVGs
    weatherClear: `<svg class="rc-inline-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`,
    weatherClearNight: `<svg class="rc-inline-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`,
    weatherCloudy: `<svg class="rc-inline-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"/></svg>`,
    weatherPartlyCloudy: `<svg class="rc-inline-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="M20 12h2"/><path d="m19.07 4.93-1.41 1.41"/><path d="M15.95 9.6A5 5 0 0 0 9 12a5.5 5.5 0 0 0-.25 10H18a4 4 0 0 0 .8-7.92 5 5 0 0 0-2.85-4.48Z"/></svg>`,
    weatherPartlyCloudyNight: `<svg class="rc-inline-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.188 8.5A6 6 0 0 1 16 4a7 7 0 0 0-6 8 6 6 0 0 0 5.259 3.039A8.005 8.005 0 0 1 3 19a8 8 0 0 1 6-7.812Z"/><path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/></svg>`,
    weatherRain: `<svg class="rc-inline-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="16" y1="13" x2="16" y2="21"/><line x1="8" y1="13" x2="8" y2="21"/><line x1="12" y1="15" x2="12" y2="23"/><path d="M20 16.58A5 5 0 0 0 18 7h-1.26A8 8 0 1 0 4 15.25"/></svg>`,
    weatherThunder: `<svg class="rc-inline-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 16.9A5 5 0 0 0 18 7h-1.26a8 8 0 1 0-11.62 9"/><polygon points="13 11 9 17 15 17 11 23"/></svg>`,
    weatherSnow: `<svg class="rc-inline-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 17.58A5 5 0 0 0 18 8h-1.26A8 8 0 1 0 4 16.25"/><line x1="8" y1="16" x2="8.01" y2="16"/><line x1="8" y1="20" x2="8.01" y2="20"/><line x1="12" y1="18" x2="12.01" y2="18"/><line x1="12" y1="22" x2="12.01" y2="22"/><line x1="16" y1="16" x2="16.01" y2="16"/><line x1="16" y1="20" x2="16.01" y2="20"/></svg>`,
    weatherFog: `<svg class="rc-inline-svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="5" y1="10" x2="19" y2="10"/><line x1="5" y1="14" x2="19" y2="14"/><line x1="5" y1="18" x2="19" y2="18"/><line x1="8" y1="6" x2="16" y2="6"/></svg>`,
  };

  function getWeatherConditionSvg(condition, isDay, timeStr) {
    const c = (condition || '').toLowerCase();
    let isNight = false;
    if (isDay === false || isDay === 0 || c.includes('night')) {
      isNight = true;
    } else if (isDay === true || isDay === 1 || c.includes('day')) {
      isNight = false;
    } else if (timeStr) {
      try {
        const timePart = timeStr.includes('T') ? timeStr.split('T')[1] : timeStr;
        const hr = parseInt(timePart.split(':')[0], 10);
        if (!isNaN(hr) && (hr < 6 || hr >= 19)) {
          isNight = true;
        }
      } catch (e) {
        isNight = false;
      }
    }

    if (c.includes('rain') || c.includes('drizzle')) return SVG_ICONS.weatherRain;
    if (c.includes('thunder')) return SVG_ICONS.weatherThunder;
    if (c.includes('snow') || c.includes('ice') || c.includes('flurry')) return SVG_ICONS.weatherSnow;
    if (c.includes('fog') || c.includes('mist') || c.includes('haze')) return SVG_ICONS.weatherFog;
    if (c.includes('cloud') && (c.includes('part') || c.includes('scatter') || c.includes('broken'))) {
      return isNight ? SVG_ICONS.weatherPartlyCloudyNight : SVG_ICONS.weatherPartlyCloudy;
    }
    if (c.includes('cloud') || c.includes('overcast')) return SVG_ICONS.weatherCloudy;
    return isNight ? SVG_ICONS.weatherClearNight : SVG_ICONS.weatherClear;
  }

  // --- Relative Time Formatter ---
  function formatRelativeTime(dateInput) {
    if (!dateInput) return '';
    const date = new Date(dateInput);
    if (isNaN(date.getTime())) return String(dateInput);

    const now = new Date();
    const diffMs = now - date;
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHours = Math.floor(diffMin / 60);
    const diffDays = Math.floor(diffHours / 24);

    if (diffMin < 1) return 'Just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) {
      return date.toLocaleDateString(undefined, { weekday: 'short' });
    }
    return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function getWeekdayName(dateInput) {
    if (!dateInput) return 'Today';
    const d = new Date(dateInput);
    if (isNaN(d.getTime())) return String(dateInput);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) return 'Today';
    return d.toLocaleDateString(undefined, { weekday: 'short' });
  }

  function handleChipClick(text) {
    const input = document.querySelector('#message-input') || document.querySelector('textarea.composer-textarea');
    if (input) {
      input.value = text;
      input.focus();
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  function formatHourTime(isoString, isNow = false) {
    if (isNow) return 'Now';
    if (!isoString) return '';
    try {
      const parts = isoString.split('T')[1]?.split(':');
      if (parts && parts.length >= 1) {
        let h = parseInt(parts[0], 10);
        const ampm = h >= 12 ? 'pm' : 'am';
        h = h % 12 || 12;
        return `${h}${ampm}`;
      }
      const d = new Date(isoString);
      if (!isNaN(d.getTime())) {
        let h = d.getHours();
        const ampm = h >= 12 ? 'pm' : 'am';
        h = h % 12 || 12;
        return `${h}${ampm}`;
      }
      return isoString;
    } catch {
      return isoString;
    }
  }

  function formatSunTime(isoString) {
    if (!isoString) return '--';
    try {
      const parts = isoString.split('T')[1]?.split(':');
      if (parts && parts.length >= 2) {
        let h = parseInt(parts[0], 10);
        const m = parts[1];
        const ampm = h >= 12 ? 'pm' : 'am';
        h = h % 12 || 12;
        return `${h}:${m} ${ampm}`;
      }
      const d = new Date(isoString);
      if (!isNaN(d.getTime())) {
        let h = d.getHours();
        const m = String(d.getMinutes()).padStart(2, '0');
        const ampm = h >= 12 ? 'pm' : 'am';
        h = h % 12 || 12;
        return `${h}:${m} ${ampm}`;
      }
      return isoString;
    } catch {
      return isoString;
    }
  }

  function getUvCategory(val) {
    if (val == null) return '--';
    const num = Number(val);
    if (isNaN(num)) return String(val);
    const rounded = Math.round(num);
    let cat = 'Low';
    if (num >= 11) cat = 'Extreme';
    else if (num >= 8) cat = 'Very High';
    else if (num >= 6) cat = 'High';
    else if (num >= 3) cat = 'Moderate';
    return `${rounded} · ${cat}`;
  }

  function renderWeatherHourlyScrubHtml(hours, isToday) {
    if (!Array.isArray(hours) || hours.length === 0) {
      return `<div class="rc-weather-hourly-empty" style="padding: 12px; font-size: 12px; color: var(--muted);">No hourly forecast available</div>`;
    }
    return hours.map((h, i) => {
      const isNow = isToday && i === 0;
      const timeLabel = formatHourTime(h.time, isNow);
      const iconSvg = getWeatherConditionSvg(h.condition, h.is_day, h.time);
      const tempVal = Math.round(h.temp ?? 0);
      const rainChance = h.rain_chance || 0;
      const rainRow = rainChance > 0 ? `<div class="rc-weather-rainrow">${SVG_ICONS.droplet}${rainChance}%</div>` : '';
      const nowClass = isNow ? ' now' : '';
      return `
        <div class="rc-weather-hcol${nowClass}">
          <div class="rc-weather-t">${timeLabel}</div>
          <div class="rc-weather-hicon">${iconSvg}</div>
          <div class="rc-weather-deg">${tempVal}°</div>
          <div class="rc-weather-rainrow-wrap">${rainRow}</div>
        </div>
      `;
    }).join('');
  }

  // --- Card Renderers ---

  // 1. Weather Card
  function renderWeatherCard(data) {
    if (!data || !data.current) return '';
    const current = data.current;
    const days = Array.isArray(data.days) ? data.days : [];
    const conditionText = escapeHtml((current.condition || 'Clear').replace(/_/g, ' '));
    const condSvg = getWeatherConditionSvg(current.condition, current.is_day);

    // Calculate global temp range for range bar
    let minT = current.temp;
    let maxT = current.temp;
    days.forEach(d => {
      if (typeof d.low === 'number') minT = Math.min(minT, d.low);
      if (typeof d.high === 'number') maxT = Math.max(maxT, d.high);
    });
    if (minT === maxT) { minT -= 5; maxT += 5; }
    const totalSpan = Math.max(1, maxT - minT);

    let daysHtml = '';
    days.forEach((d, idx) => {
      const dayCondition = (idx === 0 && current.condition) ? current.condition : d.condition;
      const daySvg = getWeatherConditionSvg(dayCondition, true);
      const dayName = escapeHtml(d.label || getWeekdayName(d.date));
      const lowVal = typeof d.low === 'number' ? Math.round(d.low) + '°' : '--';
      const highVal = typeof d.high === 'number' ? Math.round(d.high) + '°' : '--';

      const dLow = typeof d.low === 'number' ? d.low : minT;
      const dHigh = typeof d.high === 'number' ? d.high : maxT;
      const leftPct = Math.max(0, Math.min(100, Math.round(((dLow - minT) / totalSpan) * 100)));
      const widthPct = Math.max(10, Math.min(100 - leftPct, Math.round(((dHigh - dLow) / totalSpan) * 100)));

      const extraClass = idx >= DEFAULT_MAX_ROWS ? ' rc-row-hidden' : '';

      daysHtml += `
        <div class="rc-weather-day-row${extraClass}" role="button" tabindex="0" data-day-index="${idx}" aria-label="View hourly forecast for ${dayName}">
          <span class="rc-weather-day-name">${dayName}</span>
          <span class="rc-weather-day-icon-wrap">${daySvg}</span>
          <div class="rc-weather-range-bar-wrap">
            <div class="rc-weather-range-bar-fill" style="left: ${leftPct}%; width: ${widthPct}%;"></div>
          </div>
          <span class="rc-weather-day-low">${lowVal}</span>
          <span class="rc-weather-day-high">${highVal}</span>
        </div>
      `;
    });

    const toggleBtn = days.length > DEFAULT_MAX_ROWS ? `
      <button class="rc-toggle-btn" type="button" data-expanded="false" data-total="${days.length}" aria-label="Toggle 7-day forecast">
        <span>Show all ${days.length} days</span>
        ${SVG_ICONS.chevronDown}
      </button>
    ` : '';

    const initialDay = days[0] || {};
    const initialHours = (initialDay.hourly && initialDay.hourly.length) ? initialDay.hourly : (data.hourly || []);
    const initialScrubHtml = renderWeatherHourlyScrubHtml(initialHours, true);
    const initialSunRise = formatSunTime(initialDay.sunrise);
    const initialSunSet = formatSunTime(initialDay.sunset);
    const initialUv = getUvCategory(initialDay.uv_index_max ?? current.uv_index);
    const initialPressure = Math.round(initialDay.pressure ?? current.pressure ?? 1013);
    const initialFeels = Math.round(initialDay.feels_like ?? current.feels_like ?? current.temp);
    const initialHum = initialDay.humidity ?? current.humidity ?? '--';
    const initialWind = escapeHtml(initialDay.wind || current.wind || '--');
    const initialRain = initialDay.rain_chance ?? current.rain_chance ?? 0;
    const initialDayName = escapeHtml(initialDay.label || 'Today');

    // Escape and embed full serialized weather payload for interactive day switching
    const weatherJson = escapeHtml(JSON.stringify(data));

    return `
      <div class="rc-card rc-weather" role="group" aria-label="Weather for ${escapeHtml(data.city)}">
        <script type="application/json" class="rc-weather-raw-data">${weatherJson}</script>

        <!-- View 1: 7-Day Overview -->
        <div class="rc-weather-view rc-weather-view-overview">
          <div class="rc-header">
            <div class="rc-header-title">
              <span class="rc-header-icon">${SVG_ICONS.location}</span>
              <span>${escapeHtml(data.city)}${data.country ? `, ${escapeHtml(data.country)}` : ''}</span>
            </div>
            <span class="rc-badge">${escapeHtml(data.updated_at ? formatRelativeTime(data.updated_at) : 'Live')}</span>
          </div>

          <div class="rc-weather-hero">
            <div class="rc-weather-hero-main">
              <div class="rc-weather-temp-row">
                <span class="rc-weather-temp">${Math.round(current.temp)}</span>
                <span class="rc-weather-unit">${escapeHtml(current.unit || '°C')}</span>
              </div>
              <div class="rc-weather-condition-text">
                <span class="rc-weather-condition-icon">${condSvg}</span>
                <span>${conditionText} · Feels like ${Math.round(current.feels_like ?? current.temp)}°</span>
              </div>
            </div>
            <div class="rc-weather-art-icon" aria-hidden="true">${condSvg}</div>
          </div>

          <div class="rc-weather-metrics">
            <div class="rc-weather-metric-item">
              <span class="rc-weather-metric-icon">${SVG_ICONS.droplet}</span>
              <div class="rc-weather-metric-info">
                <span class="rc-weather-metric-label">Humidity</span>
                <span class="rc-weather-metric-val">${current.humidity ?? '--'}%</span>
              </div>
            </div>
            <div class="rc-weather-metric-item">
              <span class="rc-weather-metric-icon">${SVG_ICONS.wind}</span>
              <div class="rc-weather-metric-info">
                <span class="rc-weather-metric-label">Wind</span>
                <span class="rc-weather-metric-val">${escapeHtml(current.wind || '--')}</span>
              </div>
            </div>
            <div class="rc-weather-metric-item">
              <span class="rc-weather-metric-icon">${SVG_ICONS.weatherRain}</span>
              <div class="rc-weather-metric-info">
                <span class="rc-weather-metric-label">Rain</span>
                <span class="rc-weather-metric-val">${current.rain_chance ?? 0}%</span>
              </div>
            </div>
          </div>

          ${daysHtml ? `<div class="rc-weather-forecast-list">${daysHtml}</div>${toggleBtn}` : ''}
        </div>

        <!-- View 2: Day Detail & Hourly Forecast -->
        <div class="rc-weather-view rc-weather-view-detail" style="display: none;">
          <div class="rc-weather-detail-hd">
            <button class="rc-weather-back-btn" type="button" aria-label="Back to 7-day forecast">
              ${SVG_ICONS.chevronLeft}
              <span class="rc-weather-back-day-name">${initialDayName}</span>
            </button>
            <span class="rc-weather-detail-sub">${escapeHtml(data.city)}${data.country ? `, ${escapeHtml(data.country)}` : ''}</span>
          </div>

          <div class="rc-weather-scrub">
            ${initialScrubHtml}
          </div>

          <div class="rc-weather-detail-meta">
            <span>${SVG_ICONS.droplet} <span class="rc-w-hum">${initialHum}%</span></span>
            <span>${SVG_ICONS.wind} <span class="rc-w-wind">${initialWind}</span></span>
            <span>${SVG_ICONS.weatherRain} <span class="rc-w-rain">${initialRain}%</span></span>
          </div>

          <div class="rc-weather-detail-row">
            <span class="rc-weather-k">Feels like</span>
            <span class="rc-weather-v rc-w-feels">${initialFeels}°</span>
          </div>
          <div class="rc-weather-detail-row">
            <span class="rc-weather-k">UV index</span>
            <span class="rc-weather-v rc-w-uv">${initialUv}</span>
          </div>
          <div class="rc-weather-detail-row">
            <span class="rc-weather-k">Pressure</span>
            <span class="rc-weather-v rc-w-press">${initialPressure} hPa</span>
          </div>

          <div class="rc-weather-sunrow">
            <div class="rc-weather-suncol">
              <span class="rc-weather-sunlabel">${SVG_ICONS.sunrise} Sunrise</span>
              <b class="rc-w-sunrise">${initialSunRise}</b>
            </div>
            <div class="rc-weather-suncol rc-weather-suncol-right">
              <span class="rc-weather-sunlabel">${SVG_ICONS.sunset} Sunset</span>
              <b class="rc-w-sunset">${initialSunSet}</b>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  // 2. Email List Card
  function renderEmailListCard(data) {
    if (!data || !Array.isArray(data.items)) return '';
    const items = data.items;
    if (items.length === 0) {
      return `<div class="rc-empty-state">No emails found matching your query.</div>`;
    }

    let itemsHtml = '';
    items.forEach((item, idx) => {
      const sender = escapeHtml(item.sender_name || item.sender_email || 'Unknown');
      const subject = escapeHtml(item.subject || '(No Subject)');
      const snippet = escapeHtml(item.snippet || '');
      const timeStr = escapeHtml(formatRelativeTime(item.received_at || item.date));
      const initials = escapeHtml((item.sender_name || item.sender_email || 'U').slice(0, 2).toUpperCase());
      const extraClass = idx >= DEFAULT_MAX_ROWS ? ' rc-row-hidden' : '';

      itemsHtml += `
        <div class="rc-email-item${extraClass}">
          <div class="rc-email-item-header">
            <div class="rc-email-sender-wrap">
              <span class="rc-avatar-initials">${initials}</span>
              ${item.unread ? '<span class="rc-dot-unread" title="Unread"></span>' : ''}
              <span class="rc-email-sender">${sender}</span>
            </div>
            <span class="rc-email-date">${timeStr}</span>
          </div>
          <a class="rc-email-subject" href="${item.thread_url ? escapeHtml(item.thread_url) : '#'}" target="_blank" rel="noopener noreferrer">${subject}</a>
          ${snippet ? `<div class="rc-email-snippet">${snippet}</div>` : ''}
        </div>
      `;
    });

    const toggleBtn = items.length > DEFAULT_MAX_ROWS ? `
      <button class="rc-toggle-btn" type="button" data-expanded="false" data-total="${items.length}">
        <span>Show all ${items.length} emails</span>
        ${SVG_ICONS.chevronDown}
      </button>
    ` : '';

    return `
      <div class="rc-card rc-email-list" role="group" aria-label="Email results">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${SVG_ICONS.gmail}</span>
            <span>${escapeHtml(data.query || 'Recent Emails')}</span>
          </div>
          <span class="rc-badge">${data.total || items.length} message${(data.total || items.length) === 1 ? '' : 's'}${data.unread ? ` (${data.unread} unread)` : ''}</span>
        </div>
        <div class="rc-email-items">${itemsHtml}</div>
        ${toggleBtn}
        <div class="rc-email-bottom-chips">
          <button class="rc-action-chip" type="button" data-prompt="Draft a reply to the latest email">Draft reply</button>
          <button class="rc-action-chip" type="button" data-prompt="Summarize recent email threads">Summarize</button>
          <button class="rc-action-chip" type="button" data-prompt="Mark these emails as read">Mark read</button>
        </div>
      </div>
    `;
  }

  // 3. Slack Mentions Card
  function renderSlackMentionsCard(data) {
    if (!data || !Array.isArray(data.items)) return '';
    const items = data.items;
    if (items.length === 0) {
      return `<div class="rc-empty-state">No Slack messages found.</div>`;
    }

    let itemsHtml = '';
    items.forEach((m, idx) => {
      const channel = m.channel || 'general';
      const isDm = channel.toLowerCase().startsWith('dm');
      const channelLabel = isDm ? escapeHtml(channel) : '#' + escapeHtml(channel);
      const sender = escapeHtml(m.sender || 'Slack User');
      const timeStr = escapeHtml(formatRelativeTime(m.ts ? Number(m.ts) * 1000 : m.date));
      const extraClass = idx >= DEFAULT_MAX_ROWS ? ' rc-row-hidden' : '';

      itemsHtml += `
        <div class="rc-slack-item${extraClass}">
          <div class="rc-slack-item-header">
            <span class="rc-slack-meta">${channelLabel} · ${sender}</span>
            <span class="rc-slack-time">${timeStr}</span>
          </div>
          <div class="rc-slack-text">${escapeHtml(m.text || '')}</div>
          <div class="rc-slack-actions">
            <button class="rc-action-chip" type="button" data-prompt="Draft a reply to ${sender} in ${channelLabel}">Draft reply</button>
            ${m.permalink ? `<a class="rc-link-btn" href="${escapeHtml(m.permalink)}" target="_blank" rel="noopener noreferrer">View in Slack ↗</a>` : ''}
          </div>
        </div>
      `;
    });

    const toggleBtn = items.length > DEFAULT_MAX_ROWS ? `
      <button class="rc-toggle-btn" type="button" data-expanded="false" data-total="${items.length}">
        <span>Show all ${items.length} messages</span>
        ${SVG_ICONS.chevronDown}
      </button>
    ` : '';

    return `
      <div class="rc-card rc-slack-mentions" role="group" aria-label="Slack activity">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${SVG_ICONS.slack}</span>
            <span>Slack Activity</span>
          </div>
          <span class="rc-badge">${items.length} item${items.length === 1 ? '' : 's'}</span>
        </div>
        <div class="rc-slack-items">${itemsHtml}</div>
        ${toggleBtn}
      </div>
    `;
  }

  // 4 & 5. Sheet View & Sheet Update Card
  function renderSheetCard(data, isUpdate) {
    if (!data) return '';
    const columns = (Array.isArray(data.columns) ? data.columns : []).slice(0, 4);
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const highlightIdx = isUpdate && typeof data.highlight_row_index === 'number' ? data.highlight_row_index : (isUpdate ? rows.length - 1 : -1);

    const thHtml = columns.map(c => `<th>${escapeHtml(c)}</th>`).join('');
    let trHtml = '';

    const previewRows = rows.slice(0, 5);
    previewRows.forEach((row, idx) => {
      const isHighlighted = idx === highlightIdx;
      const cells = (Array.isArray(row) ? row : [row]).slice(0, 4);
      const tdHtml = cells.map(cell => `<td>${escapeHtml(cell)}</td>`).join('');
      trHtml += `<tr class="${isHighlighted ? 'rc-sheet-row-highlight' : ''}">${tdHtml}</tr>`;
    });

    return `
      <div class="rc-card rc-sheet-card" role="group" aria-label="${escapeHtml(data.title || 'Google Sheet')}">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${SVG_ICONS.sheets}</span>
            <span>${escapeHtml(data.title || (isUpdate ? 'Sheet Updated' : 'Google Sheet'))}</span>
          </div>
          ${isUpdate ? '<span class="rc-badge rc-badge-emerald">Row added</span>' : `<span class="rc-badge">Latest ${previewRows.length} of ${rows.length || previewRows.length}</span>`}
        </div>
        ${columns.length || previewRows.length ? `
          <div class="rc-sheet-wrap">
            <table class="rc-sheet-table">
              ${thHtml ? `<thead><tr>${thHtml}</tr></thead>` : ''}
              <tbody>${trHtml}</tbody>
            </table>
          </div>
        ` : '<div class="rc-empty-state">No rows found in sheet.</div>'}
        <div class="rc-sheet-footer">
          <span class="rc-sheet-meta">${isUpdate && data.row_number ? `Added to row ${data.row_number}` : `${rows.length || previewRows.length} total rows`}</span>
          <div style="display: flex; gap: 8px;">
            ${rows.length > 5 ? `<button class="rc-action-chip" type="button" data-prompt="Show all rows from ${escapeHtml(data.title || 'the sheet')}">Show more rows</button>` : ''}
            ${data.url ? `<a class="rc-link-btn" href="${escapeHtml(data.url)}" target="_blank" rel="noopener noreferrer">Open sheet ↗</a>` : ''}
          </div>
        </div>
      </div>
    `;
  }

  // 6. Calendar Event Card
  function renderCalendarEventCard(data) {
    if (!data) return '';
    const events = Array.isArray(data.events) ? data.events : (data.title ? [data] : []);
    if (events.length === 0) return '';
    const ev = events[0];

    const startDate = ev.start ? new Date(ev.start) : null;
    const endDate = ev.end ? new Date(ev.end) : null;
    const validDate = (startDate && !isNaN(startDate.getTime()))
      ? startDate
      : ((endDate && !isNaN(endDate.getTime())) ? endDate : new Date());

    const weekday = validDate.toLocaleDateString(undefined, { weekday: 'short' });
    const dayNumber = validDate.getDate();

    const startTimeStr = (startDate && !isNaN(startDate.getTime()))
      ? startDate.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
      : (ev.start || '');
    const endTimeStr = (endDate && !isNaN(endDate.getTime()))
      ? endDate.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
      : (ev.end || '');

    const timeStr = [startTimeStr, endTimeStr].filter(Boolean).join(' – ') || 'Time TBA';

    const isEnded = (endDate && !isNaN(endDate.getTime()))
      ? endDate.getTime() < Date.now()
      : ((startDate && !isNaN(startDate.getTime())) ? startDate.getTime() < Date.now() : false);

    const isConfirmed = ev.status === 'confirmed' || data.status === 'confirmed';

    const rawAttendees = ev.attendees;
    const hasAttendees = Array.isArray(rawAttendees)
      ? rawAttendees.length > 0
      : Boolean(rawAttendees && String(rawAttendees).trim() && String(rawAttendees).trim() !== 'None');
    const attendeesText = hasAttendees
      ? (Array.isArray(rawAttendees) ? rawAttendees.join(', ') : String(rawAttendees))
      : '';

    return `
      <div class="rc-card rc-calendar-card" role="group" aria-label="Event details">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${SVG_ICONS.calendar}</span>
            <span>Calendar Event</span>
          </div>
          ${isConfirmed ? '<span class="rc-badge rc-badge-emerald">Confirmed</span>' : ''}
        </div>
        <div class="rc-calendar-event-body">
          <div class="rc-cal-date-block">
            <span class="rc-cal-date-weekday">${weekday}</span>
            <span class="rc-cal-date-number">${dayNumber}</span>
          </div>
          <div class="rc-cal-event-details">
            <span class="rc-cal-event-title">${escapeHtml(ev.title || 'Scheduled Event')}</span>
            <span class="rc-cal-event-time">${SVG_ICONS.clock} ${escapeHtml(timeStr)}</span>
            ${hasAttendees ? `<span class="rc-cal-event-attendees">Attendees: ${escapeHtml(attendeesText)}</span>` : ''}
          </div>
        </div>
        <div class="rc-cal-event-actions">
          ${ev.meet_url && !isEnded ? `<a class="rc-link-btn rc-chip-primary" href="${escapeHtml(ev.meet_url)}" target="_blank" rel="noopener noreferrer">${SVG_ICONS.video} Join with Meet</a>` : ''}
          ${ev.html_link ? `<a class="rc-link-btn" href="${escapeHtml(ev.html_link)}" target="_blank" rel="noopener noreferrer">Open in Calendar ↗</a>` : ''}
        </div>
      </div>
    `;
  }

  // 7. Calendar List Card (NEW)
  function renderCalendarListCard(data) {
    if (!data || !Array.isArray(data.events)) return '';
    const events = data.events;
    if (events.length === 0) {
      return `<div class="rc-empty-state">No upcoming events scheduled.</div>`;
    }

    let itemsHtml = '';
    events.forEach((ev, idx) => {
      const extraClass = idx >= DEFAULT_MAX_ROWS ? ' rc-row-hidden' : '';
      const sDate = ev.start ? new Date(ev.start) : null;
      const eDate = ev.end ? new Date(ev.end) : null;
      const timeLabel = sDate && !isNaN(sDate.getTime())
        ? sDate.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
        : (ev.start || 'Time TBA');
      const duration = ev.duration || (ev.end && ev.start ? 'Scheduled' : 'Event');
      const attendees = ev.attendees ? (Array.isArray(ev.attendees) ? `${ev.attendees.length} people` : String(ev.attendees)) : '';
      const metaSub = [duration, attendees].filter(Boolean).join(' · ');

      const isEnded = eDate && !isNaN(eDate.getTime())
        ? eDate.getTime() < Date.now()
        : (sDate && !isNaN(sDate.getTime()) ? sDate.getTime() < Date.now() : false);

      itemsHtml += `
        <div class="rc-cal-list-item${extraClass}">
          <span class="rc-cal-list-time">${escapeHtml(timeLabel)}</span>
          <div class="rc-cal-list-info">
            <span class="rc-cal-list-title">${escapeHtml(ev.title || 'Event')}</span>
            ${metaSub ? `<span class="rc-cal-list-sub">${escapeHtml(metaSub)}</span>` : ''}
          </div>
          ${ev.meet_url && !isEnded ? `<a class="rc-cal-list-video" href="${escapeHtml(ev.meet_url)}" target="_blank" rel="noopener noreferrer" title="Join Meeting">${SVG_ICONS.video}</a>` : ''}
        </div>
      `;
    });

    const toggleBtn = events.length > DEFAULT_MAX_ROWS ? `
      <button class="rc-toggle-btn" type="button" data-expanded="false" data-total="${events.length}">
        <span>Show all ${events.length} events</span>
        ${SVG_ICONS.chevronDown}
      </button>
    ` : '';

    return `
      <div class="rc-card rc-calendar-list" role="group" aria-label="Upcoming schedule">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${SVG_ICONS.calendar}</span>
            <span>Upcoming Schedule</span>
          </div>
          <span class="rc-badge">${events.length} event${events.length === 1 ? '' : 's'}</span>
        </div>
        <div class="rc-cal-list-items">${itemsHtml}</div>
        ${toggleBtn}
      </div>
    `;
  }

  // 8. Search Summary Card
  function renderSearchSummaryCard(data) {
    if (!data) return '';
    const points = Array.isArray(data.points) ? data.points : [];
    const sources = Array.isArray(data.sources) ? data.sources : [];

    let pointsHtml = '';
    points.forEach(p => {
      pointsHtml += `
        <div class="rc-search-point">
          ${p.tag ? `<span class="rc-search-tag">${escapeHtml(p.tag)}</span>` : ''}
          <span>${escapeHtml(p.text || '')}</span>
        </div>
      `;
    });

    let sourcesHtml = '';
    sources.slice(0, 3).forEach(s => {
      sourcesHtml += `
        <a class="rc-source-pill" href="${escapeHtml(s.url || '#')}" target="_blank" rel="noopener noreferrer">
          ${SVG_ICONS.link}
          <span>${escapeHtml(s.domain || 'source')}</span>
        </a>
      `;
    });

    return `
      <div class="rc-card rc-search-card" role="group" aria-label="Search overview">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${SVG_ICONS.search}</span>
            <span>${escapeHtml(data.title || 'Search Overview')}</span>
          </div>
          <span class="rc-badge">${points.length} points · ${data.source_count || sources.length} sources</span>
        </div>
        ${pointsHtml ? `<div class="rc-search-points">${pointsHtml}</div>` : ''}
        ${sourcesHtml ? `<div class="rc-sources-row">${sourcesHtml}${data.doc_url ? `<a class="rc-link-btn" href="${escapeHtml(data.doc_url)}" target="_blank" rel="noopener noreferrer">Open note ↗</a>` : ''}</div>` : ''}
      </div>
    `;
  }

  // 9. News Card (NEW)
  function renderNewsCard(data) {
    if (!data || !Array.isArray(data.items)) return '';
    const items = data.items;
    if (items.length === 0) return '';

    let itemsHtml = '';
    items.forEach((item, idx) => {
      const extraClass = idx >= DEFAULT_MAX_ROWS ? ' rc-row-hidden' : '';
      const domain = escapeHtml(item.domain || (item.url ? new URL(item.url).hostname.replace('www.', '') : 'News'));
      const timeStr = escapeHtml(formatRelativeTime(item.published_at || item.date));
      const meta = [domain, timeStr].filter(Boolean).join(' · ');

      itemsHtml += `
        <div class="rc-news-item${extraClass}">
          <span class="rc-news-meta">${meta}</span>
          <a class="rc-news-headline" href="${escapeHtml(item.url || '#')}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.title || '(No headline)')}</a>
        </div>
      `;
    });

    const toggleBtn = items.length > DEFAULT_MAX_ROWS ? `
      <button class="rc-toggle-btn" type="button" data-expanded="false" data-total="${items.length}">
        <span>Show all ${items.length} articles</span>
        ${SVG_ICONS.chevronDown}
      </button>
    ` : '';

    return `
      <div class="rc-card rc-news-card" role="group" aria-label="News results">
        <div class="rc-header">
          <div class="rc-header-title">
            <span class="rc-header-icon">${SVG_ICONS.news}</span>
            <span>News</span>
          </div>
          <span class="rc-badge">Searched ${formatRelativeTime(new Date())}</span>
        </div>
        <div class="rc-news-items">${itemsHtml}</div>
        ${toggleBtn}
        <div class="rc-news-actions">
          <button class="rc-action-chip" type="button" data-prompt="Summarize news articles to Google Docs">Summarize to Docs</button>
        </div>
      </div>
    `;
  }

  function getCardSkeletonHtml(cardType) {
    const type = (cardType || '').toLowerCase();
    let iconSvg = SVG_ICONS.search;
    let title = 'Preparing card';
    let statusLabel = 'Fetching details…';
    let bodyHtml = '';

    if (type.includes('weather')) {
      iconSvg = SVG_ICONS.weatherClear;
      title = 'Weather';
      statusLabel = 'Loading forecast…';
      bodyHtml = `
        <div class="rc-skeleton-weather-body">
          <div class="rc-shimmer-box rc-skeleton-weather-temp"></div>
          <div class="rc-skeleton-weather-meta">
            <div class="rc-shimmer-box rc-skeleton-line short"></div>
            <div class="rc-shimmer-box rc-skeleton-line medium"></div>
          </div>
        </div>
        <div class="rc-skeleton-weather-days">
          <div class="rc-shimmer-box rc-skeleton-weather-day-chip"></div>
          <div class="rc-shimmer-box rc-skeleton-weather-day-chip"></div>
          <div class="rc-shimmer-box rc-skeleton-weather-day-chip"></div>
          <div class="rc-shimmer-box rc-skeleton-weather-day-chip"></div>
        </div>
      `;
    } else if (type.includes('calendar')) {
      iconSvg = SVG_ICONS.calendar;
      title = 'Google Calendar';
      statusLabel = 'Loading schedule…';
      bodyHtml = `
        <div class="rc-skeleton-list">
          <div class="rc-skeleton-list-item">
            <div class="rc-shimmer-box" style="width: 60px; height: 16px; border-radius: 4px;"></div>
            <div class="rc-shimmer-box" style="flex: 1; height: 16px; border-radius: 4px;"></div>
          </div>
          <div class="rc-skeleton-list-item">
            <div class="rc-shimmer-box" style="width: 60px; height: 16px; border-radius: 4px;"></div>
            <div class="rc-shimmer-box" style="flex: 0.75; height: 16px; border-radius: 4px;"></div>
          </div>
        </div>
      `;
    } else if (type.includes('email') || type.includes('gmail')) {
      iconSvg = SVG_ICONS.gmail;
      title = 'Gmail';
      statusLabel = 'Fetching messages…';
      bodyHtml = `
        <div class="rc-skeleton-list">
          <div class="rc-skeleton-list-item">
            <div class="rc-shimmer-box rc-skeleton-avatar"></div>
            <div style="flex: 1; display: flex; flex-direction: column; gap: 6px;">
              <div class="rc-shimmer-box rc-skeleton-line short"></div>
              <div class="rc-shimmer-box rc-skeleton-line full"></div>
            </div>
          </div>
          <div class="rc-skeleton-list-item">
            <div class="rc-shimmer-box rc-skeleton-avatar"></div>
            <div style="flex: 1; display: flex; flex-direction: column; gap: 6px;">
              <div class="rc-shimmer-box rc-skeleton-line short"></div>
              <div class="rc-shimmer-box rc-skeleton-line medium"></div>
            </div>
          </div>
        </div>
      `;
    } else if (type.includes('slack')) {
      iconSvg = SVG_ICONS.slack;
      title = 'Slack';
      statusLabel = 'Reading messages…';
      bodyHtml = `
        <div class="rc-skeleton-list">
          <div class="rc-skeleton-list-item">
            <div class="rc-shimmer-box rc-skeleton-avatar"></div>
            <div style="flex: 1; display: flex; flex-direction: column; gap: 6px;">
              <div class="rc-shimmer-box rc-skeleton-line short"></div>
              <div class="rc-shimmer-box rc-skeleton-line full"></div>
            </div>
          </div>
        </div>
      `;
    } else if (type.includes('sheet')) {
      iconSvg = SVG_ICONS.sheets;
      title = 'Google Sheets';
      statusLabel = 'Loading data…';
      bodyHtml = `
        <div class="rc-skeleton-list">
          <div class="rc-shimmer-box" style="height: 22px; border-radius: 4px;"></div>
          <div class="rc-shimmer-box" style="height: 18px; border-radius: 4px;"></div>
          <div class="rc-shimmer-box" style="height: 18px; border-radius: 4px;"></div>
        </div>
      `;
    } else {
      bodyHtml = `
        <div class="rc-skeleton-list">
          <div class="rc-shimmer-box rc-skeleton-line full"></div>
          <div class="rc-shimmer-box rc-skeleton-line medium"></div>
          <div class="rc-shimmer-box rc-skeleton-line short"></div>
        </div>
      `;
    }

    return `
      <div class="rc-card rc-skeleton-card" role="status" aria-label="Loading card content">
        <div class="rc-skeleton-header">
          <div class="rc-skeleton-header-left">
            ${iconSvg}
            <span style="font-size: 13px; font-weight: 600; color: var(--ink);">${escapeHtml(title)}</span>
          </div>
          <div class="rc-skeleton-badge">
            <span class="rc-skeleton-dot"></span>
            <span>${escapeHtml(statusLabel)}</span>
          </div>
        </div>
        <div class="rc-skeleton-content">
          ${bodyHtml}
        </div>
      </div>
    `;
  }

  // Skeleton Loader State
  function renderCardSkeleton(cardType) {
    const wrapper = document.createElement('div');
    wrapper.className = 'rc-container rc-skeleton-container';
    wrapper.innerHTML = getCardSkeletonHtml(cardType);
    return wrapper;
  }

  // --- Main Dispatcher ---

  function renderResultCard(card) {
    if (!card || typeof card !== 'object') return null;
    const type = card.type || (card.kind === 'result' ? card.data_type : null);
    const data = card.data || card;

    let html = '';
    try {
      switch (type) {
        case 'weather':
          html = renderWeatherCard(data);
          break;
        case 'email_list':
          html = renderEmailListCard(data);
          break;
        case 'slack_mentions':
          html = renderSlackMentionsCard(data);
          break;
        case 'sheet_view':
          html = renderSheetCard(data, false);
          break;
        case 'sheet_update':
          html = renderSheetCard(data, true);
          break;
        case 'calendar_event':
          html = renderCalendarEventCard(data);
          break;
        case 'calendar_list':
          html = renderCalendarListCard(data);
          break;
        case 'search_summary':
          html = renderSearchSummaryCard(data);
          break;
        case 'news':
          html = renderNewsCard(data);
          break;
        case 'skeleton':
          html = getCardSkeletonHtml(data.card_type || data.type);
          break;
        default:
          return null;
      }
    } catch (err) {
      console.warn('Result card renderer error:', err);
      return null;
    }

    if (!html) return null;

    const wrapper = document.createElement('div');
    wrapper.className = 'rc-container';
    wrapper.innerHTML = html;

    // Attach click handlers to action chips (PREFILL COMPOSER ONLY, NEVER SEND)
    wrapper.querySelectorAll('.rc-action-chip').forEach(btn => {
      btn.addEventListener('click', e => {
        e.preventDefault();
        const prompt = btn.getAttribute('data-prompt');
        if (prompt) handleChipClick(prompt);
      });
    });

    // Attach click handler for list toggle buttons
    wrapper.querySelectorAll('.rc-toggle-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        e.preventDefault();
        const isExpanded = btn.getAttribute('data-expanded') === 'true';
        const cardParent = btn.closest('.rc-card');
        if (!cardParent) return;

        const hiddenRows = cardParent.querySelectorAll('.rc-row-hidden, .rc-row-expanded');
        hiddenRows.forEach(row => {
          if (isExpanded) {
            row.classList.remove('rc-row-expanded');
            row.classList.add('rc-row-hidden');
          } else {
            row.classList.remove('rc-row-hidden');
            row.classList.add('rc-row-expanded');
          }
        });

        const span = btn.querySelector('span');
        const total = btn.getAttribute('data-total') || '';
        if (isExpanded) {
          btn.setAttribute('data-expanded', 'false');
          if (span) span.textContent = `Show all ${total}`;
          btn.innerHTML = `<span>Show all ${total}</span> ${SVG_ICONS.chevronDown}`;
        } else {
          btn.setAttribute('data-expanded', 'true');
          if (span) span.textContent = 'Show less';
          btn.innerHTML = `<span>Show less</span> ${SVG_ICONS.chevronUp}`;
        }
      });
    });

    // Weather card day row clicks -> transition to Day Detail view
    wrapper.querySelectorAll('.rc-weather-day-row[data-day-index]').forEach(row => {
      row.addEventListener('click', () => {
        const cardParent = row.closest('.rc-card.rc-weather');
        const dayIdx = parseInt(row.getAttribute('data-day-index'), 10);
        openWeatherDayDetail(cardParent, dayIdx);
      });
    });

    // Weather back button -> return to 7-day list
    wrapper.querySelectorAll('.rc-weather-back-btn').forEach(btn => {
      btn.addEventListener('click', e => {
        e.preventDefault();
        const cardParent = btn.closest('.rc-card.rc-weather');
        closeWeatherDayDetail(cardParent);
      });
    });

    return wrapper;
  }

  function openWeatherDayDetail(cardParent, dayIdx) {
    if (!cardParent) return;
    const rawDataScript = cardParent.querySelector('.rc-weather-raw-data');
    if (!rawDataScript) return;
    let data;
    try {
      // Decode HTML entities if textContent was escaped
      const tempDiv = document.createElement('div');
      tempDiv.innerHTML = rawDataScript.textContent;
      data = JSON.parse(tempDiv.textContent);
    } catch {
      try {
        data = JSON.parse(rawDataScript.textContent);
      } catch (err) {
        console.warn('Failed parsing raw weather data:', err);
        return;
      }
    }

    const days = data.days || [];
    const day = days[dayIdx] || days[0];
    if (!day) return;

    const isToday = dayIdx === 0;
    const dayName = day.label || getWeekdayName(day.date);
    const hours = (day.hourly && day.hourly.length) ? day.hourly : (data.hourly || []);

    const backDaySpan = cardParent.querySelector('.rc-weather-back-day-name');
    if (backDaySpan) backDaySpan.textContent = dayName;

    const scrub = cardParent.querySelector('.rc-weather-scrub');
    if (scrub) scrub.innerHTML = renderWeatherHourlyScrubHtml(hours, isToday);

    const humEl = cardParent.querySelector('.rc-w-hum');
    if (humEl) humEl.textContent = `${day.humidity ?? data.current?.humidity ?? '--'}%`;

    const windEl = cardParent.querySelector('.rc-w-wind');
    if (windEl) windEl.textContent = `${day.wind || data.current?.wind || '--'}`;

    const rainEl = cardParent.querySelector('.rc-w-rain');
    if (rainEl) rainEl.textContent = `${day.rain_chance ?? data.current?.rain_chance ?? 0}%`;

    const feelsEl = cardParent.querySelector('.rc-w-feels');
    if (feelsEl) {
      const feelVal = Math.round(day.feels_like ?? day.high ?? data.current?.feels_like ?? data.current?.temp ?? 0);
      feelsEl.textContent = `${feelVal}°`;
    }

    const uvEl = cardParent.querySelector('.rc-w-uv');
    if (uvEl) uvEl.textContent = getUvCategory(day.uv_index_max ?? data.current?.uv_index);

    const pressEl = cardParent.querySelector('.rc-w-press');
    if (pressEl) {
      const pressVal = Math.round(day.pressure ?? data.current?.pressure ?? 1013);
      pressEl.textContent = `${pressVal} hPa`;
    }

    const sunriseEl = cardParent.querySelector('.rc-w-sunrise');
    if (sunriseEl) sunriseEl.textContent = formatSunTime(day.sunrise);

    const sunsetEl = cardParent.querySelector('.rc-w-sunset');
    if (sunsetEl) sunsetEl.textContent = formatSunTime(day.sunset);

    const overview = cardParent.querySelector('.rc-weather-view-overview');
    const detail = cardParent.querySelector('.rc-weather-view-detail');
    if (overview && detail) {
      overview.style.display = 'none';
      detail.style.display = 'block';
    }
  }

  function closeWeatherDayDetail(cardParent) {
    if (!cardParent) return;
    const overview = cardParent.querySelector('.rc-weather-view-overview');
    const detail = cardParent.querySelector('.rc-weather-view-detail');
    if (overview && detail) {
      detail.style.display = 'none';
      overview.style.display = 'block';
    }
  }

  // Global event delegation for weather card interactions (handles static/injected HTML)
  document.addEventListener('click', e => {
    const dayRow = e.target.closest('.rc-weather-day-row[data-day-index]');
    if (dayRow) {
      const cardParent = dayRow.closest('.rc-card.rc-weather');
      const idx = parseInt(dayRow.getAttribute('data-day-index'), 10);
      if (cardParent && !isNaN(idx)) {
        openWeatherDayDetail(cardParent, idx);
        return;
      }
    }

    const backBtn = e.target.closest('.rc-weather-back-btn');
    if (backBtn) {
      e.preventDefault();
      const cardParent = backBtn.closest('.rc-card.rc-weather');
      if (cardParent) {
        closeWeatherDayDetail(cardParent);
      }
    }
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') {
      const dayRow = e.target.closest('.rc-weather-day-row[data-day-index]');
      if (dayRow && document.activeElement === dayRow) {
        e.preventDefault();
        dayRow.click();
      }
    }
  });

  // Export globally
  window.renderResultCard = renderResultCard;
  window.renderCardSkeleton = renderCardSkeleton;
})();
