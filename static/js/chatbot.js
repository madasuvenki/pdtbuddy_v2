// chatbot.js -- Premium PDT Buddy Chatbot
// Features:
//  * Full-height drawer with proper flex scroll
//  * Tab system: Chat | CR Info | History
//  * CR Info auto-loads from /api/cr_info_summary when CR detected
//  * Session persistence via sessionStorage + localStorage backup
//  * Restore banner when previous session exists after refresh
//  * History tab with export + clear
//  * Status line in header

document.addEventListener('DOMContentLoaded', () => {

    // -- DOM refs --------------------------------------------------------------
        const chatBtn           = document.getElementById('openChatBtn');
    const chatWindow        = document.getElementById('chatWindow');
    const chatDock          = document.getElementById('chatDock');
    const chatPageShell     = document.getElementById('chatPageShell');
        const chatMessages      = document.getElementById('chatMessages');
    const chatInput         = document.getElementById('chatInput');
    const sendBtn           = document.getElementById('sendBtn');
    const btnMinimize       = document.getElementById('chatbot-minimize');
    const btnClose          = document.getElementById('chatbot-close');
    const btnReset          = document.getElementById('chat-reset');
    const statusLine        = document.getElementById('chatStatusLine');
    const restoreBanner     = document.getElementById('chatRestoreBanner');
    const restoreBtn        = document.getElementById('chatRestoreBtn');
    const dismissRestoreBtn = document.getElementById('chatDismissRestoreBtn');
    const historyList       = document.getElementById('chatHistoryList');
    const exportBtn         = document.getElementById('chatExportBtn');
    const clearHistBtn      = document.getElementById('chatClearHistoryBtn');
    const crInfoTabBadge    = document.getElementById('crInfoTabBadge');
    const crInfoToggleBtn   = document.getElementById('chatCrInfoToggleBtn');
    const ccipSearchInput   = document.getElementById('ccipSearchInput');
    const ccipSearchBtn     = document.getElementById('ccipSearchBtn');
    const ccipContent       = document.getElementById('ccipContent');

    // Tab elements
    const tabs = {
        chat:    { tab: document.getElementById('tabChat'),    panel: document.getElementById('panelChat') },
        crinfo:  { tab: document.getElementById('tabCrInfo'),  panel: document.getElementById('panelCrInfo') },
        history: { tab: document.getElementById('tabHistory'), panel: document.getElementById('panelHistory') }
    };

        // -- State -----------------------------------------------------------------
    let chatContext  = {};
    let _activeTab   = 'chat';
    let _lastCrNum   = null;
    let _crInfoCache = {};
    // Track whether a Yes/No confirmation is currently pending
    let _awaitingConfirm = false;

    // -- Storage helpers -------------------------------------------------------
    const SS = {
        get: (k, def = null) => { try { const v = sessionStorage.getItem(k); return v ? JSON.parse(v) : def; } catch(e) { return def; } },
        set: (k, v)          => { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch(e) {} },
        del: (k)             => { try { sessionStorage.removeItem(k); } catch(e) {} }
    };
    const LS = {
        get: (k, def = null) => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : def; } catch(e) { return def; } },
        set: (k, v)          => { try { localStorage.setItem(k, JSON.stringify(v)); } catch(e) {} },
        del: (k)             => { try { localStorage.removeItem(k); } catch(e) {} }
    };

                // -- Init: always start fresh on page load --------------------------------
    SS.del('pdt_chat_history_v2');
    SS.del('pdt_chat_context');
    SS.del('pdt_chat_visible');
    LS.del('pdt_chat_history_v2_backup');
    chatContext = {};
    let _welcomeDone = false;  // guard: only send welcome once

        function applyChatLayoutSizing() {
        // CSS flex handles sizing now -- just ensure overflow is set and scroll to bottom
        if (!chatMessages) return;
        chatMessages.style.removeProperty('height');
        chatMessages.style.removeProperty('max-height');
        chatMessages.style.removeProperty('min-height');
        chatMessages.style.removeProperty('flex');
        chatMessages.style.setProperty('overflow-y', 'auto', 'important');
        chatMessages.style.setProperty('overflow-x', 'hidden', 'important');
        if (chatWindow) {
            chatWindow.style.setProperty('overflow', 'hidden', 'important');
            chatWindow.style.setProperty('height', '100%', 'important');
        }
    }

                applyChatLayoutSizing();
    window.addEventListener('resize', applyChatLayoutSizing);



        // Hide restore banner -- no session restore on refresh
    if (restoreBanner) restoreBanner.style.display = 'none';
    switchTab(_activeTab);

        // -- Tab switching ---------------------------------------------------------
    function switchTab(name) {
        _activeTab = name;
        Object.keys(tabs).forEach(k => {
            if (!tabs[k].tab || !tabs[k].panel) return;
            const isActive = (k === name);
            tabs[k].tab.classList.toggle('active', isActive);
            tabs[k].panel.style.display = isActive ? 'flex' : 'none';
        });
                if (name === 'history') renderHistoryTab();
        if (name === 'crinfo' && _lastCrNum && !_crInfoCache[_lastCrNum]) loadCrInfoPanel(_lastCrNum);
                                if (name === 'chat') scrollToBottom();
        applyChatLayoutSizing();
    }



    document.querySelectorAll('.chat-tab').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    // -- Open / Close helpers --------------------------------------------------
                            function openChat(triggerWelcome = true) {
        if (!chatWindow) return;
        chatWindow.classList.add('open');
        if (chatDock) chatDock.classList.add('dock-open');
        if (chatPageShell) chatPageShell.classList.add('chat-layout-open');
        document.body.classList.add('chat-open');
        switchTab(_activeTab || 'chat');
        setTimeout(applyChatLayoutSizing, 80);
        // Send welcome only once per session
        if (triggerWelcome && !_welcomeDone && chatMessages && chatMessages.children.length === 0) {
            _welcomeDone = true;
            setTimeout(() => sendWelcome(), 350);
        }
        scrollToBottom();
    }

        // Send welcome without showing a user bubble or incrementing question_count
    async function sendWelcome() {
        showLoadingIndicator();
        try {
            const target = (typeof FLASK_CURRENT_TARGET !== 'undefined' && FLASK_CURRENT_TARGET) ? FLASK_CURRENT_TARGET : 'global';
            const res = await fetch(`/chatbot_message/${target}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: '', context: {}, is_welcome: true })
            });
            const loader = document.getElementById('bot-loading');
            if (loader) loader.remove();
            if (!res.ok) { setStatus('Ready to help'); return; }  // silent fail
            const data = await res.json();
            chatContext = data.context || {};
            renderMessage(data.response, 'bot', chatContext, null, false);
            setStatus('Ready to help');
            scrollToBottom();
        } catch(e) {
            // Silently ignore welcome errors -- never show "Connection error" for welcome
            const loader = document.getElementById('bot-loading');
            if (loader) loader.remove();
            setStatus('Ready to help');
        }
    }


                function closeChat() {
        if (!chatWindow) return;
        chatWindow.classList.remove('open');
        if (chatDock) chatDock.classList.remove('dock-open');
        if (chatPageShell) chatPageShell.classList.remove('chat-layout-open');
        document.body.classList.remove('chat-open');
        // Full reset
        SS.del('pdt_chat_history_v2');
        SS.del('pdt_chat_context');
        SS.del('pdt_chat_visible');
        LS.del('pdt_chat_history_v2_backup');
        if (chatMessages) chatMessages.innerHTML = '';
        chatContext = {};
        _lastCrNum = null;
        _crInfoCache = {};
        _awaitingConfirm = false;
        _welcomeDone = false;
        window._lastCipCR = null;
        if (crInfoTabBadge) crInfoTabBadge.style.display = 'none';
        if (crInfoToggleBtn) crInfoToggleBtn.style.display = 'none';
        if (ccipContent) ccipContent.innerHTML = '<div class="ccip-empty"><div style="font-size:32px;margin-bottom:8px;">&#128269;</div><div>Search a CR number to see full details here.</div></div>';
        if (window.cipClose) window.cipClose();
    }



    // -- Button handlers -------------------------------------------------------
    if (chatBtn) chatBtn.onclick = () => openChat(true);

        if (btnMinimize) btnMinimize.onclick = (e) => { e.stopPropagation(); closeChat(); };
    if (btnClose)    btnClose.onclick    = (e) => { e.stopPropagation(); closeChat(); };

        if (btnReset) btnReset.onclick = (e) => {
        e.stopPropagation();
        SS.del('pdt_chat_history_v2');
        SS.del('pdt_chat_context');
        if (chatMessages) chatMessages.innerHTML = '';
        chatContext = {};
        _lastCrNum = null;
        _crInfoCache = {};
        _awaitingConfirm = false;
        _welcomeDone = false;
        window._lastCipCR = null;
        if (crInfoTabBadge) crInfoTabBadge.style.display = 'none';
        if (crInfoToggleBtn) crInfoToggleBtn.style.display = 'none';
        if (ccipContent) ccipContent.innerHTML = '<div class="ccip-empty"><div style="font-size:32px;margin-bottom:8px;">&#128269;</div><div>Search a CR number to see full details here.</div></div>';
        setStatus('Ready to help');
        sendWelcome();
    };

    if (restoreBtn) restoreBtn.onclick = () => {
        const lsHist = LS.get('pdt_chat_history_v2_backup', []);
        if (lsHist.length && chatMessages) {
            chatMessages.innerHTML = '';
            lsHist.forEach(m => renderMessage(m.text, m.sender, m.context || {}, m.ui || null, false));
            SS.set('pdt_chat_history_v2', lsHist);
            scrollToBottom();
        }
        if (restoreBanner) restoreBanner.style.display = 'none';
    };
    if (dismissRestoreBtn) dismissRestoreBtn.onclick = () => {
        if (restoreBanner) restoreBanner.style.display = 'none';
        LS.del('pdt_chat_history_v2_backup');
    };

    if (crInfoToggleBtn) crInfoToggleBtn.onclick = () => switchTab('crinfo');
    if (exportBtn)       exportBtn.onclick       = exportChat;
        if (clearHistBtn)    clearHistBtn.onclick     = () => {
        if (confirm('Clear all chat history?')) {
            SS.del('pdt_chat_history_v2');
            LS.del('pdt_chat_history_v2_backup');
            if (chatMessages) chatMessages.innerHTML = '';
            chatContext = {};
            _welcomeDone = false;
            renderHistoryTab();
            sendWelcome();
        }
    };

    if (ccipSearchBtn) ccipSearchBtn.onclick = () => {
        const v = ccipSearchInput ? ccipSearchInput.value.trim() : '';
        if (v) loadCrInfoPanel(v);
    };
    if (ccipSearchInput) ccipSearchInput.addEventListener('keypress', e => {
        if (e.key === 'Enter') { const v = ccipSearchInput.value.trim(); if (v) loadCrInfoPanel(v); }
    });

    // -- Status line -----------------------------------------------------------
    function setStatus(text, color) {
        if (!statusLine) return;
        statusLine.textContent = text;
        statusLine.style.color = color || 'rgba(186,230,253,.75)';
    }

    // -- Scroll helper ---------------------------------------------------------
    function scrollToBottom() {
        if (chatMessages) requestAnimationFrame(() => { chatMessages.scrollTop = chatMessages.scrollHeight; });
    }

    // -- Link / send helper ----------------------------------------------------
    function isOpenableLink(v) {
        const s = String(v || "").trim();
        return /^\/chatbot_table\/[0-9a-fA-F-]{36}$/.test(s) ||
               /^\/view_query_table\/.+$/.test(s) ||
               /^https?:\/\//i.test(s);
    }

            function openOrSend(v) {
        const s = String(v || "").trim();
        if (!s) return;  // never send empty
        if (isOpenableLink(s)) { window.open(s, "_blank", "noopener,noreferrer"); return; }
        sendMessage(s);
    }

        // -- Core sendMessage ------------------------------------------------------
        async function sendMessage(message = null, isProgrammatic = false, extraPayload = null) {
        const typed  = (chatInput && typeof chatInput.value === "string") ? chatInput.value.trim() : "";
        let userMsg  = (message !== null) ? String(message) : typed;
        const hasExtra = extraPayload && Object.keys(extraPayload).length > 0;
        // Never send empty message through sendMessage -- welcome uses sendWelcome()
        if (!userMsg && !hasExtra) return;

                                // Never auto-reset when awaiting a Yes/No confirmation from the bot
        const isConfirmation = ['yes','no','y','n','ok','okay','sure','cancel']
            .includes((userMsg || '').toLowerCase().trim());
        const isFollowUp = _awaitingConfirm || isConfirmation || (chatContext && (
            chatContext.state ||
            chatContext.pending_task_id ||
            chatContext.awaiting_confirmation
        ));
        if (!isFollowUp && userMsg && !isProgrammatic && chatMessages && chatMessages.children.length > 0) {
            // New question -- clear previous conversation
            SS.del('pdt_chat_history_v2');
            SS.del('pdt_chat_context');
            chatMessages.innerHTML = '';
            chatContext = {};
            _lastCrNum = null;
            _crInfoCache = {};
            _awaitingConfirm = false;
            window._lastCipCR = null;
            if (crInfoTabBadge) crInfoTabBadge.style.display = 'none';
            if (crInfoToggleBtn) crInfoToggleBtn.style.display = 'none';
        }

        if (userMsg && !isProgrammatic) renderMessage(userMsg, 'user');
        if (chatInput) chatInput.value = '';

        setStatus('Thinking...', 'rgba(253,224,71,.85)');
        showLoadingIndicator();

                try {
            const rawTarget = (typeof FLASK_CURRENT_TARGET !== 'undefined' && FLASK_CURRENT_TARGET) ? FLASK_CURRENT_TARGET : 'global';
            const target = rawTarget;
            const payload = { message: userMsg || "", context: chatContext, ...(extraPayload || {}) };
            const res  = await fetch(`/chatbot_message/${target}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            const loader = document.getElementById('bot-loading');
            if (loader) loader.remove();
            chatContext = data.context || {};
            SS.set('pdt_chat_context', chatContext);
            renderMessage(data.response, 'bot', chatContext, data.ui || null, true);
            setStatus('Ready to help');
            if (chatContext.pending_task_id) {
                showLoadingIndicator("Report generation in progress...");
                pollReportStatus(chatContext.pending_task_id);
            }
            scrollToBottom();
                } catch (e) {
            const loader = document.getElementById('bot-loading');
            if (loader) loader.remove();
            // Only show error to user for real user-typed messages
            if (!isProgrammatic) {
                renderMessage("Connection error. Please try again.", 'bot');
            }
            setStatus('Error -- try again', 'rgba(252,165,165,.9)');
        }
    }

    // -- Poll report status ----------------------------------------------------
    function pollReportStatus(taskId) {
        if (!chatMessages) return;
        showLoadingIndicator("Checking report status...");
        let loaderTxt = document.getElementById("bot-loading-text");
        const interval = setInterval(async () => {
            try {
                const res  = await fetch(`/check_report_status/${taskId}`);
                const data = await res.json();
                if (loaderTxt && data.progress) loaderTxt.innerText = data.progress;
                if (data.status === 'completed') {
                    clearInterval(interval);
                    const loader = document.getElementById('bot-loading');
                    if (loader) loader.remove();
                    delete chatContext.pending_task_id;
                    SS.set('pdt_chat_context', chatContext);
                    renderMessage("Report ready!", 'bot', data.context || {}, null, true);
                    setStatus('Ready to help');
                } else if (data.status === 'error') {
                    clearInterval(interval);
                    const loader = document.getElementById('bot-loading');
                    if (loader) loader.remove();
                    renderMessage(`Error: ${data.message}`, 'bot');
                    setStatus('Ready to help');
                }
            } catch (e) {
                clearInterval(interval);
                renderMessage("Report status check failed.", 'bot');
            }
        }, 4000);
    }

    // -- Loading indicator -----------------------------------------------------
    function showLoadingIndicator(text = "Thinking...") {
        if (!chatMessages || document.getElementById('bot-loading')) return;
        const loader = document.createElement('div');
        loader.id = "bot-loading";
        loader.className = "message-bubble message-bot";
        loader.innerHTML = `<div id="bot-loading-text" class="text" style="font-style:italic;font-size:11px;color:#64748b;">${text}</div><div class="typing-indicator"><span></span><span></span><span></span></div>`;
        chatMessages.appendChild(loader);
        scrollToBottom();
    }

    // -- Render message --------------------------------------------------------
    function renderMessage(text, sender, context = {}, ui = null, shouldSave = true) {
        if (!chatMessages) return;
        const bubble = document.createElement('div');
        bubble.className = `message-bubble message-${sender}`;
        if (sender === 'bot') {
            let mt = text || "";
            if (context.multi_sheet_url) mt += ` <a href="${context.multi_sheet_url}" target="_blank" class="chat-option-btn view-table-btn">View Report</a>`;
            bubble.innerHTML = `<div class="text">${String(mt).replace(/\n/g,'<br>')}</div>`;
        } else {
            bubble.innerHTML = `<div class="text">${String(text||'').replace(/\n/g,'<br>')}</div>`;
        }

                if (sender === 'bot') {
            const cont = document.createElement('div');
            cont.style.marginTop = "8px";
            let _contHasYesNo = false;

            // 1) Checkboxes
            if (ui && ui.type === "checkboxes" && Array.isArray(ui.options)) {
                const min = Number.isFinite(ui.min) ? ui.min : 0;
                const max = Number.isFinite(ui.max) ? ui.max : ui.options.length;
                const existing = (chatContext.ui_state && chatContext.ui_state[ui.id]) ? chatContext.ui_state[ui.id] : (ui.selected || []);
                const sel = new Set(existing);
                ui.options.forEach(optValue => {
                    const v = String(optValue);
                    const row = document.createElement("div"); row.className = "checkbox-row";
                    const cb = document.createElement("input"); cb.type="checkbox"; cb.value=v; cb.id=`${ui.id}-${v}`; cb.disabled=!shouldSave;
                    if (sel.has(v)) cb.checked = true;
                    cb.onchange = () => {
                        if (cb.checked) { if (sel.size < max) sel.add(v); else { cb.checked=false; renderMessage(`Max ${max} targets.`,'bot',{},null,true); } }
                        else sel.delete(v);
                        chatContext.ui_state = chatContext.ui_state || {};
                        chatContext.ui_state[ui.id] = Array.from(sel);
                        SS.set('pdt_chat_context', chatContext);
                    };
                    const lbl = document.createElement("label"); lbl.htmlFor=cb.id; lbl.textContent=` ${v.toUpperCase()}`;
                    row.appendChild(cb); row.appendChild(lbl); cont.appendChild(row);
                });
                const sub = document.createElement("button"); sub.textContent="Submit"; sub.className="chat-option-btn primary-option-btn";
                if (!shouldSave) { sub.disabled=true; sub.style.opacity="0.6"; sub.style.cursor="default"; }
                sub.onclick = () => {
                    const arr = Array.from(sel);
                    if (arr.length < min || arr.length > max) { renderMessage(`Select between ${min} and ${max} targets.`,'bot',{},null,true); return; }
                    sub.disabled=true; sendMessage(arr.join(','));
                };
                cont.appendChild(sub);
            }

            // 2) Context option buttons
            if (!ui && context.options && Array.isArray(context.options)) {
                context.options.forEach(opt => {
                    const ov = String(opt.value??""), ot = String(opt.text??ov);
                    const b = document.createElement('button'); b.textContent=ot; b.value=ov; b.className="chat-option-btn";
                    if (!shouldSave) { b.disabled=true; b.style.opacity="0.6"; b.style.cursor="default"; }
                    b.onclick = () => { cont.querySelectorAll('button').forEach(x=>{x.disabled=true;x.style.opacity="0.6";x.style.cursor="not-allowed";}); openOrSend(ov); };
                    cont.appendChild(b);
                });
            }

            // 3) UI buttons
            if (ui && ui.type === "buttons" && Array.isArray(ui.options)) {
                ui.options.forEach(opt => {
                    const ot = (opt&&typeof opt==="object") ? String(opt.text??opt.value??"") : String(opt);
                    const ov = (opt&&typeof opt==="object") ? String(opt.value??opt.text??"") : String(opt);
                                        const b = document.createElement("button"); b.textContent=ot; b.className="chat-option-btn";
                    if (!shouldSave) { b.disabled=true; b.style.opacity="0.6"; b.style.cursor="default"; }
                    b.onclick = () => { cont.querySelectorAll("button").forEach(x=>{x.disabled=true;x.style.opacity="0.6";x.style.cursor="not-allowed";}); openOrSend(ov); };
                    cont.appendChild(b);
                    if (['yes','no','y','n'].includes(ot.toLowerCase().trim())) _contHasYesNo = true;
                });
            }

            // 4) Progress poll
            if (ui && ui.type === "progress_poll" && ui.poll_url) {
                const taskId=ui.task_id||'', pollUrl=ui.poll_url, pollMs=ui.poll_interval_ms||3000;
                const card=document.createElement('div'); card.style.cssText='background:#f8fafc;border:1px solid #e2e8f0;border-radius:14px;padding:12px 14px;margin-top:6px;';
                const hdr=document.createElement('div'); hdr.style.cssText='display:flex;align-items:center;gap:8px;margin-bottom:8px;'; hdr.innerHTML='<span style="font-size:16px;">&#9881;</span><span style="font-weight:800;font-size:12px;color:#1e293b;">JiraQuery Report Running</span>'; card.appendChild(hdr);
                const bw=document.createElement('div'); bw.style.cssText='background:#e2e8f0;border-radius:999px;height:7px;overflow:hidden;margin-bottom:7px;';
                const bar=document.createElement('div'); bar.style.cssText='height:100%;border-radius:999px;background:linear-gradient(90deg,#6366f1,#3b82f6,#06b6d4);transition:width .5s ease;width:5%;'; bw.appendChild(bar); card.appendChild(bw);
                const sl=document.createElement('div'); sl.style.cssText='font-size:11px;font-weight:600;color:#475569;margin-bottom:7px;'; sl.textContent='Starting...'; card.appendChild(sl);
                const sr=document.createElement('div'); sr.style.cssText='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:7px;';
                function mkSt(lbl,id){const s=document.createElement('div');s.style.cssText='background:#fff;border:1px solid #e2e8f0;border-radius:7px;padding:3px 8px;text-align:center;min-width:56px;';s.innerHTML=`<div id="${id}" style="font-size:14px;font-weight:900;color:#1e293b;">-</div><div style="font-size:9px;font-weight:700;color:#94a3b8;text-transform:uppercase;">${lbl}</div>`;return s;}
                sr.appendChild(mkSt('Elapsed',`jq-elapsed-${taskId}`)); sr.appendChild(mkSt('Status',`jq-status-${taskId}`)); card.appendChild(sr);
                const aa=document.createElement('div'); aa.id=`jq-action-${taskId}`; aa.style.display='none'; card.appendChild(aa); cont.appendChild(card);
                const spct={'Starting':5,'Step 1':20,'Launching':20,'Step 2':60,'Parsing':60,'Processing':75,'Step 3':95,'ready':100,'completed':100};
                function gp(pr,st){if(st==='completed'||st==='error')return 100;for(const[k,v]of Object.entries(spct)){if((pr||'').includes(k))return v;}return 30;}
                function fe(s){return s<60?s+'s':Math.floor(s/60)+'m '+(s%60)+'s';}
                let pt=null;
                function dp(){fetch(pollUrl).then(r=>r.json()).catch(()=>({status:'error',progress:'Poll failed',elapsed:0})).then(d=>{const st=d.status||'processing',pr=d.progress||'Working...',el=d.elapsed||0;bar.style.width=gp(pr,st)+'%';sl.textContent=pr;const ee=document.getElementById(`jq-elapsed-${taskId}`),se=document.getElementById(`jq-status-${taskId}`);if(ee)ee.textContent=fe(el);if(se)se.textContent=st==='completed'?'\u2705 Done':st==='error'?'\u274C Error':'\u23F3 Running';if(st==='completed'){clearInterval(pt);bar.style.background='#22c55e';sl.innerHTML='<span style="color:#16a34a;font-weight:800;">&#10003; Report ready!</span>';const u=(d.result||{}).multi_sheet_url;if(u){aa.style.display='block';aa.innerHTML=`<a href="${u}" target="_blank" style="display:inline-flex;align-items:center;gap:5px;background:linear-gradient(135deg,#4f46e5,#6366f1);color:#fff;border-radius:999px;padding:7px 16px;font-size:11px;font-weight:800;text-decoration:none;margin-top:4px;">&#128196; Open Report</a>`;}}else if(st==='error'){clearInterval(pt);bar.style.background='#ef4444';sl.innerHTML=`<span style="color:#dc2626;font-weight:700;">&#10060; ${d.message||'Report failed'}</span>`;}});}
                dp(); pt=setInterval(dp,pollMs);
            }

                        if (cont.children.length > 0) bubble.appendChild(cont);

            // Track Yes/No state and CR Insight trigger -- inside bot block where cont is in scope
            if (shouldSave) {
                _awaitingConfirm = _contHasYesNo;
                if (!_contHasYesNo) triggerCRInsightFromText(text || '', bubble);
            }
        }

        chatMessages.appendChild(bubble);
        applyChatLayoutSizing();
        scrollToBottom();

        if (shouldSave) {
            const hist = SS.get('pdt_chat_history_v2', []);
            hist.push({ text, sender, context, ui });
            SS.set('pdt_chat_history_v2', hist);
            LS.set('pdt_chat_history_v2_backup', hist);
        }
    }

    // -- History tab ---------------------------------------------------------------
    function renderHistoryTab() {
        if (!historyList) return;
        const hist = SS.get('pdt_chat_history_v2', []);
        if (!hist.length) {
            historyList.innerHTML = '<div style="text-align:center;padding:32px 16px;color:#94a3b8;font-size:12px;"><div style="font-size:28px;margin-bottom:8px;">&#128203;</div>No conversation history yet.</div>';
            return;
        }
        historyList.innerHTML = '';
        hist.forEach(m => {
            const item = document.createElement('div');
            item.style.cssText = `display:flex;gap:8px;align-items:flex-start;padding:8px 10px;border-radius:10px;background:${m.sender==='user'?'#eff6ff':'#f8fafc'};border:1px solid ${m.sender==='user'?'#bfdbfe':'#e2e8f0'};margin-bottom:4px;`;
            const icon = document.createElement('div'); icon.style.cssText='font-size:13px;flex-shrink:0;margin-top:1px;'; icon.textContent = m.sender==='user'?'\u{1F464}':'\u{1F916}';
            const txt = document.createElement('div'); txt.style.cssText='font-size:11px;color:#374151;line-height:1.5;flex:1;word-break:break-word;';
            const raw = String(m.text||'').replace(/\n/g,'<br>'); txt.innerHTML = raw.length>300 ? raw.substring(0,300)+'\u2026' : raw;
            item.appendChild(icon); item.appendChild(txt); historyList.appendChild(item);
        });
        if (historyList.parentElement) historyList.parentElement.scrollTop = historyList.parentElement.scrollHeight;
    }

    // -- Export -------------------------------------------------------------------
    function exportChat() {
        const hist = SS.get('pdt_chat_history_v2', []);
        if (!hist.length) { alert('No chat history to export.'); return; }
        let txt = 'PDT Buddy Chat Export\n'+'='.repeat(40)+'\n\n';
        hist.forEach(m => { txt += `[${m.sender==='user'?'YOU':'BOT'}]\n${m.text||''}\n\n`; });
        const blob = new Blob([txt],{type:'text/plain'}), url=URL.createObjectURL(blob), a=document.createElement('a');
        a.href=url; a.download=`pdt_buddy_chat_${Date.now()}.txt`; a.click(); URL.revokeObjectURL(url);
    }

    // -- CR Info panel loader --------------------------------------------------
    async function loadCrInfoPanel(crNum) {
        const cr = String(crNum||'').replace(/^CR\s*/i,'').trim();
        if (!cr) return;
        _lastCrNum = cr;
        if (ccipSearchInput) ccipSearchInput.value = cr;
        if (crInfoTabBadge) { crInfoTabBadge.style.display='inline'; crInfoTabBadge.textContent=cr; }
        if (crInfoToggleBtn) crInfoToggleBtn.style.display='flex';
        if (_crInfoCache[cr]) { if (ccipContent) ccipContent.innerHTML=_crInfoCache[cr]; setStatus(`CR ${cr} loaded`,'rgba(134,239,172,.9)'); return; }
        if (ccipContent) ccipContent.innerHTML=`<div style="text-align:center;padding:40px 16px;"><div class="ccip-spinner"></div><div style="font-size:11px;color:#94a3b8;margin-top:10px;">Loading CR ${cr} data...</div></div>`;
        setStatus(`Loading CR ${cr}...`,'rgba(253,224,71,.85)');
        try {
            const tgt=(typeof FLASK_CURRENT_TARGET!=='undefined'&&FLASK_CURRENT_TARGET&&FLASK_CURRENT_TARGET!=='global')?FLASK_CURRENT_TARGET:'';
            const url=`/api/cr_info_summary?cr=${encodeURIComponent(cr)}`+(tgt?`&target=${encodeURIComponent(tgt)}`:'');
            const res=await fetch(url,{cache:'no-store'}), data=await res.json();
            if (data.error) { renderCrInfoError(cr,data.error); return; }
            const html=buildCrInfoHTML(cr,data);
            _crInfoCache[cr]=html;
            if (ccipContent) ccipContent.innerHTML=html;
            setStatus(`CR ${cr} loaded`,'rgba(134,239,172,.9)');
        } catch(e) { renderCrInfoError(cr,'Failed to load CR data. Please try again.'); setStatus('Ready to help'); }
    }

    function renderCrInfoError(cr, msg) {
        if (!ccipContent) return;
        const isPdtNotFound = msg && (msg.toLowerCase().includes('not found') || msg.toLowerCase().includes('pdt'));
        const displayMsg = isPdtNotFound
            ? 'CR not found in PDT available BUs data.'
            : (msg || 'CR not found.');
        ccipContent.innerHTML=`<div class="ccip-card" style="--ccip-accent:linear-gradient(90deg,#ef4444,#f97316);background:linear-gradient(135deg,#fee2e2,#fecaca);border-color:#fca5a5;"><div style="font-size:12px;font-weight:800;color:#b91c1c;margin-bottom:4px;"><i class="fas fa-exclamation-circle"></i> Not Found</div><div style="font-size:11px;color:#7f1d1d;">${displayMsg}</div><div style="margin-top:6px;font-size:10px;color:#991b1b;"><b>Tip:</b> Try without "CR" prefix e.g. "4483004"</div></div>`;
        setStatus('Ready to help');
    }

    function buildCrInfoHTML(cr, d) {
        function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
        function fd(v){if(!v||v==='None'||v==='null')return '\u2014';try{const dt=new Date(v);return isNaN(dt.getTime())?String(v):dt.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'});}catch(e){return String(v);}}
        const ci=d.cr_info||{}, sm=d.summary||{}, jiras=d.jiras||[];
        const st=(ci.cr_status||'').toLowerCase();
        let sc='ccip-badge-other';
        if(st.includes('open')||st.includes('undisposed')||st.includes('active'))sc='ccip-badge-open';
        else if(st.includes('closed')||st.includes('fixed')||st.includes('built'))sc='ccip-badge-closed';
        const lc=(sm.linked_crs||[]).length, occ=sm.occurrences||0, dev=sm.devices||0, age=sm.cr_age||'\u2014';
        const lh=(sm.linked_crs||[]).slice(0,8).map(c=>`<span class="ccip-linked">${esc(c)}</span>`).join('');
        const bh=Object.entries(sm.build_counts||{}).slice(0,8).map(([b,cnt])=>`<span class="ccip-build-pill"><i class="fas fa-cube" style="color:#0ea5e9;font-size:8px;"></i>${esc(b)}<span class="ccip-build-count">${cnt}</span></span>`).join('');
        const jr=jiras.slice(0,10).map((r,i)=>{const tk=r.stability_ticket||'',sn=r.serial_no||'NA',tm=r.test_team||'';return `<tr><td style="color:#94a3b8;font-size:10px;padding:4px 6px;">${i+1}</td><td style="padding:4px 6px;">${tk?`<a href="https://jira-dc2.qualcomm.com/jira/browse/${esc(tk)}" target="_blank" style="color:#1d4ed8;font-weight:700;font-size:10px;text-decoration:none;">${esc(tk)}</a>`:'<span style="color:#94a3b8;font-size:10px;">NA</span>'}</td><td style="font-size:10px;color:#64748b;padding:4px 6px;">${esc(sn)}</td><td style="font-size:10px;padding:4px 6px;">${esc(tm)}</td></tr>`;}).join('');
        const tgt=(typeof FLASK_CURRENT_TARGET!=='undefined'&&FLASK_CURRENT_TARGET&&FLASK_CURRENT_TARGET!=='global')?FLASK_CURRENT_TARGET:'';
        const cpUrl=tgt?`/dashboard/${tgt}/cr-info?cr=${encodeURIComponent(cr)}`:''
        return `
        <div class="ccip-stat-row">
            <div class="ccip-stat"><div class="ccip-stat-val">${esc(String(age))}</div><div class="ccip-stat-lbl">Age (d)</div></div>
            <div class="ccip-stat"><div class="ccip-stat-val" style="color:#dc2626;">${occ}</div><div class="ccip-stat-lbl">Occurrences</div></div>
            <div class="ccip-stat"><div class="ccip-stat-val" style="color:#16a34a;">${dev}</div><div class="ccip-stat-lbl">Devices</div></div>
        </div>
        <div class="ccip-card" style="--ccip-accent:linear-gradient(90deg,#6366f1,#0ea5e9);">
            <div class="ccip-card-title"><i class="fas fa-id-card" style="color:#6366f1;"></i> Identity</div>
            <div class="ccip-row"><span class="ccip-row-label">CR</span><span class="ccip-row-val" style="font-weight:900;font-size:13px;color:#0f172a;">${esc(cr)}</span></div>
            ${ci.mapped_cr?`<div class="ccip-row"><span class="ccip-row-label">Mapped CR</span><span class="ccip-row-val" style="color:#2563eb;font-weight:800;">${esc(ci.mapped_cr)}</span></div>`:''}
            ${ci.cr_title?`<div class="ccip-row"><span class="ccip-row-label">Title</span><span class="ccip-row-val" style="font-style:italic;">${esc((ci.cr_title||'').substring(0,120))}</span></div>`:''}
            ${ci.cr_status?`<div class="ccip-row"><span class="ccip-row-label">Status</span><span class="ccip-row-val"><span class="ccip-badge ${sc}">${esc(ci.cr_status)}</span></span></div>`:''}
            ${ci.cr_area?`<div class="ccip-row"><span class="ccip-row-label">Area</span><span class="ccip-row-val"><span class="ccip-badge ccip-badge-area">${esc(ci.cr_area)}</span></span></div>`:''}
            ${ci.cr_functionality?`<div class="ccip-row"><span class="ccip-row-label">Functionality</span><span class="ccip-row-val"><span class="ccip-badge ccip-badge-func">${esc(ci.cr_functionality)}</span></span></div>`:''}
            ${ci.cr_subsystem?`<div class="ccip-row"><span class="ccip-row-label">Subsystem</span><span class="ccip-row-val">${esc(ci.cr_subsystem)}</span></div>`:''}
            ${ci.cr_date?`<div class="ccip-row"><span class="ccip-row-label">CR Date</span><span class="ccip-row-val">${esc(fd(ci.cr_date))}</span></div>`:''}
        </div>
        ${lc?`<div class="ccip-card" style="--ccip-accent:linear-gradient(90deg,#f43f5e,#f97316);"><div class="ccip-card-title"><i class="fas fa-link" style="color:#f43f5e;"></i> Linked CRs <span style="background:#fee2e2;color:#b91c1c;border-radius:999px;padding:1px 7px;font-size:9px;margin-left:4px;">${lc}</span></div><div>${lh}</div></div>`:''}
        ${bh?`<div class="ccip-card" style="--ccip-accent:linear-gradient(90deg,#0ea5e9,#06b6d4);"><div class="ccip-card-title"><i class="fas fa-cube" style="color:#0ea5e9;"></i> Build Details</div><div>${bh}</div></div>`:''}
        ${jr?`<div class="ccip-card" style="--ccip-accent:linear-gradient(90deg,#7c3aed,#a855f7);"><div class="ccip-card-title"><i class="fas fa-ticket-alt" style="color:#7c3aed;"></i> JIRAs <span style="background:#ede9fe;color:#6d28d9;border-radius:999px;padding:1px 7px;font-size:9px;margin-left:4px;">${jiras.length}</span></div><div style="overflow-x:auto;border-radius:8px;border:1px solid #ede9fe;"><table style="width:100%;border-collapse:collapse;"><thead><tr style="background:linear-gradient(90deg,#7c3aed,#a855f7);"><th style="padding:5px 6px;color:#fff;font-weight:700;text-align:left;font-size:10px;">#</th><th style="padding:5px 6px;color:#fff;font-weight:700;text-align:left;font-size:10px;">Ticket</th><th style="padding:5px 6px;color:#fff;font-weight:700;text-align:left;font-size:10px;">Serial</th><th style="padding:5px 6px;color:#fff;font-weight:700;text-align:left;font-size:10px;">Team</th></tr></thead><tbody>${jr}</tbody></table></div>${jiras.length>10?`<div style="font-size:10px;color:#94a3b8;margin-top:6px;text-align:center;">Showing 10 of ${jiras.length} JIRAs</div>`:''}</div>`:''}
        ${cpUrl?`<div style="text-align:center;margin-top:4px;margin-bottom:8px;"><a href="${cpUrl}" target="_blank" class="ccip-open-full-btn"><i class="fas fa-external-link-alt"></i> Open Full CR Info Page</a></div>`:''}
        `;
    }

            // -- CR detection --------------------------------------------------------------
        function triggerCRInsightFromText(text, chatNode) {
        // Do not trigger on confirmation-question messages
        if (_awaitingConfirm) return;
        const pats = [/\/cr\/([0-9]{5,})/i, /\bCR[\s\/]*([0-9]{5,})\b/i];
        for (const p of pats) {
            const m = (text || '').match(p);
            if (m) {
                const crNum = m[1];
                if (crInfoTabBadge) { crInfoTabBadge.style.display='inline'; crInfoTabBadge.textContent=crNum; }
                if (crInfoToggleBtn) crInfoToggleBtn.style.display='flex';
                setStatus(`CR ${crNum} detected`,'rgba(134,239,172,.9)');
                loadCrInfoPanel(crNum);
                // Only open the CR Insight panel when the CR number actually changes
                if (window.cipLoadCR && crNum !== window._lastCipCR) {
                    window._lastCipCR = crNum;
                    setTimeout(() => window.cipLoadCR(crNum, null, chatNode || null), 300);
                }
                break;
            }
        }
    }

    // -- Input listeners -------------------------------------------------------
    if (sendBtn) sendBtn.onclick = () => sendMessage();
    if (chatInput) chatInput.onkeypress = (e) => { if (e.key === 'Enter') sendMessage(); };

    // -- Global exposure -------------------------------------------------------
    window.pdtSendMessage           = sendMessage;
    window._chatbotTriggerCRInsight = triggerCRInsightFromText;
    window.chatbotLoadCrInfo        = loadCrInfoPanel;
    window.chatbotAskAI = function(msg) {
        if (!msg) return;
        openChat(false);
        switchTab('chat');
        sendMessage(msg, true);
    };

});
