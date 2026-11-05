const select = document.getElementById("notes");
const panel = document.getElementById("selected-note");
const status = document.getElementById("status");
const reviewPanel = document.getElementById("review-card");
const deckSelect = document.getElementById("deck");
const reviewMode = document.getElementById("review-mode");
let reviewQueue = [];
let reviewPosition = 0;
let reviewRequest = 0;
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

async function refreshDecks() {
  const previous = deckSelect.value;
  const decks = await api("/api/decks");
  deckSelect.replaceChildren(new Option("All cards", ""), ...decks.map(deck =>
    new Option(deck.name + " · " + deck.count + " cards", deck.id)
  ));
  deckSelect.value = decks.some(deck => String(deck.id) === previous) ? previous : "";
}

function cardSide(card, side) {
  const element = node("div");
  element.className = "card-side";
  // Only the server's allowlisted Anki rendering is HTML. Note cards are plain text.
  if (card.html) element.innerHTML = card[side];
  else element.textContent = card[side];
  return element;
}

function prefetchCard(card) {
  if (!card?.html) return;
  const template = node("template");
  template.innerHTML = card.front + card.back;
  const images = Array.from(template.content.querySelectorAll("img")).slice(0, 2);
  for (const image of images) {
    const preview = new Image();
    preview.src = image.getAttribute("src");
  }
}

async function loadReview(reset = true) {
  const request = ++reviewRequest;
  const practice = reviewMode.value === "practice";
  if (reset) {
    reviewPosition = 0;
    reviewQueue = [];
  }
  if (!reviewQueue.length) {
    reviewPanel.replaceChildren(node("p", "Loading cards…"));
    const params = new URLSearchParams({ batch: "1", deck: deckSelect.value });
    if (practice) {
      params.set("practice", "1");
      params.set("after", String(reviewPosition));
    }
    const cards = await api("/api/review?" + params);
    if (request !== reviewRequest) return;
    reviewQueue = cards;
  }

  const card = reviewQueue[0];
  if (!card) {
    reviewPanel.replaceChildren(node("p", practice ? "End of this deck." : "You’re caught up."));
    return;
  }
  const nextCard = async () => {
    if (request !== reviewRequest) return;
    reviewPosition = card.id;
    reviewQueue.shift();
    await loadReview(false);
  };
  const answer = node("div");
  const reveal = action("Show answer", () => {
    reveal.remove();
    answer.append(cardSide(card, "back"));
    if (card.evidence) answer.append(node("blockquote", card.evidence));
    if (practice) {
      answer.append(action("Next card", nextCard));
      return;
    }
    const rate = rating => async () => {
      const buttons = answer.querySelectorAll("button");
      for (const button of buttons) button.disabled = true;
      try {
        await api("/api/cards/" + card.id + "/review", { rating, reviews: card.reviews });
        await nextCard();
      } catch (error) {
        if (request === reviewRequest && error.message.includes("another device")) {
          await loadReview();
        }
        throw error;
      } finally {
        for (const button of buttons) button.disabled = false;
      }
    };
    answer.append(action("Again · 10 min", rate("again")), action("Good", rate("good")));
  });
  reviewPanel.replaceChildren(node("p", card.name), cardSide(card, "front"), reveal, answer);
  prefetchCard(reviewQueue[1]);
}

function refreshReview() {
  loadReview().catch(error => { status.textContent = error.message; });
}

document.getElementById("load-review").addEventListener("click", refreshReview);
deckSelect.addEventListener("change", refreshReview);
reviewMode.addEventListener("change", refreshReview);
document.getElementById("import-deck").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const fields = new FormData(form);
  const file = fields.get("file");
  const name = fields.get("name").trim();
  const button = form.querySelector("button");
  button.disabled = true;
  status.textContent = "Uploading and importing deck…";
  try {
    if (!file.size || file.size > 64 * 1024 * 1024) throw new Error("Choose a deck of up to 64 MiB.");
    const query = new URLSearchParams({ file: file.name });
    if (name) query.set("name", name);
    const response = await fetch("/api/decks/import?" + query, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The import failed.");
    form.reset();
    status.textContent = result.imported + " new cards, " + result.existing + " already in this deck, " +
      result.skipped + " skipped, " + result.missingMedia + " unavailable media files.";
    await refreshDecks();
    deckSelect.value = result.id;
    await loadReview();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

document.getElementById("note-file").addEventListener("change", async (event) => {
  const input = event.currentTarget;
  const file = input.files[0];
  if (!file) return;
  try {
    if (file.size > 640000) throw new Error("Choose a smaller text or Markdown file.");
    const body = await file.text();
    if (input.files[0] !== file) return;
    if (Array.from(body).length > 160000) throw new Error("Split this note into smaller files.");
    document.getElementById("name").value = file.name.slice(0, 512);
    document.getElementById("body").value = body;
  } catch (error) {
    status.textContent = error.message;
  }
});

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

Promise.all([refreshNotes(), refreshDecks(), loadReview()]).catch(error => { status.textContent = error.message; });
setInterval(() => refreshNotes().catch(() => {}), 15000);
