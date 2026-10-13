window.FccProfiles = (() => {
  const keys = ["model", "model_fable", "model_opus", "model_sonnet", "model_haiku", "model_fallbacks", "reasoning_policy", "reasoning_fable", "reasoning_opus", "reasoning_sonnet", "reasoning_haiku"];
  let records = new Map();
  let selected = "default";
  let busy = false;
  let initialized = false;
  let namingAction = "create";

  function message(text, kind = "") {
    byId("profileMessage").textContent = text;
    byId("profileMessage").className = `message-area ${kind}`.trim();
  }

  function dirty() {
    return Array.from(byId("profileFields").querySelectorAll("input[data-key], select[data-key]")).some((input) => comparableValue(readFieldValue(input)) !== input.dataset.original);
  }

  function updateDirtyState() {
    const changed = dirty();
    byId("profileDirtyState").textContent = changed ? "Unsaved profile changes" : "No changes";
    byId("saveProfile").disabled = busy || !changed;
    byId("discardProfileChanges").disabled = busy || !changed;
  }

  function setBusy(value) {
    busy = value;
    byId("view-profile_config").inert = value;
    byId("profileNameForm").inert = value;
    byId("profileDeleteDialog").inert = value;
    byId("view-profile_config").setAttribute("aria-busy", String(value));
    updateDirtyState();
  }

  function permitDiscard() {
    return !dirty() || window.confirm("Discard unsaved profile changes?");
  }

  function render() {
    state.modelComboboxes.forEach((combobox) => {
      if (byId("profileFields").contains(combobox.element)) {
        combobox.close();
        state.modelComboboxes.delete(combobox);
      }
    });
    byId("profileFields").replaceChildren();
    byId("profileSelect").replaceChildren(...Array.from(records.values(), (record) => option(record.name, record.name === "default" ? "default — Model Config" : record.name)));
    byId("profileSelect").value = selected;
    byId("profileSelect").disabled = !records.size;
    byId("createProfile").disabled = !records.has("default");
    byId("duplicateProfile").disabled = !records.has(selected);
    const isDefault = selected === "default";
    byId("profileDefaultNotice").hidden = !isDefault;
    byId("editDefaultProfile").hidden = !isDefault;
    byId("profileEditor").hidden = isDefault || !records.has(selected);
    if (!isDefault && records.has(selected)) {
      byId("profileEditorTitle").textContent = `Editing ${selected}`;
      byId("profileLaunchHint").textContent = `To use these settings in a new terminal session, launch with --fcc-profile ${selected}.`;
      const configuration = records.get(selected).configuration;
      keys.forEach((key) => {
        const template = state.fields.get(key.toUpperCase());
        if (!template) return;
        let value = configuration[key];
        if (key === "model_fallbacks") value = value?.length ? JSON.stringify(value) : null;
        // These are saved routing choices, independent of process environment locks.
        const field = { ...template, key, value: value ?? null, locked: false, source: "", advanced: false, secret: false, nullable: key === "model_fallbacks" || key.startsWith("model_") ? true : template.nullable };
        if (template.type === "optional_model") field.description = "Select None to use this profile’s base model for this tier.";
        if (key === "model_fallbacks") field.description = "Tried in order if this profile’s selected model fails before output starts. Each attempt may consume provider usage. Native Anthropic Messages requests use only Anthropic models as fallbacks.";
        byId("profileFields").appendChild(renderField(field, { idPrefix: "profile" }));
      });
    }
    updateDirtyState();
  }

  async function refresh() {
    const result = await api("/admin/api/profiles");
    records = new Map(result.profiles.map((record) => [record.name, record]));
    if (!records.has(selected)) selected = "default";
    render();
  }

  async function operation(action) {
    if (busy) return;
    setBusy(true);
    message("");
    try {
      await action();
    } catch (error) {
      message(error.message, "error");
    } finally {
      setBusy(false);
    }
  }

  function configurationFromFields() {
    const configuration = {};
    keys.forEach((key) => {
      const input = byId(`profile-${key}`);
      if (!input) return;
      const value = readFieldValue(input);
      configuration[key] = key === "model_fallbacks" ? (value ? JSON.parse(value) : null) : typeof value === "string" ? value.trim() : value;
    });
    if (!configuration.model) throw new Error("Choose a base model before saving this profile.");
    return configuration;
  }

  function openNameDialog(action) {
    if (!permitDiscard()) return;
    namingAction = action;
    const label = action === "duplicate" ? "Duplicate profile" : "Create profile";
    byId("profileNameTitle").textContent = action === "duplicate" ? `Duplicate ${selected}` : "Create profile from default";
    byId("submitProfileName").textContent = label;
    byId("profileName").value = "";
    byId("profileName").setCustomValidity("");
    byId("profileNameError").textContent = "";
    byId("profileNameDialog").showModal();
    byId("profileName").focus();
  }

  function initialize() {
    if (initialized) return;
    initialized = true;
    window.FccFormControls.configure(byId("profileNameForm"));
    window.FccFormControls.configure(byId("profileName"));
    byId("profileSelect").addEventListener("change", () => {
      if (!permitDiscard()) { byId("profileSelect").value = selected; return; }
      selected = byId("profileSelect").value;
      render();
      message("");
    });
    byId("editDefaultProfile").addEventListener("click", () => navigateToView("model_config"));
    byId("createProfile").addEventListener("click", () => openNameDialog("create"));
    byId("duplicateProfile").addEventListener("click", () => openNameDialog("duplicate"));
    byId("reloadProfiles").addEventListener("click", () => { if (permitDiscard()) void operation(refresh); });
    byId("discardProfileChanges").addEventListener("click", () => { if (permitDiscard()) { render(); message(""); } });
    byId("saveProfile").addEventListener("click", () => operation(async () => {
      const configuration = configurationFromFields();
      const record = await api(`/admin/api/profiles/${encodeURIComponent(selected)}`, { method: "PUT", body: JSON.stringify({ configuration }) });
      records.set(selected, record);
      render();
      message(`Profile “${selected}” saved. Existing sessions keep their settings.`, "ok");
    }));
    byId("profileName").addEventListener("input", () => { byId("profileName").setCustomValidity(""); byId("profileNameError").textContent = ""; });
    byId("cancelProfileName").addEventListener("click", () => byId("profileNameDialog").close());
    byId("profileNameDialog").addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
    byId("profileNameForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy) return;
      const input = byId("profileName");
      const name = input.value;
      const error = name === "default" ? "The name default is reserved. Choose another name." : records.has(name) ? "A profile with this name already exists." : "";
      if (error) { input.setCustomValidity(error); input.reportValidity(); return; }
      setBusy(true);
      try {
        const path = namingAction === "duplicate" ? `/admin/api/profiles/${encodeURIComponent(selected)}/duplicate` : "/admin/api/profiles";
        const body = namingAction === "duplicate" ? { name } : { name, configuration: Object.fromEntries(keys.filter((key) => key in records.get("default").configuration).map((key) => [key, records.get("default").configuration[key]])) };
        const record = await api(path, { method: "POST", body: JSON.stringify(body) });
        records.set(name, record);
        selected = name;
        render();
        byId("profileNameDialog").close();
        message(`Profile “${name}” ${namingAction === "duplicate" ? "duplicated" : "created"}. Edit its routing choices below.`, "ok");
      } catch (error) {
        byId("profileNameError").textContent = error.message;
      } finally { setBusy(false); }
    });
    byId("deleteProfile").addEventListener("click", () => {
      byId("profileDeleteDescription").textContent = `Delete “${selected}” and any unsaved edits?`;
      byId("profileDeleteDialog").showModal();
    });
    byId("cancelProfileDelete").addEventListener("click", () => byId("profileDeleteDialog").close());
    byId("profileDeleteDialog").addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
    byId("confirmProfileDelete").addEventListener("click", () => operation(async () => {
      await api(`/admin/api/profiles/${encodeURIComponent(selected)}`, { method: "DELETE" });
      const deleted = selected;
      records.delete(selected);
      selected = "default";
      render();
      byId("profileDeleteDialog").close();
      message(`Profile “${deleted}” deleted. Existing sessions keep their settings.`, "ok");
    }));
    window.addEventListener("beforeunload", (event) => { if (dirty()) { event.preventDefault(); event.returnValue = ""; } });
  }

  async function load() {
    initialize();
    if (dirty()) return;
    try { await refresh(); }
    catch (error) { message(`Could not load profiles: ${error.message}. Use Reload profiles to retry.`, "error"); }
  }

  return { load, updateDirtyState, message };
})();
