# Vault template

A ready-to-copy Obsidian vault, structured for `obsidian_to_epub.py`,
`obsidian_to_pdf.py`, and `obsidian_to_odt.py` — see the main repo README
for how the pipeline works. This one compiles as-is (it's a real, if tiny,
book), so you can confirm your setup works before writing anything.

## Getting started

1. Copy this whole folder somewhere else and open it as its own vault in Obsidian:
   ```
   cp -r vault-template ~/Books/My\ Novel
   ```
2. Edit `Book Info.md` — fill in your title/author/etc.
3. Edit `Manuscript Reading Order.md`, replace the example Part/Chapter,
   and add your own scene files under `Manuscript/` (referenced there by
   filename, wherever you put them).
4. Replace the placeholder text in `Front Matter/Information.md`,
   `Front Matter/Acknowledgments.md`, and `Back Matter/About the Author.md`
   — or delete the bullets referencing them from the Reading Order note if
   you don't want them.
5. Compile:
   ```
   python3 ../obsidian_to_epub.py . --output-dir ~/wherever
   python3 ../obsidian_to_pdf.py . --output-dir ~/wherever
   python3 ../obsidian_to_odt.py . --output-dir ~/wherever
   ```
   (paths relative to wherever you copied the vault to; adjust to point at
   the scripts and your own output folder)

## About the included `.obsidian/` settings

This vault ships with a working Obsidian setup (theme, a couple of
plugins, editor preferences) as a starting point — delete `.obsidian/` and
Obsidian will just fall back to its own defaults if you'd rather not use
any of it. The vendored themes/plugins below are snapshots of a specific
version at the time this template was published; for the latest version
of any of them, remove the vendored copy and reinstall it fresh via
Obsidian's Community Plugins/Themes browser instead.

**Themes:**
- [Minimal](https://github.com/kepano/obsidian-minimal) by kepano (v8.1.7)
- [ITS Theme](https://github.com/SlRvb/Obsidian--ITS-Theme) by SlRvb (v1.4.07)

**Plugins:**
- [Typewriter Scroll](https://github.com/deathau/cm-typewriter-scroll-obsidian) by death_au (v0.2.2)
- [Novel Word Count](https://github.com/isaaclyman/obsidian-novel-word-count-plugin) by Isaac Lyman (v4.6.1)
- [LanguageTool Integration](https://github.com/Clemens-E/obsidian-languagetool-plugin) by Clemens Ertle (v0.3.8)
- [Minimal Theme Settings](https://github.com/kepano/obsidian-minimal-settings) by kepano (v8.2.3)
- [Pandoc Plugin](https://github.com/OliverBalfour/obsidian-pandoc) by Oliver Balfour (v0.4.1)
- [Style Settings](https://github.com/mgmeyers/obsidian-style-settings) by mgmeyers (v1.0.9)

The LanguageTool plugin is pre-configured to talk to a self-hosted server
at `http://127.0.0.1:8081` rather than a cloud endpoint — install and run
[LanguageTool](https://languagetool.org/) locally for grammar-checking
without sending your manuscript to a third party, or change `serverUrl` in
`.obsidian/plugins/obsidian-languagetool-plugin/data.json` to use their
cloud service instead.
