(() => {
  const sourceType = document.getElementById("id_source_type");
  const sourceRecord = document.getElementById("id_source_request");
  const sourceReference = document.getElementById("id_source_reference");
  const sourceReferenceRow = document.getElementById("source-reference-row");
  const sourceRow = document.getElementById("source-record-row");
  const sourceLabelText = document.getElementById("source-record-label-text");
  const sourceRequired = document.getElementById("source-record-required");
  const sourceOptional = document.getElementById("source-record-optional");
  const sourceEmpty = document.getElementById("source-record-empty");
  const sourceEmptyMessage = document.getElementById("source-record-empty-message");

  const site = document.getElementById("id_site");
  const asset = document.getElementById("id_asset");
  const assetEmpty = document.getElementById("asset-site-empty");
  const assetEmptyMessage = document.getElementById("asset-site-empty-message");
  const assetCreateLink = document.getElementById("asset-site-create-link");

  if (!sourceType || !site || !asset) return;

  const metadataNode = document.getElementById("workorder-source-metadata");
  const sourceMetadata = metadataNode ? JSON.parse(metadataNode.textContent) : {};

  // Cache initial option sets to guarantee no duplicates and clean filtering across browsers
  const initialSourceOptions = sourceRecord ? Array.from(sourceRecord.options).map((opt) => ({
    value: opt.value,
    text: opt.textContent,
    sourceKind: opt.dataset.sourceKind,
    siteId: opt.dataset.siteId,
    assetId: opt.dataset.assetId,
  })) : [];

  const initialAssetOptions = Array.from(asset.options).map((opt) => ({
    value: opt.value,
    text: opt.textContent,
    siteId: opt.dataset.siteId,
  }));

  const filterAssets = () => {
    const selectedSiteId = site.value;
    const currentAssetVal = asset.value;

    asset.innerHTML = "";

    const defaultOpt = document.createElement("option");
    defaultOpt.value = "";
    defaultOpt.textContent = selectedSiteId ? "Select an active asset" : "Select a site first";
    asset.appendChild(defaultOpt);

    let matchingCount = 0;
    initialAssetOptions.forEach((item) => {
      if (!item.value) return;
      if (!selectedSiteId || item.siteId === selectedSiteId) {
        const opt = document.createElement("option");
        opt.value = item.value;
        opt.textContent = item.text;
        opt.dataset.siteId = item.siteId || "";
        if (item.value === currentAssetVal) {
          opt.selected = true;
        }
        asset.appendChild(opt);
        matchingCount++;
      }
    });

    if (assetEmpty) {
      if (!selectedSiteId) {
        assetEmpty.hidden = true;
      } else {
        assetEmpty.hidden = matchingCount > 0;
        if (assetEmptyMessage) {
          assetEmptyMessage.textContent = matchingCount === 0
            ? "No active assets are available for this site. Create or activate an asset, then return here."
            : "";
        }
        if (assetCreateLink) assetCreateLink.hidden = matchingCount > 0;
      }
    }
  };

  const resetFormFields = () => {
    if (sourceRecord) sourceRecord.value = "";
    if (sourceReference) sourceReference.value = "";
    site.value = "";
    asset.value = "";
    filterAssets();
  };

  const updateSource = (isUserSwitch = false) => {
    const type = sourceType.value;
    const isLinked = type === "REQUEST" || type === "INCIDENT";
    const isPreventive = type === "PM";

    if (sourceRow) sourceRow.hidden = !isLinked;
    if (sourceReferenceRow) sourceReferenceRow.hidden = !isPreventive;

    if (sourceRecord) sourceRecord.required = isLinked;
    if (sourceReference) sourceReference.required = isPreventive;

    if (sourceRequired) sourceRequired.hidden = !isLinked;
    if (sourceOptional) sourceOptional.hidden = isLinked;

    if (sourceLabelText) {
      if (type === "REQUEST") {
        sourceLabelText.textContent = "Approved service request";
      } else if (type === "INCIDENT") {
        sourceLabelText.textContent = "Approved incident";
      } else {
        sourceLabelText.textContent = "Approved incident or service request";
      }
    }

    if (isUserSwitch) {
      resetFormFields();
    }

    if (sourceRecord && isLinked) {
      const currentVal = sourceRecord.value;
      sourceRecord.innerHTML = "";

      const emptyOpt = document.createElement("option");
      emptyOpt.value = "";
      emptyOpt.textContent = type === "REQUEST"
        ? "Select an approved service request"
        : "Select an approved incident";
      sourceRecord.appendChild(emptyOpt);

      let count = 0;
      const seenValues = new Set();
      initialSourceOptions.forEach((item) => {
        if (!item.value || seenValues.has(item.value)) return;
        if (item.sourceKind === type) {
          seenValues.add(item.value);
          const opt = document.createElement("option");
          opt.value = item.value;
          opt.textContent = item.text;
          opt.dataset.sourceKind = item.sourceKind;
          opt.dataset.siteId = item.siteId || "";
          opt.dataset.assetId = item.assetId || "";
          if (item.value === currentVal) {
            opt.selected = true;
          }
          sourceRecord.appendChild(opt);
          count++;
        }
      });

      if (sourceEmpty) {
        sourceEmpty.hidden = count > 0;
        if (sourceEmptyMessage && count === 0) {
          const kindLabel = type === "REQUEST" ? "service request" : "incident";
          sourceEmptyMessage.textContent = `No approved, unlinked ${kindLabel} is available. Records already used to create a work order cannot be selected again.`;
        }
      }
    } else if (sourceEmpty) {
      sourceEmpty.hidden = true;
    }

    if (!isUserSwitch) {
      applySourceMetadata();
    }
  };

  const applySourceMetadata = () => {
    if (!sourceRecord) return;
    if (!sourceRecord.value) {
      site.value = "";
      asset.value = "";
      filterAssets();
      return;
    }

    const selected = sourceMetadata[sourceRecord.value];
    if (!selected) return;

    if (selected.site && site.value !== selected.site) {
      site.value = selected.site;
      filterAssets();
    }

    if (selected.asset && asset.value !== selected.asset) {
      asset.value = selected.asset;
    }
    // Title is strictly entered manually by user - never auto-filled
  };

  sourceType.addEventListener("change", () => {
    updateSource(true);
  });

  if (sourceRecord) {
    sourceRecord.addEventListener("change", () => {
      applySourceMetadata();
    });
  }

  site.addEventListener("change", () => {
    filterAssets();
  });

  asset.addEventListener("change", () => {
    const chosen = initialAssetOptions.find((item) => item.value === asset.value);
    if (chosen && chosen.siteId && site.value !== chosen.siteId) {
      site.value = chosen.siteId;
    }
  });

  // Initial run without resetting already bound form values
  updateSource(false);
  filterAssets();
})();
