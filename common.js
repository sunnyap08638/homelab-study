const status = document.getElementById("status");

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

function studyLink(deck, practice = false) {
  const query = new URLSearchParams();
  if (deck) query.set("deck", deck);
  if (practice) query.set("mode", "practice");
  return query.size ? "/study?" + query : "/study";
}
