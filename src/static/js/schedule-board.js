document.addEventListener("click", (event) => {
  const openButton = event.target.closest("[data-open-dialog]");
  if (openButton) {
    const dialog = document.getElementById(openButton.dataset.openDialog);
    if (dialog instanceof HTMLDialogElement && !dialog.open) dialog.showModal();
    return;
  }

  if (event.target.closest("[data-close-dialog]")) {
    const dialog = event.target.closest("dialog");
    if (dialog instanceof HTMLDialogElement) dialog.close();
  }
});

document.addEventListener("change", (event) => {
  const select = event.target.closest("select[id^='technician-']");
  if (!select) return;
  const orderId = select.id.slice("technician-".length);
  const option = select.selectedOptions[0];
  const availability = document.querySelector(`[data-technician-availability='${orderId}']`);
  const workload = document.querySelector(`[data-technician-workload='${orderId}']`);
  if (availability) {
    availability.textContent = option && option.value
      ? "Availability: eligible based on current assignments; the server rechecks before saving."
      : "Availability: select an available technician.";
  }
  if (workload) {
    workload.textContent = option && option.value
      ? `Current workload: ${option.dataset.activeJobs || 0} active jobs · ${option.dataset.workloadHours || "0.0"} h.`
      : "Current workload: select a technician.";
  }
});

document.addEventListener("click", (event) => {
  if (event.target instanceof HTMLDialogElement && event.target.open) event.target.close();
});
