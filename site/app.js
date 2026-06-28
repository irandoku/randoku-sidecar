// 1. Toast Notification Helper
const toast = document.querySelector('.toast');
function showToast(message) {
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add('is-visible');
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove('is-visible'), 2500);
}

// 2. Scroll-triggered Page Reveals
const revealElements = [...document.querySelectorAll('.reveal')];
if ('IntersectionObserver' in window && revealElements.length) {
  const revealObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('revealed');
        revealObserver.unobserve(entry.target);
      }
    });
  }, {
    root: null,
    rootMargin: '0px 0px -10% 0px',
    threshold: 0.05,
  });

  revealElements.forEach((el) => revealObserver.observe(el));
}

// 3. Tab Glider Alignment & Selection
const installGlider = document.getElementById('tab-glider-install');
const installTabs = [...document.querySelectorAll('.tab-btn-install')];
const installPanels = [...document.querySelectorAll('[data-panel]')];

function updateGlider(activeTab) {
  if (!installGlider || !activeTab) return;
  installGlider.style.left = `${activeTab.offsetLeft}px`;
  installGlider.style.width = `${activeTab.offsetWidth}px`;
}

function activateInstallTab(tabName) {
  const activeTab = installTabs.find((tab) => tab.dataset.tab === tabName);
  if (!activeTab) return;

  installTabs.forEach((tab) => {
    const active = tab === activeTab;
    tab.classList.toggle('is-active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });

  installPanels.forEach((panel) => {
    const active = panel.dataset.panel === tabName;
    panel.classList.toggle('is-active', active);
    panel.hidden = !active;
  });

  updateGlider(activeTab);
}

installTabs.forEach((tab) => {
  tab.addEventListener('click', () => activateInstallTab(tab.dataset.tab));
});

window.addEventListener('resize', () => {
  const activeTab = installTabs.find((tab) => tab.classList.contains('is-active'));
  if (activeTab) updateGlider(activeTab);
});

// Initialize first install tab glider after page load
window.addEventListener('load', () => {
  const activeTab = installTabs.find((tab) => tab.classList.contains('is-active'));
  if (activeTab) {
    // Small timeout to let fonts / layouts calculate offsets correctly
    setTimeout(() => updateGlider(activeTab), 150);
  }
});

// 4. Secure Clipboard Copies
const copyButtons = [...document.querySelectorAll('[data-copy-target]')];
copyButtons.forEach((button) => {
  button.addEventListener('click', async () => {
    const targetId = button.dataset.copyTarget;
    const codeEl = document.getElementById(targetId);
    if (!codeEl) return;

    const codeText = codeEl.textContent.trim();
    if (!codeText) return;

    try {
      await navigator.clipboard.writeText(codeText);
      showToast('Code copied to clipboard');

      const originalText = button.textContent;
      button.textContent = 'Copied!';
      button.style.borderColor = 'var(--accent-teal)';
      button.style.color = 'var(--accent-teal)';

      window.setTimeout(() => {
        button.textContent = originalText;
        button.style.borderColor = '';
        button.style.color = '';
      }, 1500);
    } catch {
      showToast('Copy failed, select manually');
    }
  });
});

// 5. Live Interactive Sandbox Simulator
const btnRunSimulation = document.getElementById('btn-run-simulation');
const chatBox = document.getElementById('chat-box');
const serverLogs = document.getElementById('server-logs');
const toolButtons = [...document.querySelectorAll('.tool-btn')];

// State variables for active tool
let selectedTool = 'read';

// Handle tool selection click
toolButtons.forEach((btn) => {
  btn.addEventListener('click', () => {
    toolButtons.forEach((b) => b.classList.remove('is-active'));
    btn.classList.add('is-active');
    selectedTool = btn.dataset.tool;
  });
});

// Simulate events
if (btnRunSimulation) {
  btnRunSimulation.addEventListener('click', () => {
    btnRunSimulation.disabled = true;
    btnRunSimulation.style.opacity = '0.5';
    
    // Check gate toggles
    const isTerminalEnabled = document.getElementById('gate-terminal').checked;
    const isWriteEnabled = document.getElementById('gate-write').checked;
    const isMemoryEnabled = document.getElementById('gate-memory').checked;
    const isSessionEnabled = document.getElementById('gate-session').checked;

    // Clear previous logs and append connection startup
    chatBox.innerHTML = '';
    serverLogs.innerHTML = `
      <div class="log-line system">[SYSTEM] Server started on 127.0.0.1:4750</div>
      <div class="log-line system">[SYSTEM] Tunnel active: https://randoku-sidecar-tunnel.trycloudflare.com/mcp</div>
    `;

    // A. Add user message to Chat
    let userPromptText = '';
    if (selectedTool === 'read') {
      userPromptText = 'Can you view the README file on my local machine?';
    } else if (selectedTool === 'command') {
      userPromptText = 'Deploy my app by running the build command.';
    } else if (selectedTool === 'write') {
      userPromptText = 'Update config.json and set port to 8080.';
    } else if (selectedTool === 'memory') {
      userPromptText = 'Save that my project is called "Randoku Sidecar" to memory.';
    }

    const userMessageDiv = document.createElement('div');
    userMessageDiv.className = 'chat-bubble user-message';
    userMessageDiv.innerHTML = `<p>${userPromptText}</p>`;
    chatBox.appendChild(userMessageDiv);
    chatBox.scrollTop = chatBox.scrollHeight;

    // B. Add ChatGPT loading indicator
    const botIndicatorDiv = document.createElement('div');
    botIndicatorDiv.className = 'chat-bubble bot-message mcp-indicator-wrapper';
    botIndicatorDiv.innerHTML = `
      <p>Reading local workspace...</p>
      <div class="mcp-indicator">
        <span></span> Calling local sidecar...
      </div>
    `;
    
    setTimeout(() => {
      chatBox.appendChild(botIndicatorDiv);
      chatBox.scrollTop = chatBox.scrollHeight;
    }, 600);

    // C. Server connection log
    setTimeout(() => {
      const connLog = document.createElement('div');
      connLog.className = 'log-line incoming';
      connLog.innerHTML = `[SSE] GET /mcp/stream - Connection established`;
      serverLogs.appendChild(connLog);
      
      const reqLog = document.createElement('div');
      reqLog.className = 'log-line incoming';
      
      if (selectedTool === 'read') {
        reqLog.innerHTML = `[MCP] Call tool: hermes_read_file(path="README.md")`;
      } else if (selectedTool === 'command') {
        reqLog.innerHTML = `[MCP] Call tool: hermes_run_command(command="npm run build")`;
      } else if (selectedTool === 'write') {
        reqLog.innerHTML = `[MCP] Call tool: hermes_write_file(path="config.json", content="{\\n  \\"port\\": 8080\\n}")`;
      } else if (selectedTool === 'memory') {
        reqLog.innerHTML = `[MCP] Call tool: hermes_memory(action="add", content="project name is Randoku Sidecar")`;
      }
      serverLogs.appendChild(reqLog);
      serverLogs.scrollTop = serverLogs.scrollHeight;
    }, 1200);

    // D. Tool Execution & Gate evaluation
    setTimeout(() => {
      const execLog = document.createElement('div');
      const botResponse = document.createElement('div');
      botResponse.className = 'chat-bubble bot-message';
      
      // Remove loading indicator bubble
      botIndicatorDiv.remove();

      if (selectedTool === 'read') {
        execLog.className = 'log-line success';
        execLog.innerHTML = `[FILE] Read successful: 145 lines sent.`;
        serverLogs.appendChild(execLog);
        
        botResponse.innerHTML = `
          <p>I've read the local <code>README.md</code>. Here is the project headline:</p>
          <pre style="margin-top: 8px; font-family: var(--font-mono); font-size: 11px; background: rgba(255,255,255,0.06); padding: 8px; border-radius: 6px;"># randoku-sidecar\n\nrandoku-sidecar is a standalone MCP sidecar for Hermes Agent.</pre>
        `;
        showToast('Local Read executed successfully');
      } 
      
      else if (selectedTool === 'command') {
        if (isTerminalEnabled) {
          execLog.className = 'log-line executing';
          execLog.innerHTML = `[TERMINAL] Executing command in workdir...\n`;
          serverLogs.appendChild(execLog);
          
          setTimeout(() => {
            const termOut = document.createElement('div');
            termOut.className = 'log-line success';
            termOut.innerHTML = `[SHELL] > vite build\n[SHELL] ✓ built in 480ms\n[SYSTEM] process exited with code 0`;
            serverLogs.appendChild(termOut);
            serverLogs.scrollTop = serverLogs.scrollHeight;
          }, 600);

          botResponse.innerHTML = `<p>I have executed the terminal command <code>npm run build</code>. The build was completed successfully and generated the client assets folder.</p>`;
          showToast('Local Command executed successfully');
        } else {
          execLog.className = 'log-line error';
          execLog.innerHTML = `[SECURITY] Gated call rejected: hermes_run_command is disabled.\n[SECURITY] To enable terminal execution, start server with: RANDOKU_ENABLE_TERMINAL=1`;
          serverLogs.appendChild(execLog);
          
          botResponse.innerHTML = `<p><strong>Error: Security Gate Blocked.</strong> The terminal command execution tool is hidden from MCP clients. To allow this action, set the local environment variable <code>RANDOKU_ENABLE_TERMINAL=1</code> on your sidecar server and restart.</p>`;
          showToast('Security gate blocked terminal command');
        }
      } 
      
      else if (selectedTool === 'write') {
        if (isWriteEnabled) {
          execLog.className = 'log-line success';
          execLog.innerHTML = `[FILE] Write successful: config.json updated.`;
          serverLogs.appendChild(execLog);
          
          botResponse.innerHTML = `<p>I've written the updated port configuration to your local <code>config.json</code> file.</p>`;
          showToast('Local File written successfully');
        } else {
          execLog.className = 'log-line error';
          execLog.innerHTML = `[SECURITY] Gated call rejected: hermes_write_file is disabled.\n[SECURITY] To enable writes, start server with: RANDOKU_ENABLE_WRITE=1`;
          serverLogs.appendChild(execLog);
          
          botResponse.innerHTML = `<p><strong>Error: Security Gate Blocked.</strong> Write operations are disabled. Enable write/patch actions by launching the sidecar with the environment variable <code>RANDOKU_ENABLE_WRITE=1</code>.</p>`;
          showToast('Security gate blocked file write');
        }
      } 
      
      else if (selectedTool === 'memory') {
        if (isMemoryEnabled) {
          execLog.className = 'log-line success';
          execLog.innerHTML = `[MEMORY] Action: "add" &bull; target: "memory" &bull; content added successfully.`;
          serverLogs.appendChild(execLog);
          
          botResponse.innerHTML = `<p>I have successfully saved that statement into your Hermes Agent's local long-term memory vault.</p>`;
          showToast('Memory updated successfully');
        } else {
          execLog.className = 'log-line error';
          execLog.innerHTML = `[SECURITY] Gated call rejected: hermes_memory (write) is disabled.\n[SECURITY] To enable memory edits, start server with: RANDOKU_ENABLE_MEMORY_WRITE=1`;
          serverLogs.appendChild(execLog);
          
          botResponse.innerHTML = `<p><strong>Error: Security Gate Blocked.</strong> Memory writes are disabled. Set <code>RANDOKU_ENABLE_MEMORY_WRITE=1</code> to allow saving new memories.</p>`;
          showToast('Security gate blocked memory write');
        }
      }

      chatBox.appendChild(botResponse);
      chatBox.scrollTop = chatBox.scrollHeight;
      serverLogs.scrollTop = serverLogs.scrollHeight;
      
      // Re-enable trigger button
      btnRunSimulation.disabled = false;
      btnRunSimulation.style.opacity = '1';
    }, 2800);
  });
}

// 6. Set current year in Footer
const footerYearEl = document.getElementById('footer-year');
if (footerYearEl) {
  footerYearEl.textContent = String(new Date().getFullYear());
}
