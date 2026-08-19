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
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || Object.values(data).flat().join(' ') || 'Something went wrong.');
    return data;
  };
  const formData = form => Object.fromEntries(new FormData(form).entries());
  const showError = error => { const target = document.querySelector('#form-error'); if (target) target.textContent = error.message; };

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
        card.querySelector('.account-state').textContent = connected ? 'Connected' : 'Not connected';
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
      const state = { threadId: null };
      const transcript = document.querySelector('#transcript');
      const input = document.querySelector('#message-input');
      const title = document.querySelector('#thread-title');
      const threadList = document.querySelector('#thread-list');
      document.querySelector('#user-label').textContent = localStorage.getItem('ops_user') || 'Workspace online';
      const loadIntegrations = async () => { try { const data = await api('/api/integrations/status/'); (data.integrations || []).forEach(item => { const state = document.querySelector(`#${item.service}-state`); if (state) state.textContent = item.enabled ? 'Connected' : 'Connect'; }); } catch (error) { console.warn('Could not load integrations', error); } };
      document.querySelectorAll('[data-integration]').forEach(button => button.onclick = async () => { const service = button.dataset.integration; try { const data = await api(`/api/integrations/${service}/connect/`); window.location.href = data.authorization_url; } catch (error) { addMessage('agent', error.message); } });
      const addMessage = (role, content, pending = false) => { const item = document.createElement('article'); item.className = `message ${role} ${pending ? 'pending' : ''}`; item.innerHTML = `<span class="message-label">${role === 'user' ? 'YOU' : 'OPS AGENT'}</span><div class="message-content"></div>`; item.querySelector('.message-content').textContent = content; transcript.appendChild(item); transcript.scrollTop = transcript.scrollHeight; return item; };
      const selectThread = async id => { state.threadId = id; title.textContent = 'Thread ' + id; document.querySelectorAll('.thread-item').forEach(item => item.classList.toggle('active', item.dataset.id == id)); transcript.innerHTML = '<div class="loading-line">Loading thread history...</div>'; try { const messages = await api(`/api/thread/${encodeURIComponent(id)}/messages/`); transcript.innerHTML = ''; (Array.isArray(messages) ? messages : messages.results || []).forEach(message => addMessage(message.role === 'assistant' ? 'agent' : message.role, message.content)); } catch (error) { transcript.innerHTML = ''; addMessage('agent', error.message); } };
      const loadThreads = async () => { try { const data = await api('/api/list_thread/'); const threads = Array.isArray(data) ? data : data.results || []; threadList.innerHTML = threads.length ? '' : '<div class="rail-empty">No threads yet.<br>Start with a question.</div>'; threads.forEach(thread => { const item = document.createElement('button'); item.className = 'thread-item'; item.dataset.id = thread.id; item.textContent = thread.name || `Thread ${thread.id}`; item.onclick = () => selectThread(thread.id); threadList.appendChild(item); }); if (threads[0]) selectThread(threads[0].id); } catch (error) { threadList.innerHTML = `<div class="rail-empty">${error.message}</div>`; } };
      const send = async message => { const pending = addMessage('agent', 'Thinking through that now...', true); try { const endpoint = state.threadId ? `/api/thread/${state.threadId}/chat/` : '/api/chat/'; const data = await api(endpoint, { method: 'POST', body: JSON.stringify({ message }) }); pending.remove(); if (data.thread_id && !state.threadId) { state.threadId = data.thread_id; title.textContent = 'New thread'; await loadThreads(); } if (data.status === 'approval_required') { document.querySelector('#approval-card').classList.remove('hidden'); document.querySelector('#approval-detail').textContent = JSON.stringify(data.approval, null, 2); } else addMessage('agent', data.response || 'Done.'); } catch (error) { pending.remove(); addMessage('agent', error.message); } };
      document.querySelector('#chat-form').addEventListener('submit', event => { event.preventDefault(); const message = input.value.trim(); if (!message) return; addMessage('user', message); input.value = ''; send(message); });
      input.addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); document.querySelector('#chat-form').requestSubmit(); } });
      document.querySelectorAll('[data-prompt]').forEach(button => button.onclick = () => { input.value = button.dataset.prompt; input.focus(); });
      document.querySelector('#new-thread').onclick = () => { state.threadId = null; title.textContent = 'Good morning.'; transcript.innerHTML = ''; input.focus(); };
      document.querySelector('#logout').onclick = () => { localStorage.removeItem('ops_access'); localStorage.removeItem('ops_refresh'); localStorage.removeItem('ops_user'); window.location = '/signin/'; };
      document.querySelector('#approve-action').onclick = () => approve(true); document.querySelector('#cancel-action').onclick = () => approve(false);
      async function approve(value) { try { const data = await api(`/api/thread/${state.threadId}/approve-email/`, { method: 'POST', body: JSON.stringify({ approved: value }) }); document.querySelector('#approval-card').classList.add('hidden'); addMessage('agent', data.result || 'Action updated.'); } catch (error) { addMessage('agent', error.message); } }
      document.querySelector('#clock').textContent = new Intl.DateTimeFormat([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date()); loadIntegrations(); loadThreads();
    }
  };
})();
