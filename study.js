const select = document.getElementById("notes");
const panel = document.getElementById("selected-note");
const status = document.getElementById("status");
const reviewPanel = document.getElementById("review-card");
const sourceNames = { obsidian: "Obsidian", goodnotes: "Goodnotes", manual: "Pasted" };
let openNoteRequest = 0;

async function api(path, data) {
  const options = data === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data)
  };
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "The request failed.");
  return result;
}

function node(tag, content) {
  const element = document.createElement(tag);
  if (content !== undefined) element.textContent = content;
  return element;
}

function action(label, run) {
  const button = node("button", label);
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    status.textContent = "";
    try {
      await run();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

async function refreshNotes() {
  const notes = await api("/api/notes");
  const previous = select.value;
  const options = notes.map(({ id, source, name }) => new Option(sourceNames[source] + " / " + name, id));
  select.replaceChildren(new Option("Choose a note", ""), ...options);
  if (options.some(option => option.value === previous)) select.value = previous;
  return notes;
}

function showDraft(draft) {
  const container = node("article");
  const front = node("textarea");
  const back = node("textarea");
  front.value = draft.front;
  back.value = draft.back;
  front.rows = 2;
  back.rows = 3;
  front.maxLength = 500;
  back.maxLength = 2000;
  front.setAttribute("aria-label", "Card question");
  back.setAttribute("aria-label", "Card answer");
  const submit = async (decision) => {
    for (const button of container.querySelectorAll("button")) button.disabled = true;
    try {
      await api("/api/cards/" + draft.id + "/draft", {
        action: decision, front: front.value, back: back.value
      });
      container.remove();
      if (decision === "approve") await loadReview();
    } finally {
      for (const button of container.querySelectorAll("button")) button.disabled = false;
    }
  };
  container.append(front, back, node("blockquote", draft.evidence),
    action("Approve", () => submit("approve")), action("Discard", () => submit("discard")));
  return container;
}

async function openNote(id, offset = 0) {
  if (!id) return;
  const request = ++openNoteRequest;
  const note = await api("/api/notes/" + id);
  if (request !== openNoteRequest) return;
  if (!note.body) {
    panel.replaceChildren(node("h3", note.name), node("p", "This note is empty."));
    return;
  }
  const codePoints = Array.from(note.body);
  const sections = node("select");
  sections.setAttribute("aria-label", "Note section");
  for (let start = 0; start < codePoints.length; start += note.sectionSize) {
    sections.add(new Option("Section " + (sections.length + 1), start));
  }
  sections.value = offset < codePoints.length ? offset : 0;
  const excerpt = node("pre");
  const showSection = () => {
    const start = Number(sections.value);
    excerpt.textContent = codePoints.slice(start, start + note.sectionSize).join("");
  };
  sections.addEventListener("change", showSection);
  showSection();

  panel.replaceChildren(
    node("h3", note.name),
    sections,
    excerpt,
    action("Generate draft cards", async () => {
      const section = Number(sections.value);
      status.textContent = "Generating cards. If the GPU worker is asleep, it may take a few minutes.";
      try {
        const result = await api("/api/notes/" + id + "/generate", { section, revision: note.revision });
        if (request === openNoteRequest) await openNote(id, section);
        status.textContent = result.inserted + " new draft cards.";
      } catch (error) {
        status.textContent = error.message;
      }
    }),
    node("h3", "Drafts"),
    ...note.drafts.map(draft => showDraft(draft))
  );
  if (!note.drafts.length) panel.append(node("p", "No drafts for this note."));
}

async function loadReview() {
  const card = await api("/api/review");
  if (!card) {
    reviewPanel.replaceChildren(node("p", "You’re caught up."));
    return;
  }
  const answer = node("div");
  const reveal = action("Show answer", () => {
    reveal.remove();
    const rate = rating => async () => {
      await api("/api/cards/" + card.id + "/review", { rating, reviews: card.reviews });
      await loadReview();
    };
    answer.append(node("p", card.back), node("blockquote", card.evidence),
      action("Again · 10 min", rate("again")), action("Good", rate("good")));
  });
  reviewPanel.replaceChildren(node("p", card.name), node("h3", card.front), reveal, answer);
}

document.getElementById("load-review").addEventListener("click", () =>
  loadReview().catch(error => { status.textContent = error.message; }));
document.getElementById("open-note").addEventListener("click", () =>
  openNote(select.value).catch(error => { status.textContent = error.message; }));
document.getElementById("import-note").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  try {
    await api("/api/notes", { source: "manual", name: data.get("name"), body: data.get("body") });
    const notes = await refreshNotes();
    const note = notes.find(item => item.source === "manual" && item.name === data.get("name").trim());
    select.value = note.id;
    await openNote(note.id);
    form.reset();
    status.textContent = "Note imported.";
  } catch (error) {
    status.textContent = error.message;
  }
});

Promise.all([refreshNotes(), loadReview()]).catch(error => { status.textContent = error.message; });
setInterval(() => refreshNotes().catch(() => {}), 15000);
