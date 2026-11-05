const params = new URLSearchParams(location.search);
const deck = params.get("deck") || "";
const practice = params.get("mode") === "practice";
const stage = document.getElementById("review-stage");
const cardPanel = document.getElementById("review-card");
const face = document.getElementById("card-face");
const progress = document.getElementById("session-progress");
const title = document.getElementById("session-title");
const modeSwitch = document.getElementById("session-switch");
const reveal = document.getElementById("show-answer");
const hide = document.getElementById("show-question");
const again = document.getElementById("again");
const good = document.getElementById("good");
const next = document.getElementById("next-card");
const retry = document.getElementById("retry-review");
let queue = [];
let after = 0;
let completed = 0;
let total = 0;
let pending = false;

function cardSide(card, side) {
  const element = node("div");
  element.className = card.html ? "card-side rich-card" : "card-side";
  // The importer strips untrusted markup. Generated cards remain plain text.
  if (card.html) element.innerHTML = card[side];
  else element.textContent = card[side];
  return element;
}

function showFace(answer, moveFocus = true) {
  const card = queue[0];
  if (!card) return;
  face.hidden = false;
  face.textContent = (answer ? "Answer" : "Question") + (deck ? "" : " · " + card.name);
  cardPanel.replaceChildren(cardSide(card, answer ? "back" : "front"));
  if (answer && card.evidence) cardPanel.append(node("blockquote", card.evidence));
  stage.scrollTop = 0;
  reveal.hidden = answer;
  hide.hidden = !answer;
  next.hidden = !answer || !practice;
  again.hidden = good.hidden = !answer || practice;
  if (moveFocus) (answer ? stage : reveal).focus({ preventScroll: true });
}

function showMessage(message) {
  face.hidden = reveal.hidden = hide.hidden = again.hidden = good.hidden = next.hidden = true;
  cardPanel.replaceChildren(node("p", message));
}

function prefetchQuestion(card) {
  if (!card?.html) return;
  const template = node("template");
  template.innerHTML = card.front;
  const image = template.content.querySelector("img");
  if (image) new Image().src = image.getAttribute("src");
}

async function loadCard(moveFocus = false) {
  retry.hidden = true;
  if (!queue.length) {
    showMessage("Loading cards…");
    const query = new URLSearchParams({ batch: "1", deck });
    if (practice) {
      query.set("practice", "1");
      query.set("after", after);
    }
    queue = await api("/api/review?" + query);
  }
  const card = queue[0];
  if (!card) {
    progress.textContent = completed ? completed + (practice ? " browsed" : " reviewed") : "";
    showMessage(practice ? "End of this deck." : "You’re caught up.");
    if (moveFocus) stage.focus({ preventScroll: true });
    return;
  }
  progress.textContent = practice
    ? "Card " + (completed + 1).toLocaleString() + (total ? " of " + total.toLocaleString() : "")
    : completed.toLocaleString() + " reviewed this session";
  showFace(false, moveFocus);
  prefetchQuestion(queue[1]);
}

async function loadOrRetry(moveFocus) {
  try {
    await loadCard(moveFocus);
  } catch (error) {
    showMessage("Couldn’t load cards.");
    status.textContent = error.message;
    retry.hidden = false;
    if (moveFocus) retry.focus({ preventScroll: true });
  }
}

async function advance(rating) {
  if (pending || !queue.length) return;
  pending = true;
  status.textContent = "";
  const card = queue[0];
  for (const button of [hide, again, good, next]) button.disabled = true;
  try {
    if (!practice) await api("/api/cards/" + card.id + "/review", { rating, reviews: card.reviews });
    after = card.id;
    completed++;
    queue.shift();
    await loadOrRetry(true);
  } catch (error) {
    if (error.message.includes("another device")) {
      queue = [];
      await loadOrRetry(true);
    }
    status.textContent = error.message;
  } finally {
    for (const button of [hide, again, good, next]) button.disabled = false;
    pending = false;
  }
}

async function start(moveFocus = false) {
  status.textContent = "";
  if (deck && modeSwitch.hidden) {
    try {
      const decks = await api("/api/decks");
      const selected = decks.find(item => String(item.id) === deck);
      if (!selected) {
        retry.hidden = true;
        showMessage("This deck is unavailable. Choose another from Decks.");
        return;
      }
      title.textContent = selected.name;
      total = selected.count;
    } catch (error) {
      showMessage("Couldn’t load the deck.");
      status.textContent = error.message;
      retry.hidden = false;
      return;
    }
  }
  modeSwitch.hidden = false;
  await loadOrRetry(moveFocus);
}

document.getElementById("session-mode").textContent = practice ? "Browse cards" : "Due review";
title.textContent = deck ? "Study session" : "All cards";
modeSwitch.href = studyLink(deck, !practice);
modeSwitch.textContent = practice ? "Review due" : "Browse all";
reveal.addEventListener("click", () => showFace(true));
hide.addEventListener("click", () => showFace(false));
next.addEventListener("click", () => advance());
again.addEventListener("click", () => advance("again"));
good.addEventListener("click", () => advance("good"));
retry.addEventListener("click", async () => {
  retry.disabled = true;
  await start(true);
  retry.disabled = false;
  if (!retry.hidden) retry.focus({ preventScroll: true });
});

document.addEventListener("keydown", event => {
  if (event.repeat || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
  if (event.target.closest("input, textarea, select, audio, [contenteditable]")) return;
  let button;
  if (event.key === " " && !event.target.closest("button, a")) button = reveal;
  if (event.key === "1") button = again;
  if (event.key === "2") button = good;
  if (event.key === "ArrowRight") button = next;
  if (button && !button.hidden && !button.disabled) {
    event.preventDefault();
    button.click();
  }
});

start();
