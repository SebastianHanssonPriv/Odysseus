# Fonts

The redesign uses **Barlow** for body text and **Barlow Condensed SemiBold** for
headings, section labels, buttons, nav items and big numbers (REDESIGN_SPEC.md §2).

Both are SIL Open Font License, so they can be vendored into this repo and shipped
inside the `.exe`.

**They are not in git.** Nothing ignores them — they have simply never been
committed on any branch, so a fresh clone has this README and nothing else, and
`build.bat` then bundles an empty `fonts\` folder into an `.exe` that falls back
to Segoe UI on every screen. Having them locally is not the same as having them
in the repository: after dropping them in, run `git status`, then add and commit
them, or every colleague who clones this gets the fallback.

Download them and drop these three files into this folder:

```
fonts/Barlow-Regular.ttf
fonts/Barlow-Medium.ttf
fonts/BarlowCondensed-SemiBold.ttf
```

Get them from Google Fonts (https://fonts.google.com/specimen/Barlow and
https://fonts.google.com/specimen/Barlow+Condensed) — download the family, take the
three static `.ttf` files above out of the zip, and ignore the variable-font versions.

**Barlow and Barlow Condensed are two different families, from two different
downloads.** `Barlow-SemiBold.ttf` is not a substitute for
`BarlowCondensed-SemiBold.ttf`, and renaming it will not work: the family name
is recorded inside the file, so a renamed `Barlow-SemiBold.ttf` still registers
as the family "Barlow SemiBold" and every heading keeps falling back to Segoe UI
while looking as though the font was installed.

To check what is actually in this folder:

```bat
python tests\test_fonts.py
```

It reads the family name out of each `.ttf` and reports which of the two
families the stylesheets need are missing, and what falls back without them.

## What happens if they are missing

Nothing breaks. `widgets.load_fonts()` reports which files it could not register,
`studio_app.main()` logs one line about it, and every font stack in the stylesheet
falls back to Segoe UI. The app just does not look quite like the mockups until the
files are here.

Both `build.bat` and `BufabBIGovernanceStudio.spec` already include this folder in the
PyInstaller data, so once the files are present they ship with the executable
automatically.
