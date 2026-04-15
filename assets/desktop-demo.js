const desktopDemoScenes = [
  {
    id: "support",
    eyebrow: "Support triage",
    kicker: "Priority inbox + memory-aware reply",
    headline: "Escalate the right customer without losing the full history.",
    status: "2 urgent threads · 5m average first response",
    search: "refund / shipping / VIP customer",
    composeLabel: "Suggested response",
    draft:
      "Hi Marta, I have already marked your case as priority and left a follow-up task for logistics. I will confirm the refund status before 10:30.",
    conversations: [
      { name: "Marta Ruiz", preview: "Still waiting on the refund update...", meta: "Urgent", tone: "hot", active: true },
      { name: "Leo Hart", preview: "Can you confirm the replacement ETA?", meta: "Open", tone: "warm" },
      { name: "Celine Park", preview: "Need invoice + tracking in one email", meta: "Ready", tone: "calm" },
      { name: "Noah Silva", preview: "Escalation already assigned to finance", meta: "Watching", tone: "cool" }
    ],
    messages: [
      { role: "signal", title: "NEXO · live signal", body: "VIP customer + refund ticket older than 36h + negative sentiment trend." },
      { role: "user", body: "Can you answer her now and leave a task if logistics has to confirm anything first?" },
      { role: "assistant", body: "Yes. I would reply immediately, keep the tone reassuring, and create a logistics follow-up so the team confirms the refund status before 10:30." }
    ],
    snapshot: {
      company: "Northbay Clinics",
      owner: "Marta Ruiz",
      sentiment: "4/10 · tense",
      memory: "Last successful delivery: 19 days ago · prefers direct updates when there is a delay."
    },
    tasks: [
      "Reply before 10:30",
      "Check refund batch with logistics",
      "Confirm resolution in the evening"
    ],
    proof: "Every action stays tied to client memory, tags, and next steps inside one operator view."
  },
  {
    id: "sales",
    eyebrow: "Sales follow-up",
    kicker: "Lead qualification + contextual outreach",
    headline: "Move from notes and prompts to a real revenue workspace.",
    status: "7 active opportunities · 3 ready for demo",
    search: "lead score / last meeting / objections",
    composeLabel: "Suggested outreach",
    draft:
      "Hi Luca, following our call, I have prepared a short walkthrough focused on campaign coordination and client memory. Would Thursday at 16:00 work for a private demo?",
    conversations: [
      { name: "Luca Bianchi", preview: "Can we see how this fits our sales ops?", meta: "Lead", tone: "hot", active: true },
      { name: "Sonia Miller", preview: "Interested, but timing is not ideal yet", meta: "Nurture", tone: "warm" },
      { name: "Daria Cole", preview: "Wants internal review before another call", meta: "Review", tone: "cool" },
      { name: "Ben Howard", preview: "Asked for a one-page summary", meta: "Ready", tone: "calm" }
    ],
    messages: [
      { role: "signal", title: "NEXO · sales brief", body: "Lead score up 18% after the last meeting. Main objection: handoff quality across the team." },
      { role: "user", body: "Can you draft a follow-up that pushes toward the demo without sounding generic?" },
      { role: "assistant", body: "Yes. I would anchor it in the handoff problem, mention the private walkthrough, and keep the ask to one concrete next slot." }
    ],
    snapshot: {
      company: "Altura Media",
      owner: "Luca Bianchi",
      sentiment: "7/10 · interested",
      memory: "Cares about operator consistency, sales coordination, and clearer ownership after meetings."
    },
    tasks: [
      "Send follow-up before end of day",
      "Attach tailored one-page summary",
      "Prepare Friday walkthrough notes"
    ],
    proof: "Desktop turns fragmented lead context into one place for outreach, follow-up, and memory continuity."
  },
  {
    id: "catalog",
    eyebrow: "Catalog recommendation",
    kicker: "Customer context + AI-assisted recommendation",
    headline: "Suggest the next best action while the conversation is still open.",
    status: "14 active product requests · 92% response SLA",
    search: "profile / orders / preference pattern",
    composeLabel: "Suggested recommendation",
    draft:
      "For a two-year-old indoor cat, I would start with the sterilized adult line and keep a mixed dry + wet routine. I can also leave a repeat reminder for next month if you want.",
    conversations: [
      { name: "Sara Garcia", preview: "What do you recommend for a 2-year-old cat?", meta: "Live", tone: "hot", active: true },
      { name: "Pablo Martin", preview: "My cat is not eating well lately", meta: "Review", tone: "warm" },
      { name: "Laura Ruiz", preview: "Thanks, I already placed the order", meta: "Won", tone: "calm" },
      { name: "Ana Mora", preview: "Waiting for recommendation before buying", meta: "Ready", tone: "cool" }
    ],
    messages: [
      { role: "signal", title: "NEXO · client brief", body: "Customer bought premium litter before. Prefers practical answers and short product explanations." },
      { role: "user", body: "Can you answer and recommend the most appropriate line without making it too technical?" },
      { role: "assistant", body: "Yes. I would keep it concise, recommend the sterilized adult line, and offer a simple mixed routine with a reminder for replenishment." }
    ],
    snapshot: {
      company: "PetCare Direct",
      owner: "Sara Garcia",
      sentiment: "8/10 · receptive",
      memory: "Previous orders show preference for premium care and quick practical advice."
    },
    tasks: [
      "Send recommendation now",
      "Mark as likely repeat buyer",
      "Offer replenishment reminder"
    ],
    proof: "Desktop can combine customer memory, conversation state, and product guidance in one live operator surface."
  },
  {
    id: "handoff",
    eyebrow: "Client handoff",
    kicker: "Continuity between operators",
    headline: "Keep the relationship intact even when the owner changes.",
    status: "3 open handoffs · 0 missing context",
    search: "handoff / owner / risk / next step",
    composeLabel: "Suggested handoff note",
    draft:
      "Client now moves to Elena for the next phase. Key point: they value fast follow-up and dislike repeating context, so keep updates short and linked to the agreed milestones.",
    conversations: [
      { name: "Orion Labs", preview: "Who is taking this account from here?", meta: "Handoff", tone: "hot", active: true },
      { name: "Marta Ruiz", preview: "Refund confirmed, waiting final closure", meta: "Done", tone: "calm" },
      { name: "Luca Bianchi", preview: "Demo moved to Friday 16:00", meta: "Next", tone: "warm" },
      { name: "Sara Garcia", preview: "Asked for refill reminder next month", meta: "Saved", tone: "cool" }
    ],
    messages: [
      { role: "signal", title: "NEXO · handoff pulse", body: "Owner reassigned. No unresolved blockers. Client is sensitive to repeated questions." },
      { role: "user", body: "Write the handoff note and leave the next owner with the essentials only." },
      { role: "assistant", body: "I would summarize the milestones, preserve the client preferences, and make the next owner explicit so the transition feels seamless." }
    ],
    snapshot: {
      company: "Orion Labs",
      owner: "Elena Torres",
      sentiment: "6/10 · watch",
      memory: "Client reacts well to proactive updates and poorly to internal coordination leaks."
    },
    tasks: [
      "Assign next owner now",
      "Share milestone summary",
      "Set first check-in for tomorrow"
    ],
    proof: "The value is not just drafting text. It is preserving continuity, ownership, and memory across the team."
  }
];

