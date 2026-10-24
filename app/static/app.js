// Confirm destructive buttons.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-confirm]");
  if (button && !confirm(button.dataset.confirm)) event.preventDefault();
});

// Show the Save button once a row has been edited; Enter in a field saves.
document.addEventListener("input", (event) => {
  const row = event.target.closest("form.row:not(.add)");
  if (row) row.classList.add("dirty");
});

// Chart tooltip, built from each month's data-tip ("Title|Label value|...").
for (const wrap of document.querySelectorAll(".chart-wrap")) {
  const tip = wrap.querySelector(".tooltip");
  wrap.addEventListener("pointermove", (event) => {
    const hit = event.target.closest(".hit");
    if (!hit) { tip.hidden = true; return; }
    const [title, ...lines] = hit.dataset.tip.split("|");
    tip.replaceChildren();
    const heading = document.createElement("b");
    heading.textContent = title;
    tip.append(heading);
    for (const line of lines) {
      const cut = line.lastIndexOf(" ");
      const row = document.createElement("div");
      const label = document.createElement("span");
      const value = document.createElement("span");
      label.textContent = line.slice(0, cut);
      value.textContent = line.slice(cut + 1);
      row.append(label, value);
      tip.append(row);
    }
    tip.hidden = false;
    const box = wrap.getBoundingClientRect();
    const x = event.clientX - box.left;
    const left = Math.min(x + 14, box.width - tip.offsetWidth - 4);
    tip.style.left = `${Math.max(4, left)}px`;
    tip.style.top = `${Math.max(0, event.clientY - box.top - tip.offsetHeight - 10)}px`;
  });
  wrap.addEventListener("pointerleave", () => { tip.hidden = true; });
}
