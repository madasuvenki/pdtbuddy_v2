 /* ============================================================
   admin.js  -  PDT Buddy Admin Panel
   Supports:
     * AUTO  (Gen5 / Gen4.5 -> Program -> Family -> Category -> SP)
     * WBC   (Target -> SP,  e.g. Kuno -> LE.1.1)
     * All other flat BUs  (plain target key)
   DB-name patterns:
     AUTO  ->  Program_Family_Category_SP   e.g. Nord_HGY_ADAS_SP1
     WBC   ->  Target_SP                    e.g. Kuno_LE1_1
     Other ->  user-typed key               e.g. molokai_v2
   ============================================================ */

document.addEventListener('DOMContentLoaded', () => {

  /* -- tiny helpers ------------------------------------------- */
  const $  = (id) => document.getElementById(id);
  const getVal  = (el) => (el?.value || '').trim();
  const setVal  = (el, v) => { if (el) el.value = v; };

    /**
   * slugify - converts any string to a safe lowercase DB-name token.
   * Dots, spaces, slashes, hyphens -> underscore.
   * Collapses multiple underscores; strips leading/trailing.
   * Always lowercased so DB names are consistent.
   * e.g.  "LE.1.1"  ->  "le1_1"
   *        "Gen4.5"  ->  "gen4_5"
   *        "Nord"    ->  "nord"
   */
  const slugify = (v) =>
    (v || '')
      .trim()
      .toLowerCase()                // always lowercase
      .replace(/\./g, '_')          // dot  -> underscore
      .replace(/[\s\-\/]+/g, '_')   // space/dash/slash -> underscore
      .replace(/_+/g, '_')          // collapse multiples
      .replace(/^_+|_+$/g, '');     // strip edges

  /* BU type helpers */
  const isAutoBU = (v) => ['AUTO', 'AUTOMOTIVE'].includes((v || '').trim().toUpperCase());
      const isWbcBU  = (v) => ['WBC', 'MDM_TELEMATICS', 'AUTO_TELEMATICS'].includes((v || '').trim().toUpperCase());
  const isMobileBU = (v) => (v || '').trim().toUpperCase() === 'MOBILE';

  const setStatus = (el, msg, type = 'info') => {
    if (!el) return;
    el.textContent = msg;
    el.style.color = type === 'success' ? '#28a745'
                   : type === 'error'   ? '#dc3545'
                   :                      '#2980b9';
  };

  /* -- global data injected by Flask ------------------------- */
  const BUSINESS_UNITS_DATA  = window.BUSINESS_UNITS_DATA  || {};
  const ALL_TARGETS_DATA     = window.ALL_TARGETS_DATA     || [];
  const TARGETS_CONFIG_DATA  = window.TARGETS_CONFIG_DATA  || {};

  /* -- element refs ------------------------------------------- */
  const adminToggleBtn          = $('adminToggleBtn');
  const adminModal              = $('adminModal');
  const closeAdminModalXBtn     = $('closeAdminModalXBtn');
  const cancelAddTargetModalBtn = $('cancelAddTargetModalBtn');

    const adminTabAddTarget         = $('adminTabAddTarget');
  const adminTabResyncMilestones  = $('adminTabResyncMilestones');
  const adminTabAxiomRules        = $('adminTabAxiomRules');
  const adminTabRemoveTarget      = $('adminTabRemoveTarget');

  const adminSectionAddTarget        = $('admin_section_add_target');
  const adminSectionResyncMilestones = $('admin_section_resync_milestones');
  const adminSectionAxiomRules       = $('admin_section_axiom_rules');
  const adminSectionRemoveTarget     = $('admin_section_remove_target');
  const adminSectionUpdateTarget     = $('admin_section_update_target');


  /* Add-target form */
  const adminBuAdd             = $('admin_bu_add');
  const adminTargetAdd         = $('admin_target_add');       // hidden when AUTO/WBC
  const adminTargetDisplayAdd  = $('admin_target_display_add');
  const adminChipNameAdd       = $('admin_chip_name_add');
  const adminSpNameAdd         = $('admin_sp_name_add');
  const adminFetchSpMilestonesBtn = $('admin_fetch_sp_milestones_btn');
  const adminSpMilestonesPreview  = $('admin_sp_milestones_preview');
    const adminRawMilestonesPreview = $('admin_raw_milestones_preview');
  const adminPathAdd           = $('admin_path_add');
  const adminUniqueCrPathAdd    = $('admin_unique_cr_path_add');
  const submitNewTargetBtn     = $('submitNewTargetBtn');
  const adminStatusAdd         = $('admin_status_add');
  const normalTargetGroup      = $('admin_target_name_group');

  /* Mode toggle */
  let adminUniqueCrOnlyMode = false;
  const modeFullBtn   = $('adminModeFullTarget');
  const modeUniqueBtn = $('adminModeUniqueCrOnly');
  const modeBanner    = $('adminModeUniqueCrBanner');
  const excelGroup    = $('admin_excel_path_group');
  const chipGroup     = document.querySelector('#admin_chip_name_add')?.closest('.admin-form-group');
  const spGroup       = document.querySelector('#admin_sp_name_add')?.closest('.admin-form-group');

  function setAdminMode(uniqueOnly) {
    adminUniqueCrOnlyMode = uniqueOnly;
    if (modeFullBtn)   { modeFullBtn.className   = uniqueOnly ? 'btn' : 'btn primary-btn'; modeFullBtn.style.cssText   = uniqueOnly ? 'flex:1;font-size:12px;font-weight:800;background:#fff;color:#374151;border:2px solid #e2e8f0;' : 'flex:1;font-size:12px;font-weight:800;'; }
    if (modeUniqueBtn) { modeUniqueBtn.className = uniqueOnly ? 'btn primary-btn' : 'btn'; modeUniqueBtn.style.cssText = uniqueOnly ? 'flex:1;font-size:12px;font-weight:800;background:linear-gradient(135deg,#c2410c,#ea580c);color:#fff;border:none;' : 'flex:1;font-size:12px;font-weight:800;background:#fff7ed;color:#c2410c;border:2px solid #fed7aa;'; }
    if (modeBanner)    modeBanner.style.display = uniqueOnly ? '' : 'none';
    if (excelGroup)    excelGroup.style.display = uniqueOnly ? 'none' : '';
    const chipGrp = $('admin_chip_name_add') ? $('admin_chip_name_add').closest('.admin-form-group') : null;
    const spGrp   = $('admin_sp_name_add')   ? $('admin_sp_name_add').closest('.admin-form-group')   : null;
    if (chipGrp) chipGrp.style.display = uniqueOnly ? 'none' : '';
    if (spGrp)   spGrp.style.display   = uniqueOnly ? 'none' : '';
    const reqStar = $('admin_unique_cr_req_star'), optLbl = $('admin_unique_cr_opt_label');
    if (reqStar) reqStar.style.display = uniqueOnly ? '' : 'none';
    if (optLbl)  optLbl.style.display  = uniqueOnly ? 'none' : '';
    if ($('submitNewTargetBtn')) $('submitNewTargetBtn').textContent = uniqueOnly ? 'Add Unique CR Target' : 'Add & Ingest';
  }
  if (modeFullBtn)   modeFullBtn.addEventListener('click',   () => setAdminMode(false));
  if (modeUniqueBtn) modeUniqueBtn.addEventListener('click', () => setAdminMode(true));

  /* Mobile sub-fields */
  const adminMobileFields         = $('admin_mobile_fields');
  const adminMobileProductFamily  = $('admin_mobile_product_family');

    /* AUTO sub-fields */
  const adminAutoFields          = $('admin_auto_fields');
  const adminAutoGen             = $('admin_auto_gen');
  const adminAutoProgram         = $('admin_auto_program');
  const adminAutoFamily          = $('admin_auto_family');
  const adminAutoCategory        = $('admin_auto_category');
  const adminAutoSp              = $('admin_auto_sp');          // SP label (shown only when category selected)
  const adminAutoSpGroup         = $('admin_auto_sp_group');    // wrapper div for SP field
  const adminAutoTargetKeyPreview = $('admin_auto_target_key_preview');

  /* WBC sub-fields */
  const adminWbcFields           = $('admin_wbc_fields');
  const adminWbcTarget           = $('admin_wbc_target');
  const adminWbcSp               = $('admin_wbc_sp');
  const adminWbcTargetKeyPreview = $('admin_wbc_target_key_preview');

  /* Milestone / resync */
  const adminTargetMilestoneResyncSelect = $('admin_target_milestone_resync_select');
  const adminMilestoneSpName             = $('admin_milestone_sp_name');
  const adminLoadSpBtn                   = $('admin_load_sp_btn');
  const adminUpdateSpBtn                 = $('admin_update_sp_btn');
  const adminStatusMilestoneResync       = $('admin_status_milestone_resync');
  const resyncMilestonesBtn              = $('resyncMilestonesBtn');

  /* Remove / update */
  const adminTargetRemoveSelect      = $('admin_target_remove_select');
  const removeTargetBtn              = $('removeTargetBtn');
  const setActiveBtn                 = $('setActiveBtn');
  const setInactiveBtn               = $('setInactiveBtn');
  const adminStatusToggle            = $('admin_status_toggle');
  const adminTargetUpdateSelect      = $('admin_target_update_select');
  const updateExistingTargetDataBtn  = $('updateExistingTargetDataBtn');
  const adminStatusUpdate            = $('admin_status_update');
  const syncDbBtn                    = $('syncDbBtn');
    const axiomRuleName                = $('axiom_rule_name');
  const axiomRuleMatch               = $('axiom_rule_match');
  const axiomRuleEnabled             = $('axiom_rule_enabled');
  const axiomRuleAddBtn              = $('axiomRuleAddBtn');
  const axiomRuleSaveBtn             = $('axiomRuleSaveBtn');
  const axiomRulesTbody              = $('axiomRulesTbody');
  const adminAxiomRulesStatus        = $('admin_axiom_rules_status');
  const adminAxiomRulesPath          = $('admin_axiom_rules_path');
  let _axiomRules = [];



  /* Milestone picker modal */
  const milestonePickerModal = $('milestonePickerModal');
  const msCloseBtn  = $('msCloseBtn');
  const msCancelBtn = $('msCancelBtn');
  const msSaveBtn   = $('msSaveBtn');
  const msUseEs = $('ms_use_es');  const msEs = $('ms_es');
  const msUseFc = $('ms_use_fc');  const msFc = $('ms_fc');
  const msUseCs = $('ms_use_cs');  const msCs = $('ms_cs');
  const msUseCs1= $('ms_use_cs1'); const msCs1= $('ms_cs1');

  /* -- populate dropdowns ------------------------------------- */
  function populateDropdowns() {
        /* BU selector */
    if (adminBuAdd) {
      adminBuAdd.innerHTML = '<option value="">Select Business Unit</option>';
      Object.keys(BUSINESS_UNITS_DATA).sort().forEach((buKey) => {
        const info = BUSINESS_UNITS_DATA[buKey] || {};
        const opt  = document.createElement('option');
        opt.value       = buKey;
        opt.textContent = info.display_name || buKey;
        adminBuAdd.appendChild(opt);
      });
      // Fallback: if still empty, build from TARGETS_CONFIG_DATA
      if (adminBuAdd.options.length <= 1) {
        const buSet = new Set();
        Object.values(TARGETS_CONFIG_DATA).forEach((cfg) => {
          const bu = String((cfg || {}).bu || (cfg || {}).bu_key || '').toUpperCase();
          if (bu) buSet.add(bu);
        });
        Array.from(buSet).sort().forEach((buKey) => {
          const opt = document.createElement('option');
          opt.value = buKey; opt.textContent = buKey;
          adminBuAdd.appendChild(opt);
        });
      }
    }

    /* Target selectors (milestone / remove / update) */
    const fillTargets = (sel, placeholder) => {
      if (!sel) return;
      sel.innerHTML = `<option value="">${placeholder}</option>`;
      ALL_TARGETS_DATA.forEach((key) => {
        const cfg  = TARGETS_CONFIG_DATA[key] || {};
        const name = cfg.display_name || key.toUpperCase();
        const opt  = document.createElement('option');
        opt.value = key; opt.textContent = name;
        sel.appendChild(opt);
      });
    };
    fillTargets(adminTargetMilestoneResyncSelect, 'Select Target');
    fillTargets(adminTargetRemoveSelect,          'Select Target');
    fillTargets(adminTargetUpdateSelect,          'Select Target to Update');
  }

  /* -- key builders ------------------------------------------- */

    /**
   * AUTO key:
   *   Mandatory:  Program_Family          e.g. Nord_HGY
   *   With Cat+SP: Program_Family_Category_SP  e.g. Nord_HGY_ADAS_SP1
   * (gen is NOT part of the DB name - it's metadata only)
   * CP Version is removed - not required.
   */
  function buildAutoKey() {
    const prog     = slugify(getVal(adminAutoProgram));
    const family   = slugify(getVal(adminAutoFamily));
    const category = slugify(getVal(adminAutoCategory));
    const sp       = slugify(getVal(adminAutoSp));

    // Show/hide SP field based on whether category is selected
    if (adminAutoSpGroup) {
      adminAutoSpGroup.style.display = category ? '' : 'none';
    }

        // Build key: always start with prog+family; add cat+sp only when present
    // All parts already lowercased by slugify
    const parts = [prog, family];
    if (category) parts.push(category);
    if (category && sp) parts.push(sp);
    const key = parts.filter(Boolean).join('_');  // e.g. nord_hgy_adas_sp1

    setVal(adminAutoTargetKeyPreview, key);
    setVal(adminTargetAdd, key);
  }

  /**
   * WBC key:  Target_SP
   * e.g.  Kuno_LE1_1   (dots in SP become underscores)
   */
  function buildWbcKey() {
    const tgt = slugify(getVal(adminWbcTarget));
    const sp  = slugify(getVal(adminWbcSp));

    const parts = [tgt, sp].filter(Boolean);
    const key   = parts.join('_');

    setVal(adminWbcTargetKeyPreview, key);
    setVal(adminTargetAdd, key);
  }

  /* -- show/hide BU-specific sub-forms ----------------------- */
  function toggleBuFields() {
    const bu     = getVal(adminBuAdd);
    const auto   = isAutoBU(bu);
    const wbc    = isWbcBU(bu);
    const mobile = isMobileBU(bu);

    /* normal key input: only for flat BUs */
    if (normalTargetGroup) normalTargetGroup.style.display = (auto || wbc) ? 'none' : '';

    /* Mobile block */
    if (adminMobileFields) adminMobileFields.style.display = mobile ? '' : 'none';

    /* AUTO block */
    if (adminAutoFields) adminAutoFields.style.display = auto ? '' : 'none';

    /* WBC block */
    if (adminWbcFields) adminWbcFields.style.display = wbc ? '' : 'none';

    /* rebuild preview */
    if (auto) buildAutoKey();
    else if (wbc) buildWbcKey();
    else { setVal(adminAutoTargetKeyPreview, ''); setVal(adminWbcTargetKeyPreview, ''); }
  }

  /* -- status helpers ----------------------------------------- */
  function clearStatuses() {
    [adminStatusAdd, adminStatusUpdate, adminStatusMilestoneResync].forEach((el) => {
      if (el) el.textContent = '';
    });
  }

    /* -- panel switcher ----------------------------------------- */
    const ALL_TAB_BTNS = [adminTabAddTarget, adminTabResyncMilestones, adminTabAxiomRules, adminTabRemoveTarget];


  function showAdminPanel(panelEl, tabBtn) {
    // Hide all panels
        [adminSectionAddTarget, adminSectionResyncMilestones, adminSectionAxiomRules,
     adminSectionRemoveTarget, adminSectionUpdateTarget].forEach((el) => {

      if (el) el.classList.remove('active');
    });
    // Show selected panel
    if (panelEl) panelEl.classList.add('active');
    // Highlight active tab button
    ALL_TAB_BTNS.forEach((btn) => {
      if (!btn) return;
      btn.style.outline = '';
      btn.style.boxShadow = '';
      btn.style.transform = '';
    });
    if (tabBtn) {
      tabBtn.style.outline = '3px solid rgba(255,255,255,0.9)';
      tabBtn.style.boxShadow = '0 0 0 6px rgba(255,255,255,0.18)';
      tabBtn.style.transform = 'scale(1.04)';
    }
  }

  /* -- modal open / close ------------------------------------- */
    function openAdminModal() {
    if (!adminModal) return;
    adminModal.style.display = 'flex';
    adminModal.classList.add('visible');
    clearStatuses();
        populateDropdowns();
    toggleBuFields();
    loadAxiomRules();
    // Open with NO panel active -- user must click a button

    showAdminPanel(null, null);
  }

  function closeAdminModal() {
    if (!adminModal) return;
    adminModal.classList.remove('visible');
    setTimeout(() => { adminModal.style.display = 'none'; }, 200);
    clearStatuses();
  }

  /* -- milestone picker --------------------------------------- */
  function openMilestonePicker(m) {
    if (!milestonePickerModal) return;
    m = m || {};
    setVal(msEs,  m.ES  || '');
    setVal(msFc,  m.FC  || '');
    setVal(msCs,  m.CS  || '');
    setVal(msCs1, m.CS1 || m.CS || '');
    if (msUseEs)  msUseEs.checked  = !!m.ES;
    if (msUseFc)  msUseFc.checked  = !!m.FC;
    if (msUseCs)  msUseCs.checked  = !!m.CS;
    if (msUseCs1) msUseCs1.checked = !!(m.CS1 || m.CS);
    milestonePickerModal.style.setProperty('display', 'flex', 'important');
    milestonePickerModal.style.visibility = 'visible';
    milestonePickerModal.style.opacity    = '1';
    milestonePickerModal.classList.add('visible');
    if (milestonePickerModal.firstElementChild)
      milestonePickerModal.firstElementChild.style.zIndex = '10051';
  }

  function closeMilestonePicker() {
    if (!milestonePickerModal) return;
    milestonePickerModal.classList.remove('visible');
    milestonePickerModal.style.removeProperty('display');
    milestonePickerModal.style.visibility = 'hidden';
    milestonePickerModal.style.opacity    = '0';
  }

  /* -- wire modal open/close ---------------------------------- */
  // Only wire adminToggleBtn here if crv2TargetsOverlay is NOT on this page.
  // On CR Overview pages, bu_shell_layout.html handles adminToggleBtn directly.
  if (adminToggleBtn && !document.getElementById('crv2TargetsOverlay')) {
    adminToggleBtn.addEventListener('click', openAdminModal);
  }
  if (closeAdminModalXBtn)     closeAdminModalXBtn.addEventListener('click', closeAdminModal);
  if (cancelAddTargetModalBtn) cancelAddTargetModalBtn.addEventListener('click', closeAdminModal);
  if (adminModal)              adminModal.addEventListener('click', (e) => { if (e.target === adminModal) closeAdminModal(); });

  /* -- tab buttons -------------------------------------------- */
        if (adminTabAddTarget)        adminTabAddTarget.addEventListener('click',        () => showAdminPanel(adminSectionAddTarget,        adminTabAddTarget));
  if (adminTabResyncMilestones) adminTabResyncMilestones.addEventListener('click', () => showAdminPanel(adminSectionResyncMilestones, adminTabResyncMilestones));
  if (adminTabAxiomRules)       adminTabAxiomRules.addEventListener('click',       () => { showAdminPanel(adminSectionAxiomRules, adminTabAxiomRules); loadAxiomRules(); });
  if (adminTabRemoveTarget)     adminTabRemoveTarget.addEventListener('click',     () => showAdminPanel(adminSectionRemoveTarget,     adminTabRemoveTarget));


    /* -- backward-compat stubs -- */
  // (hidden elements kept in DOM for any legacy references)

  /* -- BU change -> toggle sub-forms -------------------------- */
  if (adminBuAdd) adminBuAdd.addEventListener('change', toggleBuFields);

    /* -- AUTO field changes -> rebuild key ---------------------- */
  [adminAutoGen, adminAutoProgram, adminAutoFamily, adminAutoCategory, adminAutoSp].forEach((el) => {
    if (!el) return;
    el.addEventListener('input',  buildAutoKey);
    el.addEventListener('change', buildAutoKey);
  });

  /* -- WBC field changes -> rebuild key ----------------------- */
  [adminWbcTarget, adminWbcSp].forEach((el) => {
    if (!el) return;
    el.addEventListener('input',  buildWbcKey);
    el.addEventListener('change', buildWbcKey);
  });

  /* -- milestone picker close --------------------------------- */
  if (msCloseBtn)  msCloseBtn.addEventListener('click',  closeMilestonePicker);
  if (msCancelBtn) msCancelBtn.addEventListener('click', closeMilestonePicker);
  if (milestonePickerModal)
    milestonePickerModal.addEventListener('click', (e) => { if (e.target === milestonePickerModal) closeMilestonePicker(); });

      function renderAxiomRules(){
    if(!axiomRulesTbody) return;
    axiomRulesTbody.innerHTML = _axiomRules.length
      ? _axiomRules.map((r,i)=>`<tr style="border-bottom:1px solid #e0e7ff;">
          <td style="padding:8px;font-weight:700;">${r.name||''}</td>
          <td style="padding:8px;">${(r.match_contains||[]).join(', ')}</td>
          <td style="padding:8px;">${r.enabled===false?'<span style="color:#dc2626;">No</span>':'<span style="color:#16a34a;">Yes</span>'}</td>
          <td style="padding:8px;display:flex;gap:6px;">
            <button type="button" class="btn secondary-btn" style="font-size:11px;padding:4px 10px;" data-axiom-edit="${i}">Edit</button>
            <button type="button" class="btn" style="font-size:11px;padding:4px 10px;background:#fee2e2;color:#991b1b;border:none;border-radius:6px;" data-axiom-del="${i}">Delete</button>
          </td>
        </tr>`).join('')
      : '<tr><td colspan="4" style="padding:14px;color:#64748b;text-align:center;">No rules yet. Add one above.</td></tr>';
    axiomRulesTbody.querySelectorAll('[data-axiom-edit]').forEach(btn=>btn.addEventListener('click',()=>{
      const r=_axiomRules[Number(btn.dataset.axiomEdit)]||{};
      setVal(axiomRuleName, r.name||'');
      setVal(axiomRuleMatch, (r.match_contains||[]).join(', '));
      if(axiomRuleEnabled) axiomRuleEnabled.checked = r.enabled!==false;
      _axiomRules.splice(Number(btn.dataset.axiomEdit),1);
      renderAxiomRules();
    }));
    axiomRulesTbody.querySelectorAll('[data-axiom-del]').forEach(btn=>btn.addEventListener('click',()=>{
      if(!confirm('Delete this rule?')) return;
      _axiomRules.splice(Number(btn.dataset.axiomDel),1);
      renderAxiomRules();
    }));
  }

  async function loadAxiomRules(){
    if(!axiomRulesTbody) return;
    try{
      const resp = await fetch('/admin/axiom_enrichment_rules');
      const ct = resp.headers.get('content-type')||'';
      if(!ct.includes('application/json')){
        setStatus(adminAxiomRulesStatus, 'Server returned non-JSON (session expired? try refreshing).', 'error');
        return;
      }
      const data = await resp.json();
      if(!data.success) throw new Error(data.message||'Failed to load rules');
      _axiomRules = Array.isArray(data.rules) ? data.rules : [];
      if(adminAxiomRulesPath) adminAxiomRulesPath.textContent = data.path ? 'Config: '+data.path : '';
      renderAxiomRules();
    } catch(e){ setStatus(adminAxiomRulesStatus, String(e), 'error'); }
  }

  if(axiomRuleAddBtn){ axiomRuleAddBtn.addEventListener('click', ()=>{
    const name  = getVal(axiomRuleName);
    const match = getVal(axiomRuleMatch);
    if(!name || !match){ setStatus(adminAxiomRulesStatus, 'Rule Name and Product Match Contains are required.', 'error'); return; }
    _axiomRules.push({
      name,
      match_contains: match.split(',').map(s=>s.trim()).filter(Boolean),
      target_field:   'product_flavor',
      raw_field:      'productFlavor',
      config_path:    'configuration',
      extractor:      'product_flavor',
      enabled:        !!axiomRuleEnabled?.checked
    });
    setVal(axiomRuleName,''); setVal(axiomRuleMatch,'');
    if(axiomRuleEnabled) axiomRuleEnabled.checked = true;
    renderAxiomRules();
    setStatus(adminAxiomRulesStatus, 'Rule added. Click Save All Rules to persist.', 'success');
  }); }

  if(axiomRuleSaveBtn){ axiomRuleSaveBtn.addEventListener('click', async ()=>{
    try{
      const resp = await fetch('/admin/axiom_enrichment_rules',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rules:_axiomRules})});
      const ct = resp.headers.get('content-type')||'';
      if(!ct.includes('application/json')){
        setStatus(adminAxiomRulesStatus, 'Server returned non-JSON (session expired? try refreshing).', 'error');
        return;
      }
      const data = await resp.json();
      if(!data.success) throw new Error(data.message||'Save failed');
      _axiomRules = Array.isArray(data.rules) ? data.rules : _axiomRules;
      renderAxiomRules();
      setStatus(adminAxiomRulesStatus, data.message||'Rules saved successfully.', 'success');
    } catch(e){ setStatus(adminAxiomRulesStatus, String(e), 'error'); }
  }); }



  /* -- load SP for selected target --------------------------- */

  async function loadTargetSp() {
    const target_name = getVal(adminTargetMilestoneResyncSelect);
    if (!target_name) { setStatus(adminStatusMilestoneResync, 'Select a target first.', 'error'); return ''; }
    try {
      const resp = await fetch('/admin/get_target_sp', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_name })
      });
      const data = await resp.json();
      if (data.success) {
        setVal(adminMilestoneSpName, data.sp_name || '');
        setStatus(adminStatusMilestoneResync, `Loaded SP for ${target_name}.`, 'success');
        return data.sp_name || '';
      }
      setStatus(adminStatusMilestoneResync, data.message || 'Failed to load SP.', 'error');
      return '';
    } catch (e) {
      setStatus(adminStatusMilestoneResync, 'Network error loading SP.', 'error');
      return '';
    }
  }

  if (adminLoadSpBtn)  adminLoadSpBtn.addEventListener('click', loadTargetSp);

  if (adminTargetMilestoneResyncSelect)
    adminTargetMilestoneResyncSelect.addEventListener('change', () => setVal(adminMilestoneSpName, ''));

  /* -- update SP name ----------------------------------------- */
  if (adminUpdateSpBtn) {
    adminUpdateSpBtn.addEventListener('click', async () => {
      const target_name = getVal(adminTargetMilestoneResyncSelect);
      const sp_name     = getVal(adminMilestoneSpName);
      if (!target_name || !sp_name) {
        setStatus(adminStatusMilestoneResync, 'Select a target and enter SP name.', 'error'); return;
      }
      try {
        const resp = await fetch('/admin/update_target_sp', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_name, sp_name })
        });
        const data = await resp.json();
        setStatus(adminStatusMilestoneResync, data.message || (data.success ? 'SP updated.' : 'Failed.'), data.success ? 'success' : 'error');
      } catch (e) {
        setStatus(adminStatusMilestoneResync, 'Network error updating SP.', 'error');
      }
    });
  }

  /* -- fetch SP milestones (Add-target panel) ----------------- */
  if (adminFetchSpMilestonesBtn) {
    adminFetchSpMilestonesBtn.addEventListener('click', async () => {
      const sp_name = getVal(adminSpNameAdd);
      if (!sp_name) { setStatus(adminStatusAdd, 'SP Name is required.', 'error'); return; }
      setStatus(adminStatusAdd, `Fetching milestones for ${sp_name}...`, 'info');
      if (adminSpMilestonesPreview)  adminSpMilestonesPreview.textContent  = '';
      if (adminRawMilestonesPreview) adminRawMilestonesPreview.textContent = '';
      adminFetchSpMilestonesBtn.disabled = true;
      try {
        const resp = await fetch('/admin/fetch_sp_milestones', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ sp_name })
        });
        const data = await resp.json();
        if (data.success) {
          const k = data.milestones || {};
          const parts = [];
          if (k.ES)  parts.push(`ES: ${k.ES}`);
          if (k.FC)  parts.push(`FC: ${k.FC}`);
          if (k.CS)  parts.push(`CS: ${k.CS}`);
          if (k.CS1) parts.push(`CS1: ${k.CS1}`);
          if (adminSpMilestonesPreview)  adminSpMilestonesPreview.textContent  = parts.join(' | ') || 'No dates found.';
          if (adminRawMilestonesPreview) adminRawMilestonesPreview.textContent = data.raw || '';
                    openMilestonePicker(k);
          // In Add-target flow, target doesn't exist in DB yet.
          // Store milestones on the button so the Add Target submit can use them.
          // Clear any stale target_name so Save button shows correct hint.
          if (msSaveBtn) {
            msSaveBtn.dataset.targetName = '';
            msSaveBtn.dataset.spName     = sp_name;
            msSaveBtn.dataset.milestones = JSON.stringify(k);
            msSaveBtn.dataset.addFlow    = '1';  // flag: opened from Add flow
          }
          setStatus(adminStatusAdd, 'Milestones fetched. They will be saved when you submit the target.', 'success');
        } else {
          setStatus(adminStatusAdd, data.message || 'Failed to fetch milestones.', 'error');
        }
      } catch (e) {
        setStatus(adminStatusAdd, 'Network error fetching milestones.', 'error');
      } finally {
        adminFetchSpMilestonesBtn.disabled = false;
      }
    });
  }

  /* -- submit new target -------------------------------------- */
  if (submitNewTargetBtn) {
    submitNewTargetBtn.addEventListener('click', async () => {
      const bu_key = getVal(adminBuAdd);
      const auto   = isAutoBU(bu_key);
      const wbc    = isWbcBU(bu_key);
      const mobile = isMobileBU(bu_key);

      /* resolve target_name from the right source */
      const target_name = getVal(adminTargetAdd);   // always kept in sync by builders

      const target_display_name = getVal(adminTargetDisplayAdd);
      const chip_name           = getVal(adminChipNameAdd);
      const sp_name             = getVal(adminSpNameAdd);
      const excel_path          = getVal(adminPathAdd);
      const unique_cr_path      = getVal(adminUniqueCrPathAdd) || null;

      /* Mobile-specific */
      const mobile_product_family = getVal(adminMobileProductFamily) || 'VT';

      /* AUTO-specific */
      const generation = getVal(adminAutoGen);
      const program    = getVal(adminAutoProgram);
      const family     = getVal(adminAutoFamily);
      const category   = getVal(adminAutoCategory);
            const sp_label   = getVal(adminAutoSp);

      /* WBC-specific */
      const wbc_target = getVal(adminWbcTarget);
      const wbc_sp     = getVal(adminWbcSp);

      /* -- validation -- */
      if (!bu_key)           { setStatus(adminStatusAdd, 'Please select a Business Unit.', 'error'); return; }
      if (!target_name)      { setStatus(adminStatusAdd, 'Target key is empty - fill in the required fields.', 'error'); return; }
      if (!target_display_name) { setStatus(adminStatusAdd, 'Please enter a display name.', 'error'); return; }

      if (adminUniqueCrOnlyMode) {
        if (!unique_cr_path) { setStatus(adminStatusAdd, 'Please enter the Unique CR path.', 'error'); return; }
      } else {
        if (!chip_name)  { setStatus(adminStatusAdd, 'Please enter the CHIP name.', 'error'); return; }
        if (!sp_name)    { setStatus(adminStatusAdd, 'Please enter the SP name.', 'error'); return; }
        if (!excel_path) { setStatus(adminStatusAdd, 'Please enter the Excel folder/file path.', 'error'); return; }
      }

            if (auto) {
                if (!generation) { setStatus(adminStatusAdd, 'Generation is required for Automotive.', 'error'); return; }
        if (!program)    { setStatus(adminStatusAdd, 'Program is required for Automotive.', 'error'); return; }
                // Family, Category and SP Label are optional for hierarchy-level dashboards.
        // When SP Label is blank, the record is created as an overall/family/category dashboard.
      }

      if (wbc) {
        if (!wbc_target) { setStatus(adminStatusAdd, 'Target name is required for WBC / Auto-Telematics.', 'error'); return; }
        // SP Label is optional for WBC / Auto-Telematics; blank means target-level overall dashboard.
      }

      if (mobile) {
        if (!['VT', 'PT', 'PT-AU'].includes(mobile_product_family)) {
          setStatus(adminStatusAdd, 'Please select a valid Mobile product family.', 'error'); return;
        }
      }

      /* -- build payload -- */
      const payload = {
        bu:                   bu_key,
        bu_key,
        target_name,
        target_key:           target_name,
        db_name:              target_name,
        target_display_name,
        target_display:       target_display_name,
        display_name:         target_display_name,
        chip_name,
        sp_name,
        excel_path,
        unique_cr_path,
        unique_cr_only: adminUniqueCrOnlyMode,
        path:                 excel_path,

                /* AUTO fields */
        gen:          generation,
        auto_project: program,
        family,
        category,
        cp:           '',
        mobile_product_family,
        auto_metadata: {
          gen:      generation,
          program,
          family,
          category,
          sp_label,   /* SP level (optional) */
          cp:       ''
        },

        /* WBC fields */
        wbc_metadata: {
          target:   wbc_target,
          sp_label: wbc_sp
        }
      };

      setStatus(adminStatusAdd, `Adding ${target_display_name}...`, 'info');
      submitNewTargetBtn.disabled = true;

      try {
                const resp = await fetch('/admin/add_target', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        let data;
        try { data = await resp.json(); }
        catch (_) {
          const txt = await resp.text().catch(() => '');
          throw new Error(`Server error ${resp.status}: ${txt.substring(0,200)}`);
        }
        if (data.success) {
          setStatus(adminStatusAdd, data.message || 'Target added and ingested.', 'success');
          window.location.reload();
        } else {
          setStatus(adminStatusAdd, data.message || 'Failed to add target.', 'error');
        }
      } catch (e) {
        setStatus(adminStatusAdd, 'Network error while adding target.', 'error');
      } finally {
        submitNewTargetBtn.disabled = false;
      }
    });
  }

  /* -- save milestones (picker modal) ------------------------ */
  if (msSaveBtn) {
        msSaveBtn.addEventListener('click', async () => {
      const target_name = msSaveBtn.dataset.targetName
        || getVal(adminTargetMilestoneResyncSelect)
        || getVal(adminTargetUpdateSelect)
        || getVal(adminTargetRemoveSelect);
      const sp_name = msSaveBtn.dataset.spName
        || getVal(adminMilestoneSpName)
        || getVal(adminSpNameAdd);

      let stored = {};
      try { stored = msSaveBtn.dataset.milestones ? JSON.parse(msSaveBtn.dataset.milestones) : {}; } catch (_) {}

      const milestones = {
        ES:  msUseEs?.checked  ? getVal(msEs)  : (stored.ES  || ''),
        FC:  msUseFc?.checked  ? getVal(msFc)  : (stored.FC  || ''),
        CS:  msUseCs?.checked  ? getVal(msCs)  : (stored.CS  || ''),
        CS1: msUseCs1?.checked ? getVal(msCs1) : (stored.CS1 || stored.CS || '')
      };

      // Add-target flow: target not in DB yet -- just store dates and close
      if (msSaveBtn.dataset.addFlow === '1' || !target_name) {
        // Persist chosen dates back into dataset so Add Target submit can read them
        msSaveBtn.dataset.milestones = JSON.stringify(milestones);
        delete msSaveBtn.dataset.addFlow;
        closeMilestonePicker();
        setStatus(adminStatusAdd, 'Milestone dates applied. Submit the form to save.', 'success');
        return;
      }

      const payload = { target_name, sp_name, milestones };

      const msModalStatus = $('msModalStatus');
      const showMsg = (msg, ok) => {
        if (!msModalStatus) return;
        msModalStatus.textContent  = msg;
        msModalStatus.style.display    = 'block';
        msModalStatus.style.background = ok ? '#dcfce7' : '#fee2e2';
        msModalStatus.style.color      = ok ? '#166534' : '#dc2626';
      };
      const hideMsg = () => { if (msModalStatus) { msModalStatus.style.display = 'none'; msModalStatus.textContent = ''; } };

      try {
        msSaveBtn.disabled = true;
        msSaveBtn.textContent = 'Saving...';
        setStatus(adminStatusMilestoneResync, `Saving milestones for ${target_name}...`, 'info');

        const resp = await fetch('/admin/save_milestones', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await resp.json();

        if (data.success) {
          msSaveBtn.textContent = 'Saved!';
          msSaveBtn.style.background = 'linear-gradient(135deg,#16a34a,#22c55e)';
          showMsg('Milestones saved successfully!', true);
          setStatus(adminStatusMilestoneResync, data.message || 'Milestones saved.', 'success');
          setTimeout(() => { closeMilestonePicker(); hideMsg(); msSaveBtn.textContent = 'Save Milestones'; msSaveBtn.style.background = ''; msSaveBtn.disabled = false; }, 1500);
        } else {
          const msg = data.message || 'Failed to save milestones.';
          msSaveBtn.textContent = 'Failed';
          msSaveBtn.style.background = 'linear-gradient(135deg,#dc2626,#ef4444)';
          showMsg(msg, false);
          setStatus(adminStatusMilestoneResync, msg, 'error');
          setTimeout(() => { msSaveBtn.textContent = 'Save Milestones'; msSaveBtn.style.background = ''; msSaveBtn.disabled = false; }, 2500);
        }
      } catch (e) {
        const msg = 'Network error: ' + e.message;
        msSaveBtn.textContent = 'Error';
        msSaveBtn.style.background = 'linear-gradient(135deg,#dc2626,#ef4444)';
        showMsg(msg, false);
        setStatus(adminStatusMilestoneResync, msg, 'error');
        setTimeout(() => { msSaveBtn.textContent = 'Save Milestones'; msSaveBtn.style.background = ''; msSaveBtn.disabled = false; }, 2500);
      }
    });
  }

  /* -- remove target ------------------------------------------ */
  if (removeTargetBtn) {
        removeTargetBtn.addEventListener('click', async () => {
      const target_name = getVal(adminTargetRemoveSelect);
      if (!target_name) { setStatus(adminStatusUpdate, 'Please select a target to remove.', 'error'); return; }
      if (!confirm(`Remove target '${target_name}' from DB?`)) return;
      try {
        const resp = await fetch('/admin/remove_target', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_name })
        });
        const data = await resp.json();
        if (data.success) { setStatus(adminStatusUpdate, data.message || 'Target removed.', 'success'); window.location.reload(); }
        else setStatus(adminStatusUpdate, data.message || 'Failed to remove target.', 'error');
      } catch (e) {
        setStatus(adminStatusUpdate, 'Network error removing target.', 'error');
      }
    });
  }

  /* Active / Inactive toggle */
  async function _doToggle(is_active) {
    const target_name = getVal(adminTargetRemoveSelect);
    if (!target_name) { setStatus(adminStatusToggle, 'Please select a target first.', 'error'); return; }
    const label = is_active ? 'Active' : 'Inactive';
    if (!confirm(`Set "${target_name}" to ${label}?`)) return;
    try {
      const resp = await fetch('/admin/toggle_target_active', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_name, is_active })
      });
      const data = await resp.json();
      if (data.success) {
        setStatus(adminStatusToggle, data.message || `Set to ${label}.`, 'success');
        setTimeout(() => window.location.reload(), 1200);
      } else {
        setStatus(adminStatusToggle, data.message || 'Failed.', 'error');
      }
    } catch (e) {
      setStatus(adminStatusToggle, 'Network error.', 'error');
    }
  }
  if (setActiveBtn)   setActiveBtn.addEventListener('click',   () => _doToggle(1));
  if (setInactiveBtn) setInactiveBtn.addEventListener('click', () => _doToggle(0));

  /* -- fetch milestones (Milestone panel) --------------------- */
  if (resyncMilestonesBtn) {
    resyncMilestonesBtn.addEventListener('click', async () => {
      const target_name = getVal(adminTargetMilestoneResyncSelect);
      const sp_name     = getVal(adminMilestoneSpName);
      if (!target_name) { setStatus(adminStatusMilestoneResync, 'Please select a target.', 'error'); return; }
      if (!sp_name)     { setStatus(adminStatusMilestoneResync, 'Please enter or load an SP name.', 'error'); return; }

      setStatus(adminStatusMilestoneResync, `Fetching milestones for ${target_name}...`, 'info');
      resyncMilestonesBtn.disabled = true;
      try {
        const resp = await fetch('/admin/fetch_sp_milestones', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_name, sp_name })
        });
        const data = await resp.json();
        if (!data.success) { setStatus(adminStatusMilestoneResync, data.message || 'Failed.', 'error'); return; }

        const k = data.milestones || {};
        if (adminSpMilestonesPreview)
          adminSpMilestonesPreview.textContent = `Target: ${target_name} | SP: ${sp_name} | ES: ${k.ES||''} | FC: ${k.FC||''} | CS: ${k.CS||''} | CS1: ${k.CS1||''}`;
        if (adminRawMilestonesPreview)
          adminRawMilestonesPreview.textContent = data.raw || '';

        openMilestonePicker(k);
        if (msSaveBtn) {
          msSaveBtn.dataset.targetName = target_name;
          msSaveBtn.dataset.spName     = sp_name;
          msSaveBtn.dataset.milestones = JSON.stringify(k);
        }
        setStatus(adminStatusMilestoneResync, 'Milestones fetched. Review and save.', 'success');
      } catch (e) {
        setStatus(adminStatusMilestoneResync, 'Network error resyncing milestones.', 'error');
      } finally {
        resyncMilestonesBtn.disabled = false;
      }
    });
  }

  /* -- update existing target data --------------------------- */
  if (updateExistingTargetDataBtn) {
    updateExistingTargetDataBtn.addEventListener('click', async () => {
      const target_name = getVal(adminTargetUpdateSelect);
      if (!target_name) { setStatus(adminStatusUpdate, 'Please select a target.', 'error'); return; }
      setStatus(adminStatusUpdate, `Updating data for ${target_name}...`, 'info');
      updateExistingTargetDataBtn.disabled = true;
      try {
        const resp = await fetch('/admin/update_target', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ target_name })
        });
        const data = await resp.json();
        setStatus(adminStatusUpdate, data.message || (data.success ? 'Update complete.' : 'Update failed.'), data.success ? 'success' : 'error');
      } catch (e) {
        setStatus(adminStatusUpdate, 'Network error updating target.', 'error');
      } finally {
        updateExistingTargetDataBtn.disabled = false;
      }
    });
  }

  /* -- sync config from DB ------------------------------------ */
  if (syncDbBtn) {
    syncDbBtn.addEventListener('click', async () => {
      setStatus(adminStatusUpdate, 'Syncing configuration from Database...', 'info');
      syncDbBtn.disabled = true;
      try {
        const resp = await fetch('/admin/sync_db', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const data = await resp.json();
        if (data.success) { setStatus(adminStatusUpdate, data.message || 'Sync complete.', 'success'); window.location.reload(); }
        else setStatus(adminStatusUpdate, data.message || 'Sync failed.', 'error');
      } catch (e) {
        setStatus(adminStatusUpdate, 'Network error syncing DB.', 'error');
      } finally {
        syncDbBtn.disabled = false;
      }
    });
  }

    /* -- init --------------------------------------------------- */
  populateDropdowns();
  toggleBuFields();
  // Ensure no panel is shown on page load (panels are hidden via CSS)
  showAdminPanel(null, null);
});
