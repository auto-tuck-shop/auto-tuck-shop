// WhatsApp Mock App
class WhatsAppMock {
    constructor() {
        this.conversation = null;
        this.messageHistory = [];
        this.triggerIndex = 0;

        this.messagesContainer = document.getElementById('messagesContainer');
        this.messagesEl = document.getElementById('messages');
        this.messageInput = document.getElementById('messageInput');
        this.inputBar = document.querySelector('.input-bar');
        this.sendBtn = document.getElementById('sendBtn');
        this.micBtn = document.getElementById('micBtn');
        this.botNameEl = document.getElementById('botName');
        this.botAvatarEl = document.getElementById('botAvatar');

        this.init();
    }

    async init() {
        await this.loadConversation();
        this.setupEventListeners();
        this.displayInitialMessages();
    }

    async loadConversation() {
        try {
            // Check for scenario parameter in URL
            const urlParams = new URLSearchParams(window.location.search);
            const scenario = urlParams.get('scenario') || 'default';
            const scenarioFile = scenario === 'default' ? 'conversation.json' : `scenario-${scenario}.json`;

            const response = await fetch(scenarioFile);
            this.conversation = await response.json();

            // Update header with bot info
            if (this.conversation.botName) {
                this.botNameEl.textContent = this.conversation.botName;
            }
            if (this.conversation.botAvatar) {
                this.botAvatarEl.textContent = this.conversation.botAvatar;
            }
            if (this.conversation.botAvatarImage) {
                this.botAvatarEl.innerHTML = `<img src="${this.conversation.botAvatarImage}" alt="avatar">`;
            }
        } catch (error) {
            console.error('Failed to load conversation:', error);
            this.conversation = {
                botName: 'Auto Tuck Shop',
                botAvatar: 'A',
                initialMessages: [],
                triggers: []
            };
        }
    }

