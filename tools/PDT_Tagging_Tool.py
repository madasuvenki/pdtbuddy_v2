from __future__ import print_function
import io, os, re, sys, threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext

try:
    import PDT_StatsQueryCRs as crQ
    import PDT_StatsConstants as const
    PDT_LIBS_AVAILABLE = True
except ImportError:
    PDT_LIBS_AVAILABLE = False

CLR_BG         = "#1a2340"
CLR_PANEL      = "#1e2d4a"
CLR_ACCENT     = "#e05a1e"
CLR_ACCENT2    = "#2979ff"
CLR_TEXT       = "#e8eaf6"
CLR_SUBTEXT    = "#90a4ae"
CLR_ROW_ODD    = "#1e2d4a"
CLR_ROW_EVEN   = "#243352"
CLR_HEADER_BG  = "#0d1b2e"
CLR_BTN_TAG    = "#e05a1e"
CLR_BTN_OK     = "#2979ff"
CLR_BTN_CANCEL = "#455a64"
CLR_SUCCESS    = "#43a047"
CLR_ERROR      = "#e53935"

FONT_TITLE  = ("Segoe UI", 14, "bold")
FONT_HEADER = ("Segoe UI", 10, "bold")
FONT_BODY   = ("Segoe UI", 10)
FONT_SMALL  = ("Segoe UI",  9)
FONT_MONO   = ("Consolas",  9)

def _validate_cr_number(cr_str):
    cr = cr_str.strip().upper().replace("CR","").replace(" ","")
    if cr.isdigit() and 6 <= len(cr) <= 7:
        return cr
    return ""

def _parse_cr_list(raw_text):
    result = []
    for tok in re.split(r"[\s,;]+", raw_text):
        cr = _validate_cr_number(tok)
        if cr and cr not in result:
            result.append(cr)
    return result

def _validate_tag(tag):
    t = tag.strip()
    return bool(t) and t.replace("_","").isalnum() and len(t) >= 3


def _fetch_existing_pdt_tags(cr_list):
    """Return sorted unique tags containing PDT already on any CR in cr_list."""
    if not PDT_LIBS_AVAILABLE:
        return ["PDT_P1", "PDT_AU_STAB"]   # demo simulation
    try:
        if const.apiOrbit is None:
            const.apiOrbit = crQ.orbitObjectCreation()
        tags_set = set()
        for cr in ["CR" + c for c in cr_list]:
            info = crQ.getCRInfo(cr)
            if info and isinstance(info.get("tags"), list):
                for t in info["tags"]:
                    if "PDT" in t.upper():
                        tags_set.add(t)
        return sorted(tags_set)
    except Exception:
        return []


def _execute_tagging(cr_list, tag_name, remove_tags, log_cb, done_cb):
    try:
        add_part    = tag_name if tag_name else "(none)"
        remove_part = ", ".join(remove_tags) if remove_tags else "(none)"
        header = "[INFO] PDT Tag Operation started\n[INFO] Add tag    : {}\n[INFO] Remove tags: {}\n[INFO] Total CRs  : {}\n\n"
        log_cb(header.format(add_part, remove_part, len(cr_list)))
        if not PDT_LIBS_AVAILABLE:
            import time
            for i, cr in enumerate(cr_list, 1):
                parts = []
                if tag_name:    parts.append("+ {}".format(tag_name))
                if remove_tags: parts.append("- {}".format(", ".join(remove_tags)))
                log_cb("  [{}/{}] CR{} -> {} [SIMULATED]\n".format(i, len(cr_list), cr, "  ".join(parts) or "no-op"))
            log_cb("\n[SUCCESS] Simulation complete. {} CR(s) updated.\n".format(len(cr_list)))
            done_cb(success=True, count=len(cr_list))
            return
        if const.apiOrbit is None:
            log_cb("[INFO] Initialising Orbit API...\n")
            const.apiOrbit = crQ.orbitObjectCreation()
            log_cb("[INFO] Orbit API ready.\n")
        cr_str_list = ["CR" + cr for cr in cr_list]
        old_stdout = sys.stdout
        sys.stdout = buf = io.StringIO()
        try:
            crQ.updateTagsToCRs(
                cr_str_list,
                crTagList=tag_name if tag_name else None,
                removeTagsList=remove_tags if remove_tags else None
            )
        finally:
            sys.stdout = old_stdout
            captured = buf.getvalue()
        for line in captured.splitlines():
            log_cb(line + "\n")
        ok = captured.count("Added tag(s) successfully") + captured.count("Removed tag(s) successfully")
        log_cb("\n[DONE] {}/{} CR(s) updated successfully.\n".format(ok, len(cr_list)))
        done_cb(success=True, count=ok)
    except Exception as exc:
        log_cb("\n[ERROR] {}\n".format(exc))
        done_cb(success=False, count=0)

