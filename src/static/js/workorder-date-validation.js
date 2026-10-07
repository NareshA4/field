(() => {
  const start = document.getElementById("id_planned_start");
  const end = document.getElementById("id_planned_end");
  const due = document.getElementById("id_due_at");
  const hours = document.getElementById("id_estimated_hours");
  const form = start?.form;
  if (!start || !end || !due || !hours || !form?.matches("[data-workorder-date-validation]")) return;

  const rules = [
    {field: end, error: document.getElementById("planned-end-rule-error"), message: "Planned end must be later than planned start."},
    {field: due, error: document.getElementById("due-at-rule-error"), message: "Due date and time must be later than planned end."},
  ];

  function validateDates() {
    for (const rule of rules) {
      rule.field.setCustomValidity("");
      if (rule.error) {
        rule.error.hidden = true;
        rule.error.textContent = "";
      }
      const compareTo = rule.field === end ? start : end;
      if (!compareTo.value || !rule.field.value || rule.field.value > compareTo.value) continue;
      rule.field.setCustomValidity(rule.message);
      if (rule.error) {
        rule.error.textContent = rule.message;
        rule.error.hidden = false;
      }
    }

    const hoursError = document.getElementById("estimated-hours-rule-error");
    hours.setCustomValidity("");
    if (hoursError) {
      hoursError.hidden = true;
      hoursError.textContent = "";
    }
    if (start.value && end.value && hours.value && end.value > start.value) {
      const startMs = new Date(start.value).getTime();
      const endMs = new Date(end.value).getTime();
      const availableMinutes = (endMs - startMs) / 60000;
      if (Number(hours.value) * 60 > availableMinutes + 1e-8) {
        const message = "Estimated hours cannot exceed the planned start-to-end window.";
        hours.setCustomValidity(message);
        if (hoursError) {
          hoursError.textContent = message;
          hoursError.hidden = false;
        }
      }
    }
  }

  for (const field of [start, end, due, hours]) {
    field.addEventListener("input", validateDates);
    field.addEventListener("change", validateDates);
  }
  form.addEventListener("reset", () => requestAnimationFrame(validateDates));
  validateDates();
})();
