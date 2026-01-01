document.addEventListener("DOMContentLoaded", () => {
  flatpickr(".timepicker", {
    enableTime: true,
    noCalendar: true,
    dateFormat: "H:i",   // 24-hour format
    time_24hr: true,
    minuteIncrement: 5,  // nice UX
    allowInput: true,
  });
});

document.addEventListener("DOMContentLoaded", () => {
  const table = document.querySelector("#salaryAdjustAccordion table");
  if (!table) return;

  function csrfToken() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute("content") : "";
  }

  function formatKRW(n) {
    try {
      return Number(n).toLocaleString("en-US") + "₩";
    } catch {
      return n + "₩";
    }
  }

  table.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;

    const row = btn.closest("tr[data-adj-id]");
    if (!row) return;

    const id = row.getAttribute("data-adj-id");
    const btnEdit = row.querySelector(".btn-edit");
    const btnCancel = row.querySelector(".btn-cancel");
    const btnSave = row.querySelector(".btn-save");

    // ENTER edit mode
    if (btn.classList.contains("btn-edit")) {
      // store original cells
      row.dataset.origKind = row.querySelector(".cell-kind").innerHTML;
      row.dataset.origLabel = row.querySelector(".cell-label").innerHTML;
      row.dataset.origAmount = row.querySelector(".cell-amount").innerHTML;

      // replace first 3 cells using template
      const tpl = row.querySelector("template.edit-template");
      const tds = tpl.content.querySelectorAll("td");

      // overwrite the first 3 tds
      row.children[0].replaceWith(tds[0].cloneNode(true));
      row.children[1].replaceWith(tds[1].cloneNode(true));
      row.children[2].replaceWith(tds[2].cloneNode(true));

      btnEdit.classList.add("d-none");
      btnCancel.classList.remove("d-none");
      btnSave.classList.remove("d-none");
      return;
    }

    // CANCEL edit mode
    if (btn.classList.contains("btn-cancel")) {
      // restore original html
      row.children[0].innerHTML = row.dataset.origKind;
      row.children[0].className = "cell-kind";
      row.children[1].innerHTML = row.dataset.origLabel;
      row.children[1].className = "cell-label text-muted";
      row.children[2].innerHTML = row.dataset.origAmount;
      row.children[2].className = "cell-amount text-end fw-semibold";

      btnEdit.classList.remove("d-none");
      btnCancel.classList.add("d-none");
      btnSave.classList.add("d-none");
      return;
    }

    // SAVE edit mode
    if (btn.classList.contains("btn-save")) {
      const kind = row.querySelector(".inp-kind")?.value;
      const label = row.querySelector(".inp-label")?.value?.trim();
      const amount = row.querySelector(".inp-amount")?.value;

      if (!label) return;

      const form = new FormData();
      form.append("kind", kind);
      form.append("label", label);
      form.append("amount", amount);

      // if CSRF is enabled, send token
      const token = csrfToken();
      if (token) form.append("csrf_token", token);

      const res = await fetch(`/salary/adjust/${id}/edit`, {
        method: "POST",
        body: form
      });

      if (!res.ok) return;

      const data = await res.json();
      if (!data.ok) return;

      // rebuild display cells
      const kindBadge = (data.kind === "allowance")
        ? `<span class="badge text-bg-success">Allowance</span>`
        : `<span class="badge text-bg-danger">Deduction</span>`;

      const sign = (data.kind === "allowance") ? "+" : "-";
      const amountHtml = `${sign} ${formatKRW(data.amount)}`;

      // restore display cells with correct classes
      row.children[0].outerHTML = `<td class="cell-kind">${kindBadge}</td>`;
      row.children[1].outerHTML = `<td class="cell-label text-muted">${data.label}</td>`;
      row.children[2].outerHTML = `<td class="cell-amount text-end fw-semibold ${data.kind === "allowance" ? "text-success" : "text-danger"}">${amountHtml}</td>`;

      btnEdit.classList.remove("d-none");
      btnCancel.classList.add("d-none");
      btnSave.classList.add("d-none");

      // IMPORTANT: cards won’t update until refresh.
      // We can auto-refresh after save if you want:
      // location.reload();
    }
  });
});