class TagProgressDialog(tk.Toplevel):
    def __init__(self, parent, cr_list, tag_name, remove_tags=None):
        super().__init__(parent)
        add_lbl = tag_name if tag_name else "none"
        self.title("PDT Tag Op - Add:{} / Remove:{}".format(add_lbl, ",".join(remove_tags) if remove_tags else "none"))
        self.configure(bg=CLR_BG)
        self.resizable(True, True)
        self._cr_list = cr_list; self._tag_name = tag_name; self._remove_tags = remove_tags or []
        self._build_ui(); self._center(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)
        self._start()

    def _center(self, p):
        self.update_idletasks()
        cx = p.winfo_rootx() + p.winfo_width()//2
        cy = p.winfo_rooty() + p.winfo_height()//2
        self.geometry("660x500+{}+{}".format(cx-330, cy-250))

    def _build_ui(self):
        tk.Frame(self, bg=CLR_ACCENT, height=6).pack(fill="x")
        hdr = tk.Frame(self, bg=CLR_HEADER_BG, pady=10); hdr.pack(fill="x")
        add_txt = "Add: {}".format(self._tag_name) if self._tag_name else ""
        rem_txt = "Remove: {}".format(", ".join(self._remove_tags)) if self._remove_tags else ""
        op_txt  = "  |  ".join(filter(None, [add_txt, rem_txt])) or "No-op"
        tk.Label(hdr, text="Updating {} CR(s)  -  {}".format(len(self._cr_list), op_txt),
                 font=FONT_TITLE, bg=CLR_HEADER_BG, fg=CLR_TEXT).pack(side="left", padx=16)
        pf = tk.Frame(self, bg=CLR_BG, padx=16, pady=8); pf.pack(fill="x")
        self._pb = ttk.Progressbar(pf, mode="indeterminate", length=600)
        self._pb.pack(fill="x"); self._pb.start(12)
        self._sv = tk.StringVar(value="Connecting to Orbit API...")
        tk.Label(self, textvariable=self._sv, font=FONT_BODY, bg=CLR_BG, fg=CLR_SUBTEXT).pack(padx=16, anchor="w")
        lf = tk.Frame(self, bg=CLR_BG, padx=16, pady=4); lf.pack(fill="both", expand=True)
        tk.Label(lf, text="Execution Log", font=FONT_HEADER, bg=CLR_BG, fg=CLR_SUBTEXT).pack(anchor="w")
        self._log = scrolledtext.ScrolledText(lf, font=FONT_MONO, bg=CLR_HEADER_BG, fg=CLR_TEXT,
            insertbackground=CLR_TEXT, relief="flat", state="disabled", wrap="word", height=16)
        self._log.pack(fill="both", expand=True, pady=(4,0))
        bf = tk.Frame(self, bg=CLR_BG, pady=10); bf.pack(fill="x", padx=16)
        self._close_btn = tk.Button(bf, text="Close", font=FONT_BODY, bg=CLR_BTN_CANCEL, fg=CLR_TEXT,
            relief="flat", activebackground="#607d8b", activeforeground="white",
            cursor="hand2", padx=14, pady=6, state="disabled", command=self.destroy)
        self._close_btn.pack(side="right")

    def _log_append(self, text):
        def _do():
            self._log.configure(state="normal")
            self._log.insert("end", text)
            self._log.see("end")
            self._log.configure(state="disabled")
        self.after(0, _do)

    def _on_done(self, success, count):
        def _do():
            self._pb.stop(); self._pb.configure(mode="determinate", value=100)
            if success:
                self._sv.set("Done!  {}/{} CR(s) updated successfully.".format(count, len(self._cr_list)))
            else:
                self._sv.set("Tagging encountered errors - see log above.")
            self._close_btn.configure(state="normal")
            self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(0, _do)

    def _start(self):
        threading.Thread(target=_execute_tagging,
            args=(self._cr_list, self._tag_name, self._remove_tags, self._log_append, self._on_done),
            daemon=True).start()

