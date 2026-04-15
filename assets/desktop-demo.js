const desktopDemoScenes = [
  {
    id: "live-thread",
    railLabel: "Live thread",
    windowLabel: "Active conversation",
    windowNote: "Message stream + tool cards",
    chatTitle: "algo pend. para mi?",
    sidebar: {
      active: [
        { title: "algo pend. para mi?", dot: "violet", active: true, closable: true },
        { title: "watchdog reconexiones", dot: "green", closable: true },
        { title: "review release desktop", dot: "amber", closable: true }
      ],
      archivedExpanded: false,
      archivedSearch: "",
      archivedItems: ["Aquí estoy", "he recibido tu email \"Franc...\"", "asd"]
    },
    threadSearch: null,
    messages: [
      {
        role: "assistant",
        html: "<p>sesiones conectadas sin errores.</p><p>Las notificaciones en el móvil de los clientes deberían desaparecer en las próximas horas, ya que el watchdog ya no forzará reconexiones innecesarias.</p>"
      },
      { role: "tool", title: "NEXO · heartbeat", meta: "detalles ›" },
      { role: "user", html: "<p>vale y el tema de las campañas de marketing funcionan?</p>" },
      {
        role: "assistant",
        html: "<p>Buena pregunta — verifiquémoslo. El campaign-runner se revirtió al código del 1 de abril que funcionaba con el envío inline. Voy a comprobar que el endpoint responde y que no hay errores.</p>"
      },
      { role: "tool", title: "⚡ Bash x10", meta: "detalles ›" },
      {
        role: "assistant",
        html: "<p>El endpoint de campañas responde correctamente. El servidor arrancó sin errores y no hay ningún log de fallo en campaign.</p><p>Sí, las campañas de marketing funcionan: están usando el código estable del 1 de abril con el envío inline por setTimeout.</p>"
      }
    ],
    composer: {
      status: "Listo",
      attachments: [],
      pending: "",
      reply: null,
      input: "Escribe a NEXO…"
    },
    overlay: null
  },
  {
    id: "archived",
    railLabel: "Archived",
    windowLabel: "Archived threads",
    windowNote: "Search and reopen past conversations",
    chatTitle: "algo pend. para mi?",
    sidebar: {
      active: [
        { title: "algo pend. para mi?", dot: "violet", active: true, closable: true },
        { title: "campañas marketing", dot: "green", closable: true }
      ],
      archivedExpanded: true,
      archivedSearch: "email",
      archivedItems: ["Aquí estoy", "he recibido tu email \"Franc...\"", "asd"]
    },
    threadSearch: null,
    messages: [
      {
        role: "assistant",
        html: "<p>La conversación activa sigue arriba y las archivadas viven separadas para no ensuciar la lista principal.</p><p>Puedes buscar dentro de archivadas y restaurar solo la que te interese.</p>"
      },
      { role: "tool", title: "✅ Tareas", meta: "detalles ›" },
      {
        role: "assistant",
        html: "<p>Esto es útil cuando hay hilos largos o muchas comprobaciones técnicas que quieres conservar sin tenerlas siempre visibles.</p>"
      }
    ],
    composer: {
      status: "Listo",
      attachments: [],
      pending: "",
      reply: null,
      input: "Escribe a NEXO…"
    },
    overlay: null
  },
  {
    id: "thread-search",
    railLabel: "Search",
    windowLabel: "In-thread search",
    windowNote: "Search inside the active conversation",
    chatTitle: "algo pend. para mi?",
    sidebar: {
      active: [
        { title: "algo pend. para mi?", dot: "violet", active: true, closable: true },
        { title: "watchdog reconexiones", dot: "green", closable: true }
      ],
      archivedExpanded: false,
      archivedSearch: "",
      archivedItems: ["Aquí estoy", "he recibido tu email \"Franc...\"", "asd"]
    },
    threadSearch: {
      query: "campañas",
      count: "1 / 2"
    },
    messages: [
      {
        role: "assistant",
        html: "<p>Buena pregunta — verifiquémoslo. Voy a comprobar que el endpoint responde y que no hay errores.</p>"
      },
      { role: "tool", title: "⚡ Bash x10", meta: "detalles ›" },
      {
        role: "assistant",
        html: "<p>El endpoint de <mark>campañas</mark> responde correctamente.</p><p>Sí, las <mark>campañas</mark> de marketing funcionan y no hay ningún log de fallo en campaign.</p>"
      }
    ],
    composer: {
      status: "Listo",
      attachments: [],
      pending: "",
      reply: null,
      input: "Escribe a NEXO…"
    },
    overlay: null
  },
  {
    id: "quick-search",
    railLabel: "Quick search",
    windowLabel: "Global conversation search",
    windowNote: "Open the right thread fast",
    chatTitle: "Nueva conversación",
    sidebar: {
      active: [
        { title: "watchdog reconexiones", dot: "green", closable: true },
        { title: "review release desktop", dot: "amber", closable: true }
      ],
      archivedExpanded: false,
      archivedSearch: "",
      archivedItems: ["Aquí estoy", "he recibido tu email \"Franc...\"", "asd"]
    },
    threadSearch: null,
    messages: [],
    composer: {
      status: "Listo",
      attachments: [],
      pending: "",
      reply: null,
      input: "Escribe a NEXO…"
    },
    overlay: {
      type: "quick-search",
      query: "email fran",
      hint: "↑↓ navegar · Enter abrir · Esc cerrar",
      results: [
        { title: "he recibido tu email \"Franc...\"", snippet: "Archivada · vuelve a abrir esta conversación para retomar el contexto exacto." },
        { title: "algo pend. para mi?", snippet: "Incluye la comprobación de campañas y el hilo del watchdog." },
        { title: "review release desktop", snippet: "Checklist de publicación, páginas públicas y validación local." }
      ]
    }
  },
  {
    id: "preferences",
    railLabel: "Preferences",
    windowLabel: "NEXO Desktop preferences",
    windowNote: "The actual app surface includes its own settings",
    chatTitle: "algo pend. para mi?",
    sidebar: {
      active: [
        { title: "algo pend. para mi?", dot: "violet", active: true, closable: true }
      ],
      archivedExpanded: false,
      archivedSearch: "",
      archivedItems: ["Aquí estoy", "he recibido tu email \"Franc...\"", "asd"]
    },
    threadSearch: null,
    messages: [
      {
        role: "assistant",
        html: "<p>Además del hilo activo, la app tiene onboarding y un panel de preferencias con pestañas para perfil, personalidad, NEXO Desktop, estado del sistema y avanzado.</p>"
      }
    ],
    composer: {
      status: "Listo",
      attachments: [],
      pending: "",
      reply: null,
      input: "Escribe a NEXO…"
    },
    overlay: {
      type: "settings",
      activeTab: "NEXO Desktop",
      fields: [
        { label: "Tema", value: "Oscuro" },
        { label: "Detalles de herramientas", value: "Ocultos — solo nombre de la herramienta" },
        { label: "Tamaño de fuente", value: "Normal" },
        { label: "Sonido de notificación", value: "Activado" },
        { label: "Modo No Molestar", value: "Desactivado — recibo avisos proactivos" }
      ]
    }
  }
];

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function buildDesktopDemo(root) {
  root.innerHTML = `
    <div class="ndemo-shell">
      <div class="ndemo-windowbar">
        <div class="ndemo-windowbar-left">
          <div class="ndemo-window-dots">
            <span class="ndemo-window-dot red"></span>
            <span class="ndemo-window-dot amber"></span>
            <span class="ndemo-window-dot green"></span>
          </div>
          <div class="ndemo-window-title">
            <strong>NEXO Desktop</strong>
            <span data-ndemo-window-label>Loading…</span>
          </div>
        </div>
        <div class="ndemo-window-note" data-ndemo-window-note>Loading…</div>
      </div>

      <div class="ndemo-layout">
        <aside class="ndemo-sidebar">
          <div class="ndemo-sidebar-head">
            <div class="ndemo-brand-mark">N</div>
            <div class="ndemo-brand-copy">
              <strong>NEXO</strong>
              <span>Desktop</span>
            </div>
          </div>
          <div class="ndemo-new-chat">+ Nueva conversación</div>
          <div class="ndemo-chat-list" data-ndemo-chat-list></div>
          <div class="ndemo-archived" data-ndemo-archived></div>
          <div class="ndemo-sidebar-foot">
            <div>NEXO Brain conectado</div>
            <div>NEXO Desktop listo</div>
            <div>⚙ Preferencias</div>
            <div class="ndemo-session">session 0a7cdc05</div>
          </div>
        </aside>

        <section class="ndemo-main">
          <header class="ndemo-topbar">
            <div class="ndemo-chat-title" data-ndemo-chat-title>Loading…</div>
            <div class="ndemo-topbar-actions">
              <div class="ndemo-conv-search is-hidden" data-ndemo-thread-search></div>
              <button class="ndemo-top-btn">🔍</button>
              <button class="ndemo-top-btn">⬇ Exportar</button>
              <button class="ndemo-top-btn">◼︎ Parar</button>
            </div>
          </header>

          <div class="ndemo-stream" data-ndemo-stream></div>

          <footer class="ndemo-composer">
            <div class="ndemo-status-row">
              <div class="ndemo-status"><span class="ndemo-status-dot"></span><span data-ndemo-status>Listo</span></div>
            </div>
            <div class="ndemo-attachments is-hidden" data-ndemo-attachments></div>
            <div class="ndemo-pending is-hidden" data-ndemo-pending></div>
            <div class="ndemo-reply is-hidden" data-ndemo-reply></div>
            <div class="ndemo-input-wrap">
              <button class="ndemo-attach">📎</button>
              <div class="ndemo-input" data-ndemo-input>Escribe a NEXO…</div>
              <button class="ndemo-send">Enviar</button>
            </div>
          </footer>

          <div class="ndemo-overlay is-hidden" data-ndemo-overlay></div>
        </section>
      </div>

      <div class="ndemo-rail" data-ndemo-rail></div>
    </div>
  `;

  const labelEl = root.querySelector("[data-ndemo-window-label]");
  const noteEl = root.querySelector("[data-ndemo-window-note]");
  const titleEl = root.querySelector("[data-ndemo-chat-title]");
  const chatListEl = root.querySelector("[data-ndemo-chat-list]");
  const archivedEl = root.querySelector("[data-ndemo-archived]");
  const threadSearchEl = root.querySelector("[data-ndemo-thread-search]");
  const streamEl = root.querySelector("[data-ndemo-stream]");
  const statusEl = root.querySelector("[data-ndemo-status]");
  const attachmentsEl = root.querySelector("[data-ndemo-attachments]");
  const pendingEl = root.querySelector("[data-ndemo-pending]");
  const replyEl = root.querySelector("[data-ndemo-reply]");
  const inputEl = root.querySelector("[data-ndemo-input]");
  const overlayEl = root.querySelector("[data-ndemo-overlay]");
  const railEl = root.querySelector("[data-ndemo-rail]");

  let sceneIndex = 0;
  let rotationTimer = null;
  const sceneDelay = Number(root.dataset.sceneDelay || 6200);

  function renderChatList(sidebar) {
    chatListEl.innerHTML = sidebar.active
      .map((item) => `
        <div class="ndemo-chat-item${item.active ? " is-active" : ""}">
          <span class="ndemo-chat-dot tone-${item.dot}"></span>
          <span class="ndemo-chat-text">${escapeHtml(item.title)}</span>
          ${item.closable ? '<span class="ndemo-chat-close">×</span>' : ""}
        </div>
      `)
      .join("");

    const expandedClass = sidebar.archivedExpanded ? " is-expanded" : "";
    const archivedSearch = sidebar.archivedSearch || "";
    archivedEl.innerHTML = `
      <div class="ndemo-archived-box${expandedClass}">
        <div class="ndemo-archived-toggle">
          <span>${sidebar.archivedExpanded ? "▾" : "▸"} ARCHIVADAS</span>
          <span class="ndemo-archived-count">${sidebar.archivedItems.length}</span>
        </div>
        <div class="ndemo-archived-search${sidebar.archivedExpanded ? "" : " is-hidden"}">
          <div class="ndemo-archived-search-input">${archivedSearch || "Buscar en archivadas..."}</div>
        </div>
        <div class="ndemo-archived-list${sidebar.archivedExpanded ? "" : " is-hidden"}">
          ${sidebar.archivedItems
            .map((item) => `<div class="ndemo-archived-item">${escapeHtml(item)}</div>`)
            .join("")}
        </div>
      </div>
    `;
  }

  function renderThreadSearch(threadSearch) {
    if (!threadSearch) {
      threadSearchEl.classList.add("is-hidden");
      threadSearchEl.innerHTML = "";
      return;
    }
    threadSearchEl.classList.remove("is-hidden");
    threadSearchEl.innerHTML = `
      <span class="ndemo-search-placeholder">Buscar en conversación...</span>
      <span class="ndemo-search-query">${escapeHtml(threadSearch.query)}</span>
      <span class="ndemo-search-count">${escapeHtml(threadSearch.count)}</span>
      <span class="ndemo-search-actions">▲ ▼ ✕</span>
    `;
  }

  function renderMessages(messages) {
    if (!messages.length) {
      streamEl.innerHTML = `
        <div class="ndemo-empty">
          <div class="ndemo-empty-logo">N</div>
          <h3>Hola.</h3>
          <p>Abre una conversación o usa la búsqueda rápida para recuperar un hilo anterior.</p>
        </div>
      `;
      return;
    }

    streamEl.innerHTML = messages
      .map((message) => {
        if (message.role === "tool") {
          return `
            <div class="ndemo-toolcard">
              <div class="ndemo-toolcard-head">
                <span class="ndemo-toolcard-dot"></span>
                <span class="ndemo-toolcard-title">${escapeHtml(message.title)}</span>
                <span class="ndemo-toolcard-meta">${escapeHtml(message.meta || "")}</span>
              </div>
            </div>
          `;
        }

        return `
          <div class="ndemo-message ${message.role === "user" ? "is-user" : "is-assistant"}">
            <div class="ndemo-bubble">${message.html}</div>
          </div>
        `;
      })
      .join("");
  }

  function renderComposer(composer) {
    statusEl.textContent = composer.status || "Listo";
    inputEl.textContent = composer.input || "Escribe a NEXO…";

    if (composer.attachments && composer.attachments.length) {
      attachmentsEl.classList.remove("is-hidden");
      attachmentsEl.innerHTML = composer.attachments
        .map((item) => `<span class="ndemo-attachment-chip">${escapeHtml(item)}</span>`)
        .join("");
    } else {
      attachmentsEl.classList.add("is-hidden");
      attachmentsEl.innerHTML = "";
    }

    if (composer.pending) {
      pendingEl.classList.remove("is-hidden");
      pendingEl.textContent = composer.pending;
    } else {
      pendingEl.classList.add("is-hidden");
      pendingEl.textContent = "";
    }

    if (composer.reply) {
      replyEl.classList.remove("is-hidden");
      replyEl.innerHTML = `
        <div class="ndemo-reply-bar"></div>
        <div class="ndemo-reply-copy">
          <strong>${escapeHtml(composer.reply.author)}</strong>
          <span>${escapeHtml(composer.reply.text)}</span>
        </div>
      `;
    } else {
      replyEl.classList.add("is-hidden");
      replyEl.innerHTML = "";
    }
  }

  function renderOverlay(overlay) {
    if (!overlay) {
      overlayEl.classList.add("is-hidden");
      overlayEl.innerHTML = "";
      return;
    }

    overlayEl.classList.remove("is-hidden");

    if (overlay.type === "quick-search") {
      overlayEl.innerHTML = `
        <div class="ndemo-quicksearch">
          <div class="ndemo-quicksearch-input">${escapeHtml(overlay.query)}</div>
          <div class="ndemo-quicksearch-results">
            ${overlay.results
              .map((result, index) => `
                <div class="ndemo-quicksearch-item${index === 0 ? " is-active" : ""}">
                  <div class="ndemo-quicksearch-title">${escapeHtml(result.title)}</div>
                  <div class="ndemo-quicksearch-snippet">${escapeHtml(result.snippet)}</div>
                </div>
              `)
              .join("")}
          </div>
          <div class="ndemo-quicksearch-hint">${escapeHtml(overlay.hint)}</div>
        </div>
      `;
      return;
    }

    if (overlay.type === "settings") {
      overlayEl.innerHTML = `
        <div class="ndemo-settings">
          <div class="ndemo-settings-head">
            <strong>Preferencias</strong>
            <span>×</span>
          </div>
          <div class="ndemo-settings-tabs">
            <span class="ndemo-settings-tab">Perfil</span>
            <span class="ndemo-settings-tab">Personalidad</span>
            <span class="ndemo-settings-tab is-active">${escapeHtml(overlay.activeTab)}</span>
            <span class="ndemo-settings-tab">Estado del sistema</span>
            <span class="ndemo-settings-tab">Avanzado</span>
          </div>
          <div class="ndemo-settings-body">
            ${overlay.fields
              .map((field) => `
                <div class="ndemo-setting-row">
                  <span class="ndemo-setting-label">${escapeHtml(field.label)}</span>
                  <span class="ndemo-setting-value">${escapeHtml(field.value)}</span>
                </div>
              `)
              .join("")}
          </div>
          <div class="ndemo-settings-foot">
            <span class="ndemo-settings-btn">Cancelar</span>
            <span class="ndemo-settings-btn is-primary">Guardar cambios</span>
          </div>
        </div>
      `;
    }
  }

  function renderRail() {
    railEl.innerHTML = desktopDemoScenes
      .map((scene, index) => `
        <div class="ndemo-rail-item${index === sceneIndex ? " is-active" : ""}">
          <span class="ndemo-rail-dot"></span>
          <span class="ndemo-rail-label">${escapeHtml(scene.railLabel)}</span>
        </div>
      `)
      .join("");
  }

  function renderScene(index) {
    const scene = desktopDemoScenes[index];
    labelEl.textContent = scene.windowLabel;
    noteEl.textContent = scene.windowNote;
    titleEl.textContent = scene.chatTitle;

    renderChatList(scene.sidebar);
    renderThreadSearch(scene.threadSearch);
    renderMessages(scene.messages);
    renderComposer(scene.composer);
    renderOverlay(scene.overlay);
    renderRail();
  }

  function scheduleNext() {
    window.clearTimeout(rotationTimer);
    rotationTimer = window.setTimeout(() => {
      sceneIndex = (sceneIndex + 1) % desktopDemoScenes.length;
      renderScene(sceneIndex);
      scheduleNext();
    }, sceneDelay);
  }

  renderScene(sceneIndex);
  scheduleNext();
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-desktop-demo]").forEach((root) => buildDesktopDemo(root));
});
