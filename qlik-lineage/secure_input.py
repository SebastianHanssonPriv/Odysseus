from __future__ import annotations

import tkinter as tk

# Clipboard-out and deletion routes we block so a secret can be typed/pasted IN
# but never copied/cut OUT of the field. Paste (<<Paste>> / Ctrl+V) stays
# enabled so values can be filled from a password manager.
_BLOCK_SEQUENCES = (
    "<<Copy>>",
    "<<Cut>>",
    "<Control-c>",
    "<Control-C>",
    "<Control-x>",
    "<Control-X>",
    "<Control-Insert>",
    "<Shift-Delete>",
    "<Button-3>",  # right-click context menu (another copy route)
    "<Button-2>",  # middle-click paste-selection on X11
)


def _harden(entry: tk.Entry) -> None:
    entry.bind("<<Copy>>", lambda _e: "break")
    for seq in _BLOCK_SEQUENCES:
        entry.bind(seq, lambda _e: "break")


class _CredentialDialog:
    """Modal window that collects credentials into memory only.

    Every field is masked (nothing is shown on screen) and copy/cut are
    disabled. Values are never written to disk or logged by this module.
    """

    def __init__(self, fields: list[tuple[str, str]]):
        self.result: dict[str, str] = {}
        self._root = tk.Tk()
        self._root.title("Qlik Cloud credentials (development)")
        self._root.resizable(False, False)

        tk.Label(
            self._root,
            text=(
                "Enter credentials. Values are masked, copy/cut are disabled, "
                "and nothing is saved to disk."
            ),
            wraplength=380,
            justify="left",
        ).grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 8), sticky="w")

        self._entries: dict[str, tk.Entry] = {}
        for i, (key, label) in enumerate(fields, start=1):
            tk.Label(self._root, text=label).grid(
                row=i, column=0, padx=(12, 6), pady=4, sticky="e"
            )
            entry = tk.Entry(self._root, width=46, show="•")
            entry.grid(row=i, column=1, padx=(0, 12), pady=4, sticky="w")
            _harden(entry)
            self._entries[key] = entry

        buttons = tk.Frame(self._root)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, pady=(8, 12))
        tk.Button(buttons, text="Cancel", width=12, command=self._cancel).pack(
            side="right", padx=6
        )
        tk.Button(buttons, text="Connect", width=12, command=self._submit).pack(
            side="right"
        )

        self._root.bind("<Return>", lambda _e: self._submit())
        self._root.bind("<Escape>", lambda _e: self._cancel())
        if fields:
            self._entries[fields[0][0]].focus_set()

    def _submit(self) -> None:
        values = {key: entry.get() for key, entry in self._entries.items()}
        if any(not v.strip() for v in values.values()):
            return  # wait until every field is filled
        self.result = values
        self._wipe()
        self._root.destroy()

    def _cancel(self) -> None:
        self.result = {}
        self._wipe()
        self._root.destroy()

    def _wipe(self) -> None:
        # Best effort: clear the widget contents. Python strings are immutable,
        # so the entered text cannot be truly zeroed in memory, but we remove
        # every reference we control.
        for entry in self._entries.values():
            entry.delete(0, tk.END)

    def run(self) -> dict[str, str]:
        self._root.mainloop()
        return self.result


def prompt_credentials(need_tenant: bool, need_client: bool) -> dict[str, str]:
    """Open the secure dialog and return entered credentials (memory only).

    Returns an empty dict if the user cancels.
    """
    fields: list[tuple[str, str]] = []
    if need_tenant:
        fields.append(("tenant_url", "Tenant URL"))
    if need_client:
        fields.append(("client_id", "OAuth client ID"))
    fields.append(("client_secret", "OAuth client secret"))
    return _CredentialDialog(fields).run()