class TagNameDialog(tk.Toplevel):
    """
    Step 2 dialog - three sections:
      A) ADD TAG   : free-form entry + quick-select buttons (optional)
      B) EXISTING  : PDT tags already on these CRs shown as clickable chips
                     (fetched in background; user can click to pre-fill Remove)
      C) REMOVE TAGS: free-form entry + chips from existing PDT tags (optional)
    result = (add_tag_str, remove_tags_list)  or  None if cancelled
    """
    QUICK_TAGS = ["PDT_P0","PDT_P1","PDT_P2","PDT_AU_STAB","PDT_APPS_STAB","PDT_REGRESSION"]

    def __init__(self, parent, selected_crs):
        super().__init__(parent)
        self.title("PDT Tag Operation")
        self.configure(bg=CLR_BG); self.resizable(True, True)
        self.result = None
        self._crs = selected_crs
        self._existing_pdt_tags = []   # filled by background fetch
        self._remove_chip_btns  = {}   # tag -> Button widget
        self._build_ui()
        self.grab_set(); self.focus_force()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._center(parent)
        # Fetch existing PDT tags in background so UI stays responsive
        threading.Thread(target=self._fetch_tags_bg, daemon=True).start()

    def _center(self, p):
        self.update_idletasks()
        cx = p.winfo_rootx() + p.winfo_width()//2
        cy = p.winfo_rooty() + p.winfo_height()//2
        self.geometry("620x640+{}+{}".format(cx-310, cy-320))

    # - UI -
    def _build_ui(self):
        tk.Frame(self, bg=CLR_ACCENT, height=6).pack(fill="x")

        # Header
        hdr = tk.Frame(self, bg=CLR_HEADER_BG, pady=12); hdr.pack(fill="x")
        tk.Label(hdr, text="PDT Tag Operation", font=FONT_TITLE, bg=CLR_HEADER_BG, fg=CLR_TEXT).pack(side="left", padx=16)

        # CR summary
        sf = tk.Frame(self, bg=CLR_PANEL, padx=16, pady=8)
        sf.pack(fill="x", padx=12, pady=(8,0))
        tk.Label(sf, text="Selected CRs  ({})".format(len(self._crs)),
                 font=FONT_HEADER, bg=CLR_PANEL, fg=CLR_SUBTEXT).pack(anchor="w")
        preview = ", ".join(["CR"+c for c in self._crs[:12]])
        if len(self._crs) > 12: preview += "  ... +{} more".format(len(self._crs)-12)
        tk.Label(sf, text=preview, font=FONT_SMALL, bg=CLR_PANEL, fg=CLR_TEXT,
                 wraplength=560, justify="left").pack(anchor="w", pady=(2,0))

        # - Section A: ADD TAG -
        self._build_section_add()

        # - Section B: EXISTING PDT TAGS -
        self._build_section_existing()

        # - Section C: REMOVE TAGS -
        self._build_section_remove()

        # Validation message
        self._msg_var = tk.StringVar()
        tk.Label(self, textvariable=self._msg_var, font=FONT_SMALL,
                 bg=CLR_BG, fg=CLR_ERROR).pack(padx=16, anchor="w", pady=(4,0))

        # Buttons
        bf = tk.Frame(self, bg=CLR_BG, pady=12); bf.pack(fill="x", padx=16)
        tk.Button(bf, text="Cancel", font=FONT_BODY, bg=CLR_BTN_CANCEL, fg=CLR_TEXT, relief="flat",
                  activebackground="#607d8b", activeforeground="white", cursor="hand2", padx=14, pady=6,
                  command=self._cancel).pack(side="right", padx=(8,0))
        tk.Button(bf, text="Apply", font=FONT_BODY, bg=CLR_BTN_TAG, fg="white", relief="flat",
                  activebackground="#bf360c", activeforeground="white", cursor="hand2", padx=14, pady=6,
                  command=self._ok).pack(side="right")

    def _section_header(self, parent, letter, title, subtitle=""):
        row = tk.Frame(parent, bg=CLR_BG); row.pack(fill="x", padx=12, pady=(10,2))
        tk.Label(row, text=letter, font=("Segoe UI",10,"bold"), bg=CLR_ACCENT, fg="white",
                 width=2, padx=4, pady=2).pack(side="left")
        tk.Label(row, text="  "+title, font=FONT_HEADER, bg=CLR_BG, fg=CLR_TEXT).pack(side="left")
        if subtitle:
            tk.Label(row, text=subtitle, font=FONT_SMALL, bg=CLR_BG, fg=CLR_SUBTEXT).pack(side="left", padx=(8,0))

    # - Section A -
    def _build_section_add(self):
        self._section_header(self, "A", "Add Tag", "(optional - leave blank to skip)")
        ef = tk.Frame(self, bg=CLR_BG, padx=16); ef.pack(fill="x")
        tk.Label(ef, text="Type any tag name or pick a quick-select below:",
                 font=FONT_SMALL, bg=CLR_BG, fg=CLR_SUBTEXT).pack(anchor="w", pady=(0,4))
        self._add_var = tk.StringVar()
        self._add_entry = tk.Entry(ef, textvariable=self._add_var, font=("Segoe UI",12),
                       bg=CLR_PANEL, fg=CLR_TEXT, insertbackground=CLR_TEXT, relief="flat",
                       highlightthickness=1, highlightcolor=CLR_ACCENT2, highlightbackground=CLR_SUBTEXT)
        self._add_entry.pack(fill="x", ipady=6)
        self._add_entry.focus_set()
        self._add_entry.bind("<Return>", lambda e: self._ok())
        # Quick-select chips (wrap across multiple rows if needed)
        qf_outer = tk.Frame(self, bg=CLR_BG, padx=16); qf_outer.pack(fill="x", pady=(4,0))
        tk.Label(qf_outer, text="Quick add:", font=FONT_SMALL, bg=CLR_BG, fg=CLR_SUBTEXT).pack(anchor="w")
        qf = tk.Frame(qf_outer, bg=CLR_BG); qf.pack(fill="x")
        for tag in self.QUICK_TAGS:
            tk.Button(qf, text=tag, font=FONT_SMALL, bg=CLR_PANEL, fg=CLR_TEXT, relief="flat",
                      activebackground=CLR_ACCENT2, activeforeground="white", cursor="hand2", padx=6, pady=2,
                      command=lambda t=tag: self._add_var.set(t)).pack(side="left", padx=(0,6), pady=2)

    # - Section B -
    def _build_section_existing(self):
        self._section_header(self, "B", "Existing PDT Tags on selected CRs",
                             "(click a tag to add it to Remove list)")
        self._exist_frame = tk.Frame(self, bg=CLR_BG, padx=16); self._exist_frame.pack(fill="x")
        self._exist_status = tk.Label(self._exist_frame, text="Fetching...",
                                      font=FONT_SMALL, bg=CLR_BG, fg=CLR_SUBTEXT)
        self._exist_status.pack(anchor="w")

    def _fetch_tags_bg(self):
        """Background thread: fetch existing PDT tags then update UI."""
        tags = _fetch_existing_pdt_tags(self._crs)
        self.after(0, lambda: self._render_existing_tags(tags))

    def _render_existing_tags(self, tags):
        self._existing_pdt_tags = tags
        self._exist_status.destroy()
        if not tags:
            tk.Label(self._exist_frame, text="No existing PDT tags found on selected CRs.",
                     font=FONT_SMALL, bg=CLR_BG, fg=CLR_SUBTEXT).pack(anchor="w")
            return
        chip_row = tk.Frame(self._exist_frame, bg=CLR_BG); chip_row.pack(fill="x", pady=(2,0))
        for tag in tags:
            btn = tk.Button(chip_row, text=tag, font=FONT_SMALL,
                            bg="#1a3a2a", fg="#80cfa9", relief="flat",
                            activebackground="#2e7d52", activeforeground="white",
                            cursor="hand2", padx=7, pady=3,
                            command=lambda t=tag: self._add_to_remove(t))
            btn.pack(side="left", padx=(0,6), pady=2)
        tk.Label(self._exist_frame,
                 text="Click a green tag to add it to the Remove field below",
                 font=FONT_SMALL, bg=CLR_BG, fg=CLR_SUBTEXT).pack(anchor="w", pady=(2,0))
        # Also populate remove chips
        self._refresh_remove_chips()

    # - Section C -
    def _build_section_remove(self):
        self._section_header(self, "C", "Remove Tags", "(optional - leave blank to skip)")
        rf = tk.Frame(self, bg=CLR_BG, padx=16); rf.pack(fill="x")
        tk.Label(rf, text="Type tag(s) to remove (comma-separated) or click existing tags above:",
                 font=FONT_SMALL, bg=CLR_BG, fg=CLR_SUBTEXT).pack(anchor="w", pady=(0,4))
        self._rem_var = tk.StringVar()
        self._rem_entry = tk.Entry(rf, textvariable=self._rem_var, font=("Segoe UI",12),
                       bg=CLR_PANEL, fg=CLR_TEXT, insertbackground=CLR_TEXT, relief="flat",
                       highlightthickness=1, highlightcolor="#e53935", highlightbackground=CLR_SUBTEXT)
        self._rem_entry.pack(fill="x", ipady=6)
        # Chip area for existing PDT tags (populated after fetch)
        self._rem_chip_frame = tk.Frame(self, bg=CLR_BG, padx=16); self._rem_chip_frame.pack(fill="x", pady=(4,0))

    def _add_to_remove(self, tag):
        """Append tag to the remove entry (comma-separated)."""
        current = self._rem_var.get().strip()
        existing = [t.strip() for t in current.split(",") if t.strip()]
        if tag not in existing:
            existing.append(tag)
        self._rem_var.set(", ".join(existing))

    def _refresh_remove_chips(self):
        """Show existing PDT tags as one-click remove chips under Section C."""
        for w in self._rem_chip_frame.winfo_children(): w.destroy()
        if not self._existing_pdt_tags: return
        tk.Label(self._rem_chip_frame, text="Quick remove:", font=FONT_SMALL,
                 bg=CLR_BG, fg=CLR_SUBTEXT).pack(side="left")
        for tag in self._existing_pdt_tags:
            tk.Button(self._rem_chip_frame, text="- "+tag, font=FONT_SMALL,
                      bg="#3a1a1a", fg="#ef9a9a", relief="flat",
                      activebackground="#c62828", activeforeground="white",
                      cursor="hand2", padx=6, pady=2,
                      command=lambda t=tag: self._add_to_remove(t)).pack(side="left", padx=(6,0))

    # - Validation & result -
    def _parse_remove_list(self):
        raw = self._rem_var.get()
        return [t.strip() for t in raw.split(",") if t.strip()]

    def _ok(self):
        add_tag     = self._add_var.get().strip()
        remove_tags = self._parse_remove_list()
        # Validate: at least one action must be specified
        if not add_tag and not remove_tags:
            self._msg_var.set("Specify at least one tag to Add or Remove.")
            return
        # Validate add tag if provided
        if add_tag and not _validate_tag(add_tag):
            self._msg_var.set("Add tag invalid - alphanumeric + underscore, min 3 chars, no spaces.")
            return
        # Validate each remove tag
        for rt in remove_tags:
            if not _validate_tag(rt):
                self._msg_var.set("Remove tag invalid: '{}' - alphanumeric + underscore, min 3 chars.".format(rt))
                return
        self.result = (add_tag, remove_tags)
        self.destroy()

    def _cancel(self):
        self.result = None; self.destroy()

