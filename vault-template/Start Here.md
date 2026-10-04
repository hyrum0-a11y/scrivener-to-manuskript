# Start Here

This vault is a small, complete example book. It already converts into a
real e-book, so you can try that first, then replace the example with your
own writing.

## What's in this vault

| Note or file | What it's for |
|---|---|
| **Book Info** | Title, author, cover and other details. Open it first. |
| **Manuscript Reading Order** | The order of everything in your book. |
| **Manuscript** folder | Your story: one note per scene, in part and chapter folders. |
| **Front Matter** folder | Pages before the story (copyright page, acknowledgments). |
| **Back Matter** folder | Pages after the story (about the author). |
| **cover.jpg** | An example cover. Replace it with your own (see below). |

## Writing

- Write each scene in its own note inside the **Manuscript** folder.
- Put **each paragraph on its own line**, with no empty line between
  paragraphs. The converter adds proper paragraph spacing and indents.
- Don't type anything between scenes: the converter adds a scene-break
  symbol between them for you.
- *Italics* and **bold** work as usual in Obsidian.
- Two hyphens `--` become a long dash (—) in the finished book.
- Notes to yourself: anything between `%%` marks, like `%%check this date%%`,
  stays in the vault but is left out of the book. So is ~~struck-out text~~.

## Adding scenes and chapters

The **Manuscript Reading Order** note decides what goes in the book and in
what order. Folder names don't matter, only this note does.

- **A new scene**: create a note in the Manuscript folder (in the chapter's
  folder, to keep things tidy). Then, in the Reading Order, add a line
  under the chapter with the note's name between double square brackets:
  `- [[My New Scene]]`. Obsidian suggests names as you type `[[`.
- **A new chapter**: in the Reading Order, add a line starting with `## `
  and the chapter title, then the scene lines under it. A plain line right
  under the title (like a character's name) is printed under the chapter
  title, as in the example.
- **Parts**: a line starting with `# ` begins a part, like
  `# Part I The Beginning`. The `>` lines under it are an optional short
  epigraph. Book not divided into parts? Delete the part line and its `>`
  lines, and list your chapters directly.
- **Front and back matter**: these lines end with *_Front Matter_* or
  *_Back Matter_*. Delete a line to leave that page out.

If a scene doesn't appear in your book, it's almost always missing from the
Reading Order, or its name there is spelled differently from the note.

## Your cover

Every book needs a cover. The easiest way: name your cover image
`cover.jpg` and put it in this vault's top folder, replacing the example.
Nothing else needs to change. If your cover has another name or is a
`.png`, put it in the top folder and change the `cover:` line in
**Book Info** to its exact file name.

## Turning it into a book

1. Go to **https://authortools.hyrumjones.com**.
2. Choose **Obsidian → EPUB** (or PDF) and pick this vault's folder.
3. The site checks your book first and lists anything to fix, then gives
   you the finished file to download.

Converting on your own computer with the command-line tools instead? The
finished books are saved to the folder named in **Book Info** under
`output_dir` (your Downloads folder unless you change it).

## About this vault's Obsidian settings

The vault comes with a theme and a few plugins already set up. Delete the
hidden `.obsidian` folder if you'd rather start with Obsidian's defaults.

- Themes: [Minimal](https://github.com/kepano/obsidian-minimal) by kepano,
  [ITS Theme](https://github.com/SlRvb/Obsidian--ITS-Theme) by SlRvb.
- Plugins: [Typewriter Scroll](https://github.com/deathau/cm-typewriter-scroll-obsidian),
  [Novel Word Count](https://github.com/isaaclyman/obsidian-novel-word-count-plugin),
  [LanguageTool Integration](https://github.com/Clemens-E/obsidian-languagetool-plugin),
  [Minimal Theme Settings](https://github.com/kepano/obsidian-minimal-settings),
  [Pandoc Plugin](https://github.com/OliverBalfour/obsidian-pandoc),
  [Style Settings](https://github.com/mgmeyers/obsidian-style-settings).
- The LanguageTool grammar checker is set to use a copy of LanguageTool
  running on your own computer, so your writing isn't sent anywhere. To use
  LanguageTool's online service instead, change it in the plugin's settings.

The example cover is from *Through the Curtains*, book one of the Cloud
World series by Hyrum Jones.
