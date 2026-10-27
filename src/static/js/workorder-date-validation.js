(() => {
  const start = document.getElementById("id_planned_start");
  const end = document.getElementById("id_planned_end");
  const due = document.getElementById("id_due_at");
  const hours = document.getElementById("id_estimated_hours");
  const form = start?.form;
  if (!start || !end || !due || !hours || !form?.matches("[data-workorder-date-validation]")) return;

  const endError = document.getElementById("planned-end-rule-error");
  const dueError = document.getElementById("due-at-rule-error");
  const hoursError = document.getElementById("estimated-hours-rule-error");

  function validateDates() {
    // 1. Planned End: must be strictly later than planned start
    end.setCustomValidity("");
    if (endError) {
      endError.hidden = true;
      endError.textContent = "";
    }
    if (start.value && end.value && end.value <= start.value) {
      const msg = "Planned end must be later than planned start.";
      end.setCustomValidity(msg);
      if (endError) {
        endError.textContent = msg;
        endError.hidden = false;
      }
    }

    // 2. Due At: can be greater than or equal to planned end (due >= end)
    due.setCustomValidity("");
    if (dueError) {
      dueError.hidden = true;
      dueError.textContent = "";
    }
    if (end.value && due.value && due.value < end.value) {
      const msg = "Due date must be on or after planned end.";
      due.setCustomValidity(msg);
      if (dueError) {
        dueError.textContent = msg;
        dueError.hidden = false;
      }
    } else if (start.value && due.value && due.value <= start.value) {
      const msg = "Due date must be later than planned start.";
      due.setCustomValidity(msg);
      if (dueError) {
        dueError.textContent = msg;
        dueError.hidden = false;
      }
    }

    // 3. Estimated Hours: positive and within (planned_end - planned_start) window
    hours.setCustomValidity("");
    if (hoursError) {
      hoursError.hidden = true;
      hoursError.textContent = "";
    }
    if (hours.value && Number(hours.value) <= 0) {
      const msg = "Estimated hours must be greater than zero.";
      hours.setCustomValidity(msg);
      if (hoursError) {
        hoursError.textContent = msg;
        hoursError.hidden = false;
      }
    } else if (start.value && end.value && hours.value && end.value > start.value) {
      const startMs = new Date(start.value).getTime();
      const endMs = new Date(end.value).getTime();
      const availableMinutes = (endMs - startMs) / 60000;
      if (Number(hours.value) * 60 > availableMinutes + 1e-8) {
        const msg = "Estimated hours cannot exceed the planned start-to-end window.";
        hours.setCustomValidity(msg);
        if (hoursError) {
          hoursError.textContent = msg;
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
