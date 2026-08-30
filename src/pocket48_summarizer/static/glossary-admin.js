// The official catalog carries over seven hundred members, so the enable and
// disable buttons are only usable if an administrator can find one member by
// name first.
const filterInput = document.getElementById("member-filter");
const memberTable = document.getElementById("member-catalog-table");
const filterCount = document.getElementById("member-filter-count");

let applyFilter = () => {};

if (filterInput && memberTable && filterCount) {
  const rows = Array.from(
    memberTable.querySelectorAll("tbody tr[data-member-search]"),
  );
  const total = Number(filterCount.dataset.memberTotal || rows.length);

  const describe = (shown) => {
    const i18n = window.P48I18n;
    const key = shown === total ? "memberFilterAll" : "memberFilterMatches";
    const params = { shown: String(shown), total: String(total) };
    if (i18n) {
      i18n.setText(filterCount, key, params);
    } else {
      filterCount.textContent = `${shown} / ${total}`;
    }
  };

  applyFilter = () => {
    const query = filterInput.value.trim().toLowerCase();
    let shown = 0;
    rows.forEach((row) => {
      const haystack = (row.dataset.memberSearch || "").toLowerCase();
      const visible = !query || haystack.includes(query);
      row.hidden = !visible;
      if (visible) shown += 1;
    });
    describe(shown);
  };

  filterInput.addEventListener("input", applyFilter);
  document.addEventListener("p48:languagechange", applyFilter);
  applyFilter();
}

// Every switch on this page is a form POST that redirects back, so the browser
// reloads and drops the reader at the very top with the search box cleared.
// Over seven hundred rows that meant re-scrolling and re-typing after every
// single click, which made bulk edits impractical.
const RESTORE_KEY = "p48:glossary:reading-position";
const scrollPanes = () =>
  Array.from(document.querySelectorAll(".admin-table-wrap"));

document.addEventListener(
  "submit",
  () => {
    try {
      sessionStorage.setItem(
        RESTORE_KEY,
        JSON.stringify({
          filter: filterInput ? filterInput.value : "",
          y: window.scrollY,
          panes: scrollPanes().map((pane) => pane.scrollTop),
        }),
      );
    } catch (error) {
      // A private-mode storage refusal only costs the scroll position.
    }
  },
  true,
);

const restoreReadingPosition = () => {
  let saved = null;
  try {
    saved = sessionStorage.getItem(RESTORE_KEY);
    sessionStorage.removeItem(RESTORE_KEY);
  } catch (error) {
    return;
  }
  if (!saved) return;
  let state;
  try {
    state = JSON.parse(saved);
  } catch (error) {
    return;
  }
  if (filterInput && typeof state.filter === "string" && state.filter) {
    filterInput.value = state.filter;
    // Filtering changes the document height, so it has to happen before the
    // window scroll is restored or the offset lands somewhere else.
    applyFilter();
  }
  if (Array.isArray(state.panes)) {
    scrollPanes().forEach((pane, index) => {
      const top = state.panes[index];
      if (typeof top === "number") pane.scrollTop = top;
    });
  }
  if (typeof state.y === "number") window.scrollTo(0, state.y);
};

if ("scrollRestoration" in history) history.scrollRestoration = "manual";
restoreReadingPosition();