function renderDesktopDemo(root) {
  const titleEl = root.querySelector("[data-demo-title]");
  const kickerEl = root.querySelector("[data-demo-kicker]");
  const headlineEl = root.querySelector("[data-demo-headline]");
  const statusEl = root.querySelector("[data-demo-status]");
  const searchEl = root.querySelector("[data-demo-search]");
  const composeLabelEl = root.querySelector("[data-demo-compose-label]");
  const draftEl = root.querySelector("[data-demo-draft]");
  const conversationsEl = root.querySelector("[data-demo-conversations]");
  const messagesEl = root.querySelector("[data-demo-messages]");
  const companyEl = root.querySelector("[data-demo-company]");
  const ownerEl = root.querySelector("[data-demo-owner]");
  const sentimentEl = root.querySelector("[data-demo-sentiment]");
  const memoryEl = root.querySelector("[data-demo-memory]");
  const tasksEl = root.querySelector("[data-demo-tasks]");
  const proofEl = root.querySelector("[data-demo-proof]");
  const railEl = root.querySelector("[data-demo-rail]");

  let sceneIndex = 0;
  let sceneTimer = null;
  let typingTimer = null;

  const pauseDelay = Number(root.dataset.sceneDelay || 5600);

  function typeDraft(text) {
    if (!draftEl) return;
    window.clearInterval(typingTimer);
    draftEl.textContent = "";
    let cursor = 0;
    typingTimer = window.setInterval(() => {
      cursor += 1;
      draftEl.textContent = text.slice(0, cursor);
      if (cursor >= text.length) {
        window.clearInterval(typingTimer);
      }
    }, 18);
  }

  function renderConversations(conversations) {
    if (!conversationsEl) return;
    conversationsEl.innerHTML = conversations
      .map((item) => {
        const initials = item.name
          .split(" ")
          .map((part) => part.charAt(0))
          .join("")
          .slice(0, 2)
          .toUpperCase();
        return `
          <div class="desktop-conversation-item${item.active ? " is-active" : ""}">
            <div class="desktop-conversation-avatar tone-${item.tone}">${initials}</div>
            <div class="desktop-conversation-copy">
              <div class="desktop-conversation-name">${item.name}</div>
              <div class="desktop-conversation-preview">${item.preview}</div>
            </div>
            <div class="desktop-conversation-meta">${item.meta}</div>
          </div>
        `;
      })
      .join("");
  }

  function renderMessages(messages) {
    if (!messagesEl) return;
    messagesEl.innerHTML = messages
      .map((message) => {
        if (message.role === "signal") {
          return `
            <div class="desktop-message desktop-message-signal">
              <div class="desktop-message-label">${message.title}</div>
              <p>${message.body}</p>
            </div>
          `;
        }

        return `
          <div class="desktop-message desktop-message-${message.role}">
            <p>${message.body}</p>
          </div>
        `;
      })
      .join("");
  }

  function renderTasks(tasks) {
    if (!tasksEl) return;
    tasksEl.innerHTML = tasks
      .map((task) => `<li>${task}</li>`)
      .join("");
  }

  function renderRail() {
    if (!railEl) return;
    railEl.innerHTML = desktopDemoScenes
      .map((scene, index) => {
        const active = index === sceneIndex ? " is-active" : "";
        return `
          <div class="desktop-rail-item${active}">
            <span class="desktop-rail-dot"></span>
            <span class="desktop-rail-label">${scene.eyebrow}</span>
            <span class="desktop-rail-progress"></span>
          </div>
        `;
      })
      .join("");
  }

  function renderScene(index) {
    const scene = desktopDemoScenes[index];
    root.dataset.scene = scene.id;

    if (titleEl) titleEl.textContent = scene.eyebrow;
    if (kickerEl) kickerEl.textContent = scene.kicker;
    if (headlineEl) headlineEl.textContent = scene.headline;
    if (statusEl) statusEl.textContent = scene.status;
    if (searchEl) searchEl.textContent = scene.search;
    if (composeLabelEl) composeLabelEl.textContent = scene.composeLabel;
    if (companyEl) companyEl.textContent = scene.snapshot.company;
    if (ownerEl) ownerEl.textContent = scene.snapshot.owner;
    if (sentimentEl) sentimentEl.textContent = scene.snapshot.sentiment;
    if (memoryEl) memoryEl.textContent = scene.snapshot.memory;
    if (proofEl) proofEl.textContent = scene.proof;

    renderConversations(scene.conversations);
    renderMessages(scene.messages);
    renderTasks(scene.tasks);
    renderRail();
    typeDraft(scene.draft);
  }

  function scheduleNext() {
    window.clearTimeout(sceneTimer);
    sceneTimer = window.setTimeout(() => {
      sceneIndex = (sceneIndex + 1) % desktopDemoScenes.length;
      renderScene(sceneIndex);
      scheduleNext();
    }, pauseDelay);
  }

  function restart() {
    renderScene(sceneIndex);
    scheduleNext();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      window.clearTimeout(sceneTimer);
      window.clearInterval(typingTimer);
      return;
    }

    restart();
  });

  renderScene(sceneIndex);
  scheduleNext();
}

document.querySelectorAll("[data-desktop-demo]").forEach(renderDesktopDemo);