class CRSelectionDialog(tk.Toplevel):
    def __init__(self, parent, prefilled_crs=None):
        super().__init__(parent)
        self.title("PDT Tagging - Select CRs")
        self.configure(bg=CLR_BG); self.resizable(True, True)
        self._parent = parent; self._prefilled = prefilled_crs or []
        self._check_vars = {}; self._browse_crs = []
        self._build_ui(); self._center(parent)
        self.grab_set(); self.focus_force()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        if self._prefilled:
            self._paste_txt.insert("1.0", "\n".join(["CR"+c for c in self._prefilled]))
            self._update_count()

    def _center(self, p):
        self.update_idletasks()
        cx = p.winfo_rootx() + p.winfo_width()//2
        cy = p.winfo_rooty() + p.winfo_height()//2
        self.geometry("780x640+{}+{}".format(cx-390, cy-320))

    def _build_ui(self):
        tk.Frame(self, bg=CLR_ACCENT, height=6).pack(fill="x")
        hdr = tk.Frame(self, bg=CLR_HEADER_BG, pady=12); hdr.pack(fill="x")
        tk.Label(hdr, text="PDT Tagging  -  Select CRs", font=FONT_TITLE, bg=CLR_HEADER_BG, fg=CLR_TEXT).pack(side="left", padx=16)
        tk.Label(hdr, text="Select CRs to tag, then click  Next", font=FONT_SMALL, bg=CLR_HEADER_BG, fg=CLR_SUBTEXT).pack(side="right", padx=16)
        sty = ttk.Style(); sty.theme_use("clam")
        sty.configure("TNotebook", background=CLR_BG, borderwidth=0)
        sty.configure("TNotebook.Tab", background=CLR_PANEL, foreground=CLR_SUBTEXT, padding=[14,6], font=FONT_BODY)
        sty.map("TNotebook.Tab", background=[("selected",CLR_ACCENT)], foreground=[("selected","white")])
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill="both", expand=True, padx=12, pady=8)
        self._t_paste  = tk.Frame(self._nb, bg=CLR_BG)
        self._t_browse = tk.Frame(self._nb, bg=CLR_BG)
        self._nb.add(self._t_paste,  text="  From Filter / Paste  ")
        self._nb.add(self._t_browse, text="  Browse All CRs  ")
        self._build_paste_tab(); self._build_browse_tab()
        bot = tk.Frame(self, bg=CLR_HEADER_BG, pady=10); bot.pack(fill="x", side="bottom")
        self._count_var = tk.StringVar(value="0 CR(s) selected")
        tk.Label(bot, textvariable=self._count_var, font=FONT_BODY, bg=CLR_HEADER_BG, fg=CLR_SUBTEXT).pack(side="left", padx=16)
        tk.Button(bot, text="Cancel", font=FONT_BODY, bg=CLR_BTN_CANCEL, fg=CLR_TEXT, relief="flat",
                  activebackground="#607d8b", activeforeground="white", cursor="hand2", padx=14, pady=6,
                  command=self._cancel).pack(side="right", padx=(8,16))
        tk.Button(bot, text="Next  >", font=FONT_BODY, bg=CLR_BTN_OK, fg="white", relief="flat",
                  activebackground="#1565c0", activeforeground="white", cursor="hand2", padx=14, pady=6,
                  command=self._next).pack(side="right")

    def _build_paste_tab(self):
        f = self._t_paste
        tk.Label(f, text="Paste CR numbers below  (comma, space, or newline separated):",
                 font=FONT_BODY, bg=CLR_BG, fg=CLR_TEXT).pack(anchor="w", padx=12, pady=(10,4))
        self._paste_txt = scrolledtext.ScrolledText(f, font=FONT_MONO, bg=CLR_PANEL, fg=CLR_TEXT,
            insertbackground=CLR_TEXT, relief="flat", height=14, wrap="word")
        self._paste_txt.pack(fill="both", expand=True, padx=12, pady=(0,6))
        self._paste_txt.bind("<KeyRelease>", lambda e: self._update_count())
        br = tk.Frame(f, bg=CLR_BG); br.pack(fill="x", padx=12, pady=(0,8))
        tk.Button(br, text="Import from File", font=FONT_SMALL, bg=CLR_PANEL, fg=CLR_TEXT, relief="flat",
                  activebackground=CLR_ACCENT2, activeforeground="white", cursor="hand2", padx=10, pady=4,
                  command=self._import_file).pack(side="left")
        tk.Button(br, text="Clear", font=FONT_SMALL, bg=CLR_PANEL, fg=CLR_TEXT, relief="flat",
                  activebackground=CLR_BTN_CANCEL, activeforeground="white", cursor="hand2", padx=10, pady=4,
                  command=self._clear_paste).pack(side="left", padx=(8,0))
        self._paste_info = tk.Label(f, text="", font=FONT_SMALL, bg=CLR_BG, fg=CLR_SUBTEXT)
        self._paste_info.pack(anchor="w", padx=12)

    def _clear_paste(self):
        self._paste_txt.delete("1.0","end"); self._update_count()

    def _build_browse_tab(self):
        f = self._t_browse
        tb = tk.Frame(f, bg=CLR_BG); tb.pack(fill="x", padx=12, pady=(10,4))
        tk.Button(tb, text="Load CR File (CSV/TXT)", font=FONT_SMALL, bg=CLR_PANEL, fg=CLR_TEXT, relief="flat",
                  activebackground=CLR_ACCENT2, activeforeground="white", cursor="hand2", padx=10, pady=4,
                  command=self._load_browse).pack(side="left")
        tk.Button(tb, text="Select All", font=FONT_SMALL, bg=CLR_PANEL, fg=CLR_TEXT, relief="flat",
                  cursor="hand2", padx=8, pady=4, command=self._sel_all).pack(side="left", padx=(8,0))
        tk.Button(tb, text="Deselect All", font=FONT_SMALL, bg=CLR_PANEL, fg=CLR_TEXT, relief="flat",
                  cursor="hand2", padx=8, pady=4, command=self._desel_all).pack(side="left", padx=(4,0))
        sf = tk.Frame(f, bg=CLR_BG); sf.pack(fill="x", padx=12, pady=(0,4))
        tk.Label(sf, text="Search:", font=FONT_SMALL, bg=CLR_BG, fg=CLR_SUBTEXT).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace("w", self._on_search)
        tk.Entry(sf, textvariable=self._search_var, font=FONT_BODY, bg=CLR_PANEL, fg=CLR_TEXT,
                 insertbackground=CLR_TEXT, relief="flat", highlightthickness=1,
                 highlightcolor=CLR_ACCENT2, highlightbackground=CLR_SUBTEXT
                 ).pack(side="left", fill="x", expand=True, padx=(6,0), ipady=4)
        lo = tk.Frame(f, bg=CLR_BG); lo.pack(fill="both", expand=True, padx=12, pady=(0,6))
        self._cv = tk.Canvas(lo, bg=CLR_BG, highlightthickness=0)
        vsb = ttk.Scrollbar(lo, orient="vertical", command=self._cv.yview)
        self._cv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y"); self._cv.pack(side="left", fill="both", expand=True)
        self._cbf = tk.Frame(self._cv, bg=CLR_BG)
        self._cw = self._cv.create_window((0,0), window=self._cbf, anchor="nw")
        self._cbf.bind("<Configure>", lambda e: self._cv.configure(scrollregion=self._cv.bbox("all")))
        self._cv.bind("<Configure>", lambda e: self._cv.itemconfig(self._cw, width=e.width))
        self._cv.bind_all("<MouseWheel>", lambda e: self._cv.yview_scroll(-1*(e.delta//120),"units"))
        self._browse_info = tk.Label(f, text="Load a CR file to browse CRs.", font=FONT_SMALL, bg=CLR_BG, fg=CLR_SUBTEXT)
        self._browse_info.pack(anchor="w", padx=12)

    def _load_browse(self):
        path = filedialog.askopenfilename(title="Load CR File",
            filetypes=[("Text/CSV","*.txt *.csv"),("All files","*.*")])
        if not path: return
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh: raw = fh.read()
            crs = _parse_cr_list(raw)
            if not crs:
                messagebox.showwarning("No CRs Found","No valid CR numbers found.", parent=self); return
            self._browse_crs = crs; self._render_checkboxes(crs)
            self._browse_info.configure(text="Loaded {} CR(s) from {}".format(len(crs),os.path.basename(path)), fg=CLR_SUCCESS)
        except Exception as exc: messagebox.showerror("Load Error", str(exc), parent=self)

    def _render_checkboxes(self, crs):
        for w in self._cbf.winfo_children(): w.destroy()
        self._check_vars.clear()
        hdr = tk.Frame(self._cbf, bg=CLR_HEADER_BG); hdr.pack(fill="x")
        tk.Label(hdr, text="  Sel", width=5, font=FONT_HEADER, bg=CLR_HEADER_BG, fg=CLR_SUBTEXT).pack(side="left")
        tk.Label(hdr, text="CR Number", width=14, font=FONT_HEADER, bg=CLR_HEADER_BG, fg=CLR_SUBTEXT, anchor="w").pack(side="left")
        tk.Label(hdr, text="(status loaded on demand)", font=FONT_HEADER, bg=CLR_HEADER_BG, fg=CLR_SUBTEXT, anchor="w").pack(side="left")
        for i, cr in enumerate(crs):
            bg = CLR_ROW_ODD if i%2==0 else CLR_ROW_EVEN
            row = tk.Frame(self._cbf, bg=bg); row.pack(fill="x")
            var = tk.BooleanVar(value=True); self._check_vars[cr] = var
            var.trace("w", lambda *a: self._update_count())
            tk.Checkbutton(row, variable=var, bg=bg, activebackground=bg,
                           selectcolor=CLR_PANEL, cursor="hand2").pack(side="left", padx=(4,0))
            tk.Label(row, text="CR{}".format(cr), width=14, font=FONT_MONO, bg=bg, fg=CLR_TEXT, anchor="w").pack(side="left")
        self._update_count()

    def _on_search(self, *_):
        q = self._search_var.get().strip().upper()
        self._render_checkboxes([c for c in self._browse_crs if q in c or q in "CR{}".format(c)])

    def _sel_all(self):
        for v in self._check_vars.values(): v.set(True)

    def _desel_all(self):
        for v in self._check_vars.values(): v.set(False)

    def _import_file(self):
        path = filedialog.askopenfilename(title="Import CR List",
            filetypes=[("Text/CSV","*.txt *.csv"),("All files","*.*")])
        if not path: return
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh: content = fh.read()
            self._paste_txt.delete("1.0","end"); self._paste_txt.insert("1.0", content)
            self._paste_info.configure(text="Loaded: {}".format(os.path.basename(path)), fg=CLR_SUCCESS)
            self._update_count()
        except Exception as exc: messagebox.showerror("Import Error", str(exc), parent=self)

    def _update_count(self):
        tab = self._nb.index(self._nb.select())
        count = len(_parse_cr_list(self._paste_txt.get("1.0","end"))) if tab==0 else sum(1 for v in self._check_vars.values() if v.get())
        self._count_var.set("{} CR(s) selected".format(count))

    def _get_selected(self):
        tab = self._nb.index(self._nb.select())
        if tab == 0: return _parse_cr_list(self._paste_txt.get("1.0","end"))
        return [cr for cr,v in self._check_vars.items() if v.get()]

    def _next(self):
        selected = self._get_selected()
        if not selected:
            messagebox.showwarning("No CRs Selected","Please select at least one CR.", parent=self); return
        self.withdraw()
        dlg = TagNameDialog(self._parent, selected)
        self._parent.wait_window(dlg)
        if dlg.result is None: self.deiconify(); return
        add_tag, remove_tags = dlg.result
        self.destroy()
        TagProgressDialog(self._parent, selected, add_tag, remove_tags)

    def _cancel(self): self.destroy()

def launch_pdt_tagging_button(parent_frame, cr_list_callback=None,
                               button_text="PDT Tagging", pack_kwargs=None):
    """Create and pack a PDT Tagging button into parent_frame.

    Parameters
    ----------
    parent_frame     : tk widget to place the button in.
    cr_list_callback : optional callable() -> list[str] of digits-only CR numbers.
                       Pre-fills the selection dialog with the current view CRs.
    button_text      : button label.
    pack_kwargs      : dict for button.pack().  Default: side="right".
    Returns  tk.Button
    """
    if pack_kwargs is None:
        pack_kwargs = {"side": "right", "padx": 8, "pady": 4}
    def _click():
        prefilled = []
        if cr_list_callback:
            try: prefilled = cr_list_callback() or []
            except Exception: pass
        root = parent_frame.winfo_toplevel()
        dlg = CRSelectionDialog(root, prefilled_crs=prefilled)
        root.wait_window(dlg)
    btn = tk.Button(parent_frame, text=button_text, font=FONT_BODY,
                    bg=CLR_BTN_TAG, fg="white", relief="flat",
                    activebackground="#bf360c", activeforeground="white",
                    cursor="hand2", padx=14, pady=6, command=_click)
    btn.pack(**pack_kwargs)
    return btn


def _demo():
    root = tk.Tk()
    root.title("PDT Stats Dashboard  -  Demo")
    root.configure(bg=CLR_BG); root.geometry("1100x680")
    topbar = tk.Frame(root, bg=CLR_HEADER_BG, height=52)
    topbar.pack(fill="x"); topbar.pack_propagate(False)
    tk.Label(topbar, text="Top Offenders - Highest JIRA Impact",
             font=FONT_TITLE, bg=CLR_HEADER_BG, fg=CLR_TEXT).pack(side="left", padx=16)
    DEMO_CRS = ["4520954","4529363","4535420","4501785"]
    launch_pdt_tagging_button(topbar, cr_list_callback=lambda: DEMO_CRS,
        button_text="PDT Tagging", pack_kwargs={"side":"right","padx":16,"pady":10})
    cols = ("#","CR ID","Title","ARFA","Subsystem","Functionality","Hits","Age","Status","PDT Tag")
    tf = tk.Frame(root, bg=CLR_BG); tf.pack(fill="both", expand=True, padx=12, pady=8)
    sty = ttk.Style()
    sty.configure("D.Treeview", background=CLR_ROW_ODD, foreground=CLR_TEXT,
                  fieldbackground=CLR_ROW_ODD, rowheight=28, font=FONT_BODY)
    sty.configure("D.Treeview.Heading", background=CLR_HEADER_BG, foreground=CLR_SUBTEXT,
                  font=FONT_HEADER, relief="flat")
    sty.map("D.Treeview", background=[("selected",CLR_ACCENT2)], foreground=[("selected","white")])
    tree = ttk.Treeview(tf, columns=cols, show="headings", style="D.Treeview", height=12)
    for col in cols:
        tree.heading(col, text=col)
        tree.column(col, width=260 if col=="Title" else 100, anchor="w")
    rows = [
        (1,"CR4520954","ACDB: Write response error with address 0x17000...","CPUSS","CPU/CP Debug","CPU/CP Debug",1142,"24d","Open","PDT_P1"),
        (2,"CR4529363","[Mach LA 1.0-r1-00059-STD (N-1)]: CNSS Crash...","Core","WNS","PCIe/WiFi/Coexisting",1152,"24d","Analysis","PDT_P1"),
        (3,"CR4535420","[GT_Mach LA 1.0-r1-00079-STD Al-L0515_mainline_2]...","Core","Core","Core",848,"10d","Analysis","PDT_P1"),
        (4,"CR4501785","MASTER_OEM[CV/PDTM69875][APStability][Q]Crash...","Linux","Kernel","Kernel_Virt",598,"38d","Fix","PDT_P1"),
    ]
    for row in rows:
        tree.insert("","end",values=row,tags=("odd" if row[0]%2 else "even",))
    tree.tag_configure("odd", background=CLR_ROW_ODD)
    tree.tag_configure("even", background=CLR_ROW_EVEN)
    vsb = ttk.Scrollbar(tf, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side="right", fill="y"); tree.pack(fill="both", expand=True)
    sb = tk.Frame(root, bg=CLR_HEADER_BG, height=28); sb.pack(fill="x", side="bottom")
    tk.Label(sb, text="PDT Stats v5.0.54  |  Click  PDT Tagging  to tag selected CRs",
             font=FONT_SMALL, bg=CLR_HEADER_BG, fg=CLR_SUBTEXT).pack(side="left", padx=12)
    root.mainloop()


if __name__ == "__main__":
    _demo()

