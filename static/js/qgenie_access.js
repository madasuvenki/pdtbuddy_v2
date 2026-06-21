document.addEventListener("DOMContentLoaded", function () {
    const openChatBtn = document.getElementById("openChatBtn");
    const chatWindow = document.getElementById("chatWindow");
    const chatInput = document.getElementById("chatInput");
    const sendBtn = document.getElementById("sendBtn");

    const qgeniePanel = document.getElementById("qgenieConfigPanel");
    const qgenieOverlay = document.getElementById("qgenieConfigOverlay");
    const qgenieCloseBtn = document.getElementById("qgenieCloseBtn");
    const qgenieApiKey = document.getElementById("qgenieApiKey");
    const saveBtn = document.getElementById("saveQGenieConfigBtn");
    const msgBox = document.getElementById("qgenieConfigMessage");
    const rememberCheckbox = document.getElementById("rememberQGenieKey");
    const toggleKeyBtn = document.getElementById("toggleQGenieKey");
    const loginBadge = document.getElementById("qgenieLoginBadge");

        // ── Detect login-popup mode (set by server after TARGET_GROUP login) ──
    const isLoginPopup = qgenieOverlay && qgenieOverlay.dataset.needsPopup === 'true';

        function applyLoginPopupStyle() {
        // Show overlay (panel is inside it — centred automatically)
        if (qgenieOverlay) {
            qgenieOverlay.style.display = 'flex';
        }
        // Show the "one last step" badge, hide close button
        if (loginBadge)     loginBadge.style.display = 'block';
        if (qgenieCloseBtn) qgenieCloseBtn.style.display = 'none';
        // Block page scroll
        document.body.style.overflow = 'hidden';
        setTimeout(() => { if (qgenieApiKey) qgenieApiKey.focus(); }, 120);
    }

        function disableChatbot() {
        if (chatInput) chatInput.disabled = true;
        if (sendBtn) sendBtn.disabled = true;
    }

    function enableChatbot() {
        if (chatInput) chatInput.disabled = false;
        if (sendBtn) sendBtn.disabled = false;
    }

        function showPanel() {
        if (qgenieOverlay) qgenieOverlay.style.display = 'flex';
        disableChatbot();
        setTimeout(() => { if (qgenieApiKey) qgenieApiKey.focus(); }, 100);
    }

    function hidePanel() {
        if (qgenieOverlay) qgenieOverlay.style.display = 'none';
        enableChatbot();
    }

        // ── Auto-open in login-popup mode ──
    if (isLoginPopup) {
        applyLoginPopupStyle();
        // If key already saved in browser — auto-submit silently, skip popup
        const savedKey = getSavedKey();
        if (savedKey) {
                        validateKeyWithBackend(savedKey, false).then(data => {
                if (data.success) {
                    // Key valid — continue to the next post-login step when provided.
                    window.location.href = data.next_url || window.location.href;
                } else {

                    // Saved key invalid — clear it and show popup
                    clearKeyFromBrowser();
                    showPanel();
                }
            }).catch(() => showPanel());
        } else {
            showPanel();
        }
    }

        window.openQGenieConfigPanel = showPanel;
    window.closeQGenieConfigPanel = hidePanel;
    window.saveQGenieKeyFromPanel = saveQGenieKey;

    function setMessage(text, type = "error") {
        msgBox.textContent = text;
        msgBox.style.color = type === "success" ? "#86efac" : "#fca5a5";
    }

    function saveKeyToBrowser(apiKey) {
        localStorage.setItem("qgenie_api_key", apiKey);
        localStorage.setItem("qgenie_remember", "true");
    }

    function clearKeyFromBrowser() {
        localStorage.removeItem("qgenie_api_key");
        localStorage.removeItem("qgenie_remember");
    }

    function getSavedKey() {
        const remember = localStorage.getItem("qgenie_remember");
        if (remember === "true") {
            return localStorage.getItem("qgenie_api_key") || "";
        }
        return "";
    }

    async function validateKeyWithBackend(apiKey, autoCheck = false) {
        const response = await fetch("/api/qgenie/configure", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                api_key: apiKey,
                auto_check: autoCheck
            })
        });

        return await response.json();
    }

    async function handleChatOpen() {
        const savedKey = getSavedKey();

        if (!savedKey) {
            showPanel();
            return;
        }

        setMessage("Checking saved key...", "success");

        try {
            const data = await validateKeyWithBackend(savedKey, true);

            if (data.success) {
                hidePanel();
                if (chatWindow) {
                    chatWindow.classList.add("active");
                }
            } else {
                clearKeyFromBrowser();
                qgenieApiKey.value = "";
                rememberCheckbox.checked = false;
                setMessage("Saved key is invalid. Please enter again.");
                showPanel();
            }
        } catch (error) {
            setMessage("Unable to verify saved key. Please try again.");
            showPanel();
        }
    }

        async function saveQGenieKey() {
        const apiKey = qgenieApiKey.value.trim();

        if (!apiKey) {
            setMessage("Please enter QGenie API key.");
            return false;
        }

        setMessage("Validating key...", "success");

        try {
            const data = await validateKeyWithBackend(apiKey, false);

                        if (data.success) {
                if (rememberCheckbox.checked) {
                    saveKeyToBrowser(apiKey);
                } else {
                    clearKeyFromBrowser();
                }

                setMessage("QGenie configured successfully.", "success");

                                                setTimeout(() => {
                                        if (isLoginPopup) {
                        document.body.style.overflow = '';
                        window.location.href = data.next_url || window.location.href;
                    } else {

                        hidePanel();
                        if (chatWindow) chatWindow.classList.add('active');
                    }
                }, 500);
                return true;
            } else {
                setMessage(data.message || "Invalid QGenie API key.");
                return false;
            }
        } catch (error) {
            setMessage("Something went wrong while validating the key.");
            return false;
        }
    }

    if (toggleKeyBtn) {
        toggleKeyBtn.addEventListener("click", function () {
            const isPassword = qgenieApiKey.type === "password";
            qgenieApiKey.type = isPassword ? "text" : "password";
            toggleKeyBtn.innerHTML = isPassword
                ? '<i class="fas fa-eye-slash"></i>'
                : '<i class="fas fa-eye"></i>';
        });
    }

        if (saveBtn) {
        saveBtn.addEventListener("click", async function () {
            await saveQGenieKey();
        });
    }

        if (qgenieCloseBtn) {
        qgenieCloseBtn.addEventListener("click", function () {
            if (!isLoginPopup) hidePanel();
        });
    }

    if (qgenieOverlay) {
        qgenieOverlay.addEventListener("click", function (e) {
            // In login-popup mode the overlay is not dismissible
            if (!isLoginPopup && e.target === qgenieOverlay) hidePanel();
        });
    }

    if (qgenieApiKey) {
        qgenieApiKey.addEventListener("keypress", function (e) {
            if (e.key === "Enter") {
                saveQGenieKey();
            }
        });
    }

    if (openChatBtn) {
        openChatBtn.addEventListener("click", async function () {
            await handleChatOpen();
        });
    }
});