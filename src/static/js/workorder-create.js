(() => {
  const sourceType = document.getElementById("id_source_type");
  const sourceRecord = document.getElementById("id_source_request");
  const sourceRow = document.getElementById("source-record-row");
  const sourceEmpty = document.getElementById("source-record-empty");
  const site = document.getElementById("id_site");
  const asset = document.getElementById("id_asset");
  const assetEmpty = document.getElementById("asset-site-empty");
  const assetEmptyMessage = document.getElementById("asset-site-empty-message");
  const assetCreateLink = document.getElementById("asset-site-create-link");
  const component = document.getElementById("id_component");
  const priority = document.getElementById("id_priority");
  if (!sourceType || !sourceRecord || !site || !asset) return;

  const metadataNode = document.getElementById("workorder-source-metadata");
  const sourceMetadata = metadataNode ? JSON.parse(metadataNode.textContent) : {};

  // A linked approved request owns the work-order priority. Keep the select in
  // sync with its severity and prevent the browser from submitting a stale
  // autofilled value from an earlier form attempt.
  const syncSourcePriority = (selected) => {
    if (!priority) return;
    const linkedSource = sourceType.value === "REQUEST" || sourceType.value === "INCIDENT";
    const expected = linkedSource && selected ? selected.priority : "";
    Array.from(priority.options).forEach((option) => {
      option.disabled = Boolean(expected && option.value && option.value !== expected);
    });
    if (expected) priority.value = expected;
  };

  const filterAssets = () => {
    const emptyOption = asset.options[0];
    if (emptyOption) emptyOption.textContent = site.value ? "Select an active asset" : "Select a site first";
    asset.disabled = !site.value;
    Array.from(asset.options).forEach((option) => {
      if (!option.value) return;
      option.hidden = !site.value || option.dataset.siteId !== site.value;
    });
    if (asset.selectedOptions[0]?.hidden) asset.value = "";
    const visibleAssets = Array.from(asset.options).filter((option) => option.value && !option.hidden).length;
    if (assetEmpty) {
      assetEmpty.hidden = visibleAssets > 0;
      if (assetEmptyMessage) assetEmptyMessage.textContent = !site.value
        ? "Choose a site to load its active assets."
        : "No active assets are available for this site. Create or activate an asset, then return here.";
      if (assetCreateLink) assetCreateLink.hidden = !site.value;
      assetEmpty.classList.toggle("text-warning", Boolean(site.value && visibleAssets === 0));
      assetEmpty.classList.toggle("text-secondary", !site.value);
    }
    if (component) filterComponents();
  };

  const filterComponents = () => {
    const emptyOption = component.options[0];
    if (emptyOption) emptyOption.textContent = asset.value ? "No component selected" : "Select an asset first";
    component.disabled = !asset.value;
    Array.from(component.options).forEach((option) => {
      if (!option.value) return;
      option.hidden = !asset.value || option.dataset.parentAsset !== asset.value;
    });
    if (component.selectedOptions[0]?.hidden) component.value = "";
  };

  const updateSource = () => {
    const type = sourceType.value;
    const linked = type === "REQUEST" || type === "INCIDENT";
    if (sourceRow) sourceRow.hidden = !linked;
    if (sourceRecord) sourceRecord.required = linked;
    const sourceRequired = document.getElementById("source-record-required");
    const sourceOptional = document.getElementById("source-record-optional");
    if (sourceRequired) sourceRequired.hidden = !linked;
    if (sourceOptional) sourceOptional.hidden = linked;
    if (sourceEmpty) sourceEmpty.hidden = !linked || Array.from(sourceRecord.options).some((option) =>
      option.value && option.dataset.sourceKind === type);

    let currentIsValid = false;
    Array.from(sourceRecord.options).forEach((option) => {
      if (!option.value) return;
      option.hidden = option.dataset.sourceKind !== type;
      if (option.value === sourceRecord.value && !option.hidden) currentIsValid = true;
    });
    if (!currentIsValid) sourceRecord.value = "";
    const selected = sourceMetadata[sourceRecord.value];
    syncSourcePriority(selected);
    if (selected) {
      site.value = selected.site;
      filterAssets();
      asset.value = selected.asset;
      filterComponents();
    }
    filterAssets();
  };

  sourceType.addEventListener("change", updateSource);
  sourceRecord.addEventListener("change", updateSource);
  const form = sourceType.form;
  if (form) form.addEventListener("submit", () => syncSourcePriority(sourceMetadata[sourceRecord.value]));
  site.addEventListener("change", filterAssets);
  asset.addEventListener("change", filterComponents);
  updateSource();
})();
