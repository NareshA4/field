(() => {
  const sourceType = document.getElementById("id_source_type");
  const sourceRecord = document.getElementById("id_source_request");
  const sourceReference = document.getElementById("id_source_reference");
  const sourceReferenceRow = document.getElementById("source-reference-row");
  const sourceRow = document.getElementById("source-record-row");
  const sourceEmpty = document.getElementById("source-record-empty");
  const sourceEmptyMessage = document.getElementById("source-record-empty-message");
  const site = document.getElementById("id_site");
  const asset = document.getElementById("id_asset");
  const assetEmpty = document.getElementById("asset-site-empty");
  const assetEmptyMessage = document.getElementById("asset-site-empty-message");
  const assetCreateLink = document.getElementById("asset-site-create-link");
  const component = document.getElementById("id_component");
  if (!sourceType || !sourceRecord || !site || !asset) return;

  const metadataNode = document.getElementById("workorder-source-metadata");
  const sourceMetadata = metadataNode ? JSON.parse(metadataNode.textContent) : {};
  const description = document.getElementById("id_description");
  const title = document.getElementById("id_title");
  let manualDescription = description ? description.value : "";
  let manualTitle = title ? title.value : "";
  if (description) description.addEventListener("input", () => {
    if (!description.readOnly) manualDescription = description.value;
  });
  if (title) title.addEventListener("input", () => { manualTitle = title.value; });

  const mirrorDisabledSelect = (select, locked) => {
    if (!select) return;
    let mirror = select.form.querySelector(`[data-source-mirror="${select.name}"]`);
    if (locked && select.value) {
      if (!mirror) {
        mirror = document.createElement("input");
        mirror.type = "hidden";
        mirror.name = select.name;
        mirror.dataset.sourceMirror = select.name;
        select.insertAdjacentElement("afterend", mirror);
      }
      mirror.value = select.value;
      select.disabled = true;
      select.setAttribute("aria-disabled", "true");
    } else {
      if (mirror) mirror.remove();
      select.disabled = false;
      select.removeAttribute("aria-disabled");
    }
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
    const preventive = type === "PM";
    if (sourceRow) sourceRow.hidden = !linked;
    if (sourceRecord) sourceRecord.required = linked;
    if (sourceReferenceRow) sourceReferenceRow.hidden = !preventive;
    if (sourceReference) sourceReference.required = preventive;
    const sourceRequired = document.getElementById("source-record-required");
    const sourceOptional = document.getElementById("source-record-optional");
    if (sourceRequired) sourceRequired.hidden = !linked;
    if (sourceOptional) sourceOptional.hidden = linked;
    const hasMatchingSource = Array.from(sourceRecord.options).some((option) =>
      option.value && option.dataset.sourceKind === type);
    if (sourceEmpty) sourceEmpty.hidden = !linked || hasMatchingSource;
    if (sourceEmptyMessage && linked && !hasMatchingSource) {
      const label = type === "REQUEST" ? "service request" : "incident";
      sourceEmptyMessage.textContent = `No approved, unlinked ${label} is available. Records already used to create a work order cannot be selected again.`;
    }

    let currentIsValid = false;
    Array.from(sourceRecord.options).forEach((option) => {
      if (!option.value) return;
      option.hidden = option.dataset.sourceKind !== type;
      if (option.value === sourceRecord.value && !option.hidden) currentIsValid = true;
    });
    if (!currentIsValid) sourceRecord.value = "";
    const selected = sourceMetadata[sourceRecord.value];
    if (selected) {
      if (title && (!title.value.trim() || Object.values(sourceMetadata).some((item) => item.title === title.value))) {
        title.value = selected.title || "";
      }
      site.value = selected.site;
      filterAssets();
      asset.value = selected.asset;
      filterComponents();
      mirrorDisabledSelect(site, true);
      mirrorDisabledSelect(asset, true);
      if (description) {
        description.value = selected.description || "";
        description.readOnly = true;
      }
    } else {
      mirrorDisabledSelect(site, false);
      mirrorDisabledSelect(asset, false);
      if (description) {
        description.readOnly = false;
        description.value = manualDescription;
      }
      if (title && (!title.value.trim() || Object.values(sourceMetadata).some((item) => item.title === title.value))) {
        title.value = manualTitle;
      }
    }
    filterAssets();
    if (selected) {
      mirrorDisabledSelect(site, true);
      mirrorDisabledSelect(asset, true);
    }
  };

  sourceType.addEventListener("change", updateSource);
  sourceRecord.addEventListener("change", updateSource);
  const form = sourceType.form;
  site.addEventListener("change", filterAssets);
  asset.addEventListener("change", filterComponents);
  updateSource();
})();
