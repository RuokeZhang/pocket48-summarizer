// The official catalog carries over seven hundred members, so the enable and
// disable buttons are only usable if an administrator can find one member by
// name first.
const filterInput = document.getElementById("member-filter");
const memberTable = document.getElementById("member-catalog-table");
const filterCount = document.getElementById("member-filter-count");

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

  const applyFilter = () => {
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
