function legacyCopy(text) {
  const input = document.createElement("textarea");
  input.value = text;
  input.setAttribute("readonly", "");
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  const copied = document.execCommand("copy");
  input.remove();
  if (!copied) throw new Error("Copy failed");
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy-target]");
  if (!button) return;
  const target = document.getElementById(button.dataset.copyTarget);
  if (!target) return;
  const text = target.textContent.trim();
  const name = button.dataset.copyName || "Value";
  const label = button.querySelector(".copy-label");
  const feedback = button.parentElement.querySelector(".copy-feedback");
  if (button.dataset.copyBusy) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      legacyCopy(text);
    }
    button.dataset.copyBusy = "1";
    button.classList.add("is-copied");
    if (label) {
      button.dataset.copyLabel = label.textContent;
      label.textContent = "Copied";
    }
    if (feedback) feedback.textContent = `${name} copied to clipboard.`;
    window.setTimeout(() => {
      button.classList.remove("is-copied");
      if (label && button.dataset.copyLabel) label.textContent = button.dataset.copyLabel;
      if (feedback) feedback.textContent = "";
      delete button.dataset.copyBusy;
    }, 1600);
  } catch (_error) {
    if (feedback) feedback.textContent = "Could not copy automatically. Select the value and copy it manually.";
  }
});

/* Row menus behave like menus: one open at a time, dismissed by click-away or
   Escape. An open popover overlaps the rows beneath it — including destructive
   actions — so the dismissing click is swallowed instead of falling through. */
document.addEventListener("pointerdown", (event) => {
  const open = document.querySelector("details.menu[open]");
  if (!open || open.contains(event.target)) return;
  open.removeAttribute("open");

  const swallow = (dismissed) => {
    dismissed.stopPropagation();
    dismissed.preventDefault();
  };
  document.addEventListener("click", swallow, { capture: true, once: true });
  window.setTimeout(() => document.removeEventListener("click", swallow, true), 400);
}, true);

document.addEventListener("toggle", (event) => {
  const menu = event.target;
  if (!menu.matches || !menu.matches("details.menu[open]")) return;
  document.querySelectorAll("details.menu[open]").forEach((other) => {
    if (other !== menu) other.removeAttribute("open");
  });
}, true);

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  const open = document.querySelector("details.menu[open]");
  if (!open) return;
  open.removeAttribute("open");
  const summary = open.querySelector("summary");
  if (summary) summary.focus();
});

/* Keep the page list current without a full-page refresh. The server remains
   the source of truth and renders the replacement fragment. One-time secrets
   already visible in a page row are carried into the fresh fragment. */
const liveRegion = document.getElementById("dashboard-live-region");
if (liveRegion) {
  let refreshInFlight = false;

  const applyPageFilters = () => {
    const selected = liveRegion.querySelector(".seg-input:checked")?.id || "filter-all";
    const state = selected.replace("filter-", "");
    const query = (liveRegion.querySelector("[data-page-search]")?.value || "").trim().toLowerCase();
    const machine = liveRegion.querySelector("[data-machine-filter]")?.value || "";
    let visible = 0;
    liveRegion.querySelectorAll(".page-row").forEach((row) => {
      const stateMatches = state === "all" || row.dataset.state.split(" ").includes(state);
      const searchMatches = !query || row.dataset.search.includes(query);
      const machineMatches = !machine || row.dataset.machine === machine;
      row.hidden = !(stateMatches && searchMatches && machineMatches);
      if (!row.hidden) visible += 1;
    });
    const empty = liveRegion.querySelector(".filter-empty");
    if (empty) empty.classList.toggle("is-visible", visible === 0);
  };

  liveRegion.addEventListener("input", (event) => {
    if (event.target.matches("[data-page-search]")) applyPageFilters();
  });
  liveRegion.addEventListener("change", (event) => {
    if (event.target.matches(".seg-input, [data-machine-filter]")) applyPageFilters();
  });

  const setLiveStatus = (online) => {
    const status = document.querySelector("[data-live-status]");
    if (!status) return;
    status.classList.toggle("is-offline", !online);
    const label = status.querySelector("span");
    if (label) label.textContent = online ? "Live updates" : "Reconnecting";
  };

  const refreshDashboard = async () => {
    if (refreshInFlight) return;
    refreshInFlight = true;
    try {
      const response = await fetch(liveRegion.dataset.liveUrl, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { "X-ShowMe-Live": "1" },
      });
      if (!response.ok || response.redirected) throw new Error("Dashboard refresh failed");
      const documentText = await response.text();
      const incomingDocument = new DOMParser().parseFromString(documentText, "text/html");
      const incoming = incomingDocument.getElementById("dashboard-live-region");
      if (!incoming) throw new Error("Dashboard fragment missing");
      const currentStyle = document.querySelector('[data-showme-asset="style"]');
      const incomingStyle = incomingDocument.querySelector('[data-showme-asset="style"]');
      const currentScript = document.querySelector('[data-showme-asset="script"]');
      const incomingScript = incomingDocument.querySelector('[data-showme-asset="script"]');
      if (
        (currentStyle && incomingStyle && currentStyle.getAttribute("href") !== incomingStyle.getAttribute("href")) ||
        (currentScript && incomingScript && currentScript.getAttribute("src") !== incomingScript.getAttribute("src"))
      ) {
        window.location.reload();
        return;
      }
      setLiveStatus(true);
      if (incoming.dataset.dashboardVersion === liveRegion.dataset.dashboardVersion) return;

      const selectedFilter = liveRegion.querySelector(".seg-input:checked")?.id;
      const searchValue = liveRegion.querySelector("[data-page-search]")?.value || "";
      const machineValue = liveRegion.querySelector("[data-machine-filter]")?.value || "";
      liveRegion.querySelectorAll(".page-row[data-page-id]").forEach((row) => {
        const secret = row.querySelector(".secret");
        if (!secret) return;
        const destination = Array.from(incoming.querySelectorAll(".page-row[data-page-id]"))
          .find((candidate) => candidate.dataset.pageId === row.dataset.pageId);
        if (!destination) return;
        const staleSecret = destination.querySelector(".secret");
        if (staleSecret) staleSecret.remove();
        destination.append(secret.cloneNode(true));
      });

      if (selectedFilter) {
        const filter = incoming.querySelector(`#${selectedFilter}`);
        if (filter) filter.checked = true;
      }
      const incomingSearch = incoming.querySelector("[data-page-search]");
      if (incomingSearch) incomingSearch.value = searchValue;
      const incomingMachine = incoming.querySelector("[data-machine-filter]");
      if (incomingMachine && Array.from(incomingMachine.options).some((option) => option.value === machineValue)) {
        incomingMachine.value = machineValue;
      }
      liveRegion.replaceChildren(...incoming.childNodes);
      liveRegion.dataset.dashboardVersion = incoming.dataset.dashboardVersion;
      applyPageFilters();
    } catch (_error) {
      setLiveStatus(false);
    } finally {
      refreshInFlight = false;
    }
  };

  refreshDashboard();
  applyPageFilters();
  window.setInterval(refreshDashboard, 2500);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshDashboard();
  });
}