    setupEventListeners() {
        // Input text change - toggle mic/send button
        this.messageInput.addEventListener('input', () => {
            if (this.messageInput.value.trim()) {
                this.inputBar.classList.add('has-text');
            } else {
                this.inputBar.classList.remove('has-text');
            }
        });

        // Enter key to send
        this.messageInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });

        // Send button click
        this.sendBtn.addEventListener('click', () => {
            this.sendMessage();
        });

        // Mic button click - simulate voice message
        this.micBtn.addEventListener('click', () => {
            this.sendVoiceMessage();
        });
    }

    sendVoiceMessage() {
        // If already recording, stop and send
        if (this.isRecording) {
            this.stopRecording();
            return;
        }

        // Start recording UI
        this.startRecording();
    }

    startRecording() {
        this.isRecording = true;
        this.recordingStartTime = Date.now();

        // Show recording UI
        this.inputBar.classList.add('recording');

        // Generate dots for waveform
        const dots = Array(35).fill(0).map(() =>
            `<span class="waveform-dot"></span>`
        ).join('');

        // Create recording bar (replaces input bar content)
        const recordingUI = document.createElement('div');
        recordingUI.className = 'recording-bar';
        recordingUI.innerHTML = `
            <div class="recording-top-row">
                <span class="recording-timer">0:00</span>
                <div class="recording-waveform">
                    ${dots}
                    <div class="waveform-playhead"></div>
                </div>
            </div>
            <div class="recording-bottom-row">
                <button class="recording-delete" aria-label="Delete">
                    <svg viewBox="0 0 24 24" width="24" height="24">
                        <path fill="currentColor" d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
                    </svg>
                </button>
                <button class="recording-pause" aria-label="Pause">
                    <svg viewBox="0 0 24 24" width="32" height="32">
                        <path fill="currentColor" d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/>
                    </svg>
                </button>
                <button class="recording-send" aria-label="Send">
                    <svg viewBox="0 0 24 24" width="24" height="24">
                        <path fill="currentColor" d="M1.101 21.757L23.8 12.028 1.101 2.3l.011 7.912 13.623 1.816-13.623 1.817-.011 7.912z"/>
                    </svg>
                </button>
            </div>
        `;
        this.inputBar.appendChild(recordingUI);
        this.recordingUI = recordingUI;

        // Set up delete button
        const deleteBtn = recordingUI.querySelector('.recording-delete');
        deleteBtn.addEventListener('click', () => this.cancelRecording());

        // Set up send button
        const sendBtn = recordingUI.querySelector('.recording-send');
        sendBtn.addEventListener('click', () => this.stopRecording());

        // Animate waveform dots
        this.animateWaveform();

        // Start timer
        this.recordingInterval = setInterval(() => {
            const elapsed = Math.floor((Date.now() - this.recordingStartTime) / 1000);
            const mins = Math.floor(elapsed / 60);
            const secs = elapsed % 60;
            const timer = recordingUI.querySelector('.recording-timer');
            if (timer) {
                timer.textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
            }
        }, 1000);
    }

    animateWaveform() {
        if (!this.isRecording || !this.recordingUI) return;

        const dots = this.recordingUI.querySelectorAll('.waveform-dot');
        const playhead = this.recordingUI.querySelector('.waveform-playhead');
        let position = 0;

        const updateWaveform = () => {
            if (!this.isRecording) return;

            // Animate random dots to simulate audio
            dots.forEach((dot, i) => {
                if (i <= position) {
                    const intensity = Math.random();
                    dot.style.opacity = 0.3 + intensity * 0.7;
                    dot.style.transform = `scaleY(${0.5 + intensity * 0.5})`;
                }
            });

            // Move playhead
            if (playhead && position < dots.length) {
                playhead.style.left = `${(position / dots.length) * 100}%`;
            }

            position = Math.min(position + 0.5, dots.length);

            requestAnimationFrame(() => setTimeout(updateWaveform, 100));
        };
        updateWaveform();
    }

    cancelRecording() {
        this.isRecording = false;
        clearInterval(this.recordingInterval);
        this.inputBar.classList.remove('recording');
        if (this.recordingUI) {
            this.recordingUI.remove();
            this.recordingUI = null;
        }
    }

    stopRecording() {
        if (!this.isRecording) return;

        const duration = Math.floor((Date.now() - this.recordingStartTime) / 1000);
        const mins = Math.floor(duration / 60);
        const secs = duration % 60;
        const durationStr = `${mins}:${secs.toString().padStart(2, '0')}`;

        // Clean up recording UI
        this.isRecording = false;
        clearInterval(this.recordingInterval);
        this.inputBar.classList.remove('recording');
        if (this.recordingUI) {
            this.recordingUI.remove();
            this.recordingUI = null;
        }

        // Add voice message from user
        this.addMessage({
            type: 'voice',
            duration: durationStr,
            sent: true
        }, true);

        // Find matching voice trigger
        const seqTriggers = this.conversation.triggers.filter(t => t.matchType === 'sequential');
        const voiceTrigger = seqTriggers[this.triggerIndex];

        if (voiceTrigger && voiceTrigger.responses) {
            this.triggerIndex++;
            this.showTypingThenRespond(voiceTrigger.responses);
        }
    }

    displayInitialMessages() {
        if (!this.conversation.initialMessages) return;

        this.conversation.initialMessages.forEach((msg, index) => {
            setTimeout(() => {
                this.addMessage(msg, index === 0);
                // Set up button handlers for initial messages with buttons
                if (msg.buttons) {
                    this.setupMessageButtons(msg.type);
                }
            }, msg.delay || 0);
        });
    }

    sendMessage() {
        const text = this.messageInput.value.trim();
        if (!text) return;

        // Add user message
        this.addMessage({
            type: 'text',
            text: text,
            sent: true
        }, true);

        // Clear input
        this.messageInput.value = '';
        this.inputBar.classList.remove('has-text');

        // Find matching trigger
        this.processUserMessage(text);
    }

    processUserMessage(text) {
        if (!this.conversation.triggers) return;

        // Find matching trigger (skip sequential triggers - those are for voice/mic)
        const trigger = this.conversation.triggers.find(t => {
            if (t.matchType === 'sequential') {
                // Sequential triggers only fire via mic button, not text
                return false;
            } else if (t.matchType === 'exact') {
                return text.toLowerCase() === t.match.toLowerCase();
            } else if (t.matchType === 'contains') {
                return text.toLowerCase().includes(t.match.toLowerCase());
            } else if (t.matchType === 'regex') {
                return new RegExp(t.match, 'i').test(text);
            }
            // Default: contains
            return text.toLowerCase().includes(t.match.toLowerCase());
        });

        if (trigger && trigger.responses) {
            this.showTypingThenRespond(trigger.responses);
        }
    }

    showTypingThenRespond(responses) {
        // Show typing indicator
        const typingEl = this.createTypingIndicator();
        this.messagesEl.appendChild(typingEl);
        this.scrollToBottom();

        // Calculate total delay for typing
        const typingDelay = responses[0]?.delay || 800;

        setTimeout(() => {
            // Remove typing indicator
            typingEl.remove();

            // Add responses with delays
            let cumulativeDelay = 0;
            responses.forEach((response, index) => {
                setTimeout(() => {
                    this.addMessage(response, index === 0);

                    // Set up button handlers for any message type with buttons
                    if (response.buttons) {
                        this.setupMessageButtons(response.type);
                    }
                }, cumulativeDelay);

                cumulativeDelay += (response.delay || 300);
            });
        }, typingDelay);
    }

    createTypingIndicator() {
        const template = document.getElementById('typingTemplate');
        return template.content.cloneNode(true).querySelector('.message');
    }

    addMessage(msg, hasTail = true) {
        const messageEl = document.createElement('div');
        messageEl.className = `message ${msg.sent ? 'sent' : 'received'}`;
        if (hasTail) messageEl.classList.add('has-tail');
        if (msg.type === 'system') messageEl.classList.add('system');
        if (msg.type === 'confirmation') messageEl.classList.add('confirmation');
        if (msg.type === 'voice') messageEl.classList.add('voice');

        const timestamp = msg.timestamp || this.getCurrentTime();

        switch (msg.type) {
            case 'card':
                messageEl.innerHTML = this.createCardHTML(msg, timestamp);
                break;
            case 'system':
                messageEl.innerHTML = this.createSystemHTML(msg);
                break;
            case 'confirmation':
                messageEl.innerHTML = this.createConfirmationHTML(msg, timestamp);
                break;
            case 'quoted':
                messageEl.innerHTML = this.createQuotedHTML(msg, timestamp);
                break;
            case 'voice':
                messageEl.innerHTML = this.createVoiceHTML(msg, timestamp);
                break;
            case 'summary':
                messageEl.innerHTML = this.createSummaryHTML(msg, timestamp);
                break;
            case 'alert':
                messageEl.innerHTML = this.createAlertHTML(msg, timestamp);
                break;
            default:
                messageEl.innerHTML = this.createTextHTML(msg, timestamp);
        }

        this.messagesEl.appendChild(messageEl);
        this.messageHistory.push(msg);
        this.scrollToBottom();

        return messageEl;
    }

    createTextHTML(msg, timestamp) {
        const readReceipts = msg.sent ? `
            <span class="read-receipts">
                <svg viewBox="0 0 16 11" width="16" height="11">
                    <path fill="currentColor" d="M11.071.653a.457.457 0 00-.304-.102.493.493 0 00-.381.178l-6.19 7.636-2.405-2.272a.463.463 0 00-.336-.146.47.47 0 00-.343.146l-.311.31a.445.445 0 00-.14.337c0 .136.047.25.14.343l2.996 2.996a.724.724 0 00.508.217.778.778 0 00.56-.217l6.817-8.347a.442.442 0 00.127-.326.457.457 0 00-.14-.343l-.298-.31zm3.583 0a.457.457 0 00-.304-.102.493.493 0 00-.381.178L7.778 8.365l-.637-.637-.31.31a.445.445 0 00-.14.337c0 .136.047.25.14.343l.927.927a.724.724 0 00.508.217.778.778 0 00.56-.217l6.817-8.347a.442.442 0 00.127-.326.457.457 0 00-.14-.343l-.298-.31z"/>
                </svg>
            </span>
        ` : '';

        const textContent = this.formatWhatsAppText(msg.text);

        const buttonsHTML = msg.buttons ? `
            <div class="wa-reply-buttons">
                ${msg.buttons.map(btn => `<button class="wa-reply-btn" data-action="${btn.toLowerCase()}">${this.escapeHtml(btn)}</button>`).join('')}
            </div>
        ` : '';

        return `
            <div class="bubble">
                <span class="message-text">${textContent}</span>
                <span class="message-meta">
                    <span class="timestamp">${timestamp}</span>
                    ${readReceipts}
                </span>
            </div>
            ${buttonsHTML}
        `;
    }

    createCardHTML(msg, timestamp) {
        // Build plain text with WhatsApp formatting (like summary/alert)
        let lines = [];

        if (msg.title) {
            lines.push(`*${msg.title}*`);
        }

        if (msg.items) {
            lines.push('');
            msg.items.forEach(item => {
                lines.push(item);
            });
        }

        if (msg.total) {
            lines.push('');
            lines.push(`*Total: ${msg.total}*`);
        }

        const textContent = this.formatWhatsAppText(lines.join('\n'));

        const buttonsHTML = msg.buttons ? `
            <div class="wa-reply-buttons">
                ${msg.buttons.map(btn => `<button class="wa-reply-btn" data-action="${btn.toLowerCase()}">${this.escapeHtml(btn)}</button>`).join('')}
            </div>
        ` : '';

        return `
            <div class="bubble">
                <span class="message-text">${textContent}</span>
                <span class="message-meta">
                    <span class="timestamp">${timestamp}</span>
                </span>
            </div>
            ${buttonsHTML}
        `;
    }

    createSystemHTML(msg) {
        return `
            <div class="bubble">
                <span class="message-text">${this.escapeHtml(msg.text)}</span>
            </div>
        `;
    }

    createConfirmationHTML(msg, timestamp) {
        return `
            <div class="bubble">
                <span class="confirmation-icon">
                    <svg viewBox="0 0 24 24" width="14" height="14">
                        <path fill="currentColor" d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z"/>
                    </svg>
                </span>
                <span class="message-text">${this.escapeHtml(msg.text)}</span>
                <span class="message-meta">
                    <span class="timestamp">${timestamp}</span>
                </span>
            </div>
        `;
    }

    createQuotedHTML(msg, timestamp) {
        const readReceipts = msg.sent ? `
            <span class="read-receipts">
                <svg viewBox="0 0 16 11" width="16" height="11">
                    <path fill="currentColor" d="M11.071.653a.457.457 0 00-.304-.102.493.493 0 00-.381.178l-6.19 7.636-2.405-2.272a.463.463 0 00-.336-.146.47.47 0 00-.343.146l-.311.31a.445.445 0 00-.14.337c0 .136.047.25.14.343l2.996 2.996a.724.724 0 00.508.217.778.778 0 00.56-.217l6.817-8.347a.442.442 0 00.127-.326.457.457 0 00-.14-.343l-.298-.31zm3.583 0a.457.457 0 00-.304-.102.493.493 0 00-.381.178L7.778 8.365l-.637-.637-.31.31a.445.445 0 00-.14.337c0 .136.047.25.14.343l.927.927a.724.724 0 00.508.217.778.778 0 00.56-.217l6.817-8.347a.442.442 0 00.127-.326.457.457 0 00-.14-.343l-.298-.31z"/>
                </svg>
            </span>
        ` : '';

        return `
            <div class="bubble">
                <div class="quoted-reply">
                    <div class="quoted-title">${this.escapeHtml(msg.quotedTitle || '')}</div>
                    <div class="quoted-text">${this.escapeHtml(msg.quotedText || '')}</div>
                </div>
                <span class="message-text">${this.escapeHtml(msg.text)}</span>
                <span class="message-meta">
                    <span class="timestamp">${timestamp}</span>
                    ${readReceipts}
                </span>
            </div>
        `;
    }

    createVoiceHTML(msg, timestamp) {
        const readReceipts = msg.sent ? `
            <span class="read-receipts">
                <svg viewBox="0 0 16 11" width="16" height="11">
                    <path fill="currentColor" d="M11.071.653a.457.457 0 00-.304-.102.493.493 0 00-.381.178l-6.19 7.636-2.405-2.272a.463.463 0 00-.336-.146.47.47 0 00-.343.146l-.311.31a.445.445 0 00-.14.337c0 .136.047.25.14.343l2.996 2.996a.724.724 0 00.508.217.778.778 0 00.56-.217l6.817-8.347a.442.442 0 00.127-.326.457.457 0 00-.14-.343l-.298-.31zm3.583 0a.457.457 0 00-.304-.102.493.493 0 00-.381.178L7.778 8.365l-.637-.637-.31.31a.445.445 0 00-.14.337c0 .136.047.25.14.343l.927.927a.724.724 0 00.508.217.778.778 0 00.56-.217l6.817-8.347a.442.442 0 00.127-.326.457.457 0 00-.14-.343l-.298-.31z"/>
                </svg>
            </span>
        ` : '';

        const duration = msg.duration || '0:05';
        const waveformBars = Array(28).fill(0).map(() =>
            `<div class="waveform-bar" style="height: ${Math.random() * 16 + 4}px"></div>`
        ).join('');

        return `
            <div class="bubble voice-bubble">
                <div class="voice-message">
                    <button class="voice-play-btn">
                        <svg viewBox="0 0 24 24" width="20" height="20">
                            <path fill="currentColor" d="M8 5v14l11-7z"/>
                        </svg>
                    </button>
                    <div class="voice-waveform">${waveformBars}</div>
                    <span class="voice-duration">${duration}</span>
                </div>
                <span class="message-meta">
                    <span class="timestamp">${timestamp}</span>
                    ${readReceipts}
                </span>
            </div>
        `;
    }

    createSummaryHTML(msg, timestamp) {
        // Build plain text with WhatsApp formatting
        let lines = [];

        if (msg.icon && msg.title) {
            lines.push(`${msg.icon} *${msg.title}*`);
        }
        if (msg.subtitle) {
            lines.push(msg.subtitle);
        }

        if (msg.sections) {
            msg.sections.forEach(section => {
                lines.push('');
                lines.push(`*${section.title}*`);
                section.items.forEach(item => {
                    lines.push(item);
                });
            });
        }

        const textContent = this.formatWhatsAppText(lines.join('\n'));

        const buttonsHTML = msg.buttons ? `
            <div class="wa-reply-buttons">
                ${msg.buttons.map(btn => `<button class="wa-reply-btn" data-action="${btn.toLowerCase()}">${this.escapeHtml(btn)}</button>`).join('')}
            </div>
        ` : '';

        return `
            <div class="bubble">
                <span class="message-text">${textContent}</span>
                <span class="message-meta">
                    <span class="timestamp">${timestamp}</span>
                </span>
            </div>
            ${buttonsHTML}
        `;
    }

    createAlertHTML(msg, timestamp) {
        // Build plain text with WhatsApp formatting
        let lines = [];

        if (msg.icon && msg.title) {
            lines.push(`${msg.icon} *${msg.title}*`);
        }
        if (msg.text) {
            lines.push('');
            lines.push(msg.text);
        }
        if (msg.items) {
            lines.push('');
            msg.items.forEach(item => {
                lines.push(item);
            });
        }

        const textContent = this.formatWhatsAppText(lines.join('\n'));

        const buttonsHTML = msg.buttons ? `
            <div class="wa-reply-buttons">
                ${msg.buttons.map(btn => `<button class="wa-reply-btn" data-action="${btn.toLowerCase()}">${this.escapeHtml(btn)}</button>`).join('')}
            </div>
        ` : '';

        return `
            <div class="bubble">
                <span class="message-text">${textContent}</span>
                <span class="message-meta">
                    <span class="timestamp">${timestamp}</span>
                </span>
            </div>
            ${buttonsHTML}
        `;
    }

    formatWhatsAppText(text) {
        // Convert WhatsApp formatting to HTML
        let html = this.escapeHtml(text);
        // Bold: *text*
        html = html.replace(/\*([^*]+)\*/g, '<strong>$1</strong>');
        // Italic: _text_
        html = html.replace(/_([^_]+)_/g, '<em>$1</em>');
        // Line breaks
        html = html.replace(/\n/g, '<br>');
        return html;
    }

    setupMessageButtons(messageType) {
        // All buttons now use wa-reply-btn (cards, summary, alert)
        let btnSelector = '.wa-reply-btn';

        // Find all buttons that haven't been set up yet
        const buttons = this.messagesEl.querySelectorAll(btnSelector);
        buttons.forEach(btn => {
            // Avoid adding multiple listeners
            if (btn.dataset.listenerAdded) return;
            btn.dataset.listenerAdded = 'true';

            btn.addEventListener('click', () => {
                const action = btn.dataset.action;

                // Find button response trigger
                const buttonTrigger = this.conversation.triggers.find(t =>
                    t.matchType === 'button' && t.match.toLowerCase() === action
                );

                if (buttonTrigger && buttonTrigger.responses) {
                    this.showTypingThenRespond(buttonTrigger.responses);
                }
            });
        });
    }

    // Keep old method name for backwards compatibility
    setupCardButtons(cardMsg) {
        this.setupMessageButtons('card');
    }

    getCurrentTime() {
        const now = new Date();
        return now.toLocaleTimeString('en-US', {
            hour: 'numeric',
            minute: '2-digit',
            hour12: true
        }).toLowerCase();
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    scrollToBottom() {
        requestAnimationFrame(() => {
            this.messagesContainer.scrollTop = this.messagesContainer.scrollHeight;
        });
    }
}

// Initialize the app
document.addEventListener('DOMContentLoaded', () => {
    window.app = new WhatsAppMock();
});
