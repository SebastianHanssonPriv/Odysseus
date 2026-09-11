# Fonts

The redesign uses **Barlow** for body text and **Barlow Condensed SemiBold** for
headings, section labels, buttons, nav items and big numbers (REDESIGN_SPEC.md §2).

Both are SIL Open Font License, so they can be vendored into this repo and shipped
inside the `.exe`. They are not committed yet — download them and drop these three
files into this folder:

```
fonts/Barlow-Regular.ttf
fonts/Barlow-Medium.ttf
fonts/BarlowCondensed-SemiBold.ttf
```

Get them from Google Fonts (https://fonts.google.com/specimen/Barlow and
https://fonts.google.com/specimen/Barlow+Condensed) — download the family, take the
three static `.ttf` files above out of the zip, and ignore the variable-font versions.

## What happens if they are missing

Nothing breaks. `widgets.load_fonts()` reports which files it could not register,
`studio_app.main()` logs one line about it, and every font stack in the stylesheet
falls back to Segoe UI. The app just does not look quite like the mockups until the
files are here.

Both `build.bat` and `BufabBIGovernanceStudio.spec` already include this folder in the
PyInstaller data, so once the files are present they ship with the executable
automatically.
