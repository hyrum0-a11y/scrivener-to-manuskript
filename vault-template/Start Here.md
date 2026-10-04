# Start Here

This vault is a small, complete example book. It already converts into a
real e-book, so you can try that first, then replace the example with your
own writing.

## What's in this vault

| Note or file | What it's for |
|---|---|
| **Book Info** | Title, author, cover and other details. Open it first. |
| **Title Page** (in Front Matter) | Your book's title page, written the way you want it. |
| **Manuscript Reading Order** | The order of everything in your book. |
| **Manuscript** folder | Your story: one note per scene, in part and chapter folders. |
| **Front Matter** folder | Pages before the story: **Information** (your copyright page) and acknowledgments. |
| **Back Matter** folder | Pages after the story (about the author). |
| **cover.jpg** | An example cover. Replace it with your own (see below). |

## Writing

- Write each scene in its own note inside the **Manuscript** folder.
- Put **each paragraph on its own line**, with no empty line between
  paragraphs. The converter adds proper paragraph spacing and indents.
- Don't type anything between scenes: the converter adds a scene-break
  symbol between them for you. It's `—※—` unless you choose another one
  under `scene_break` in **Book Info** (for example `* * *` or `#`, or
  `blank` for just an empty line).
- *Italics* and **bold** work as usual in Obsidian.
- Two hyphens `--` become a long dash (—) in the finished book.
- Notes to yourself: anything between `%%` marks, like `%%check this date%%`,
  stays in the vault but is left out of the book. So is ~~struck-out text~~.

## How your finished book looks

Your notes in Obsidian are for writing; the converter does the typesetting.
So the EPUB, PDF and Word versions won't look exactly like your notes.
What it does for you:

- **Paragraphs**: indented, with no gap between them. The first paragraph
  of a chapter and the first one after a scene break aren't indented.
- **Chapter openings**: each chapter starts on a new page, its title
  exactly as typed in the Reading Order, and its first paragraph opens
  with a large capital letter.
- **Scene breaks**: the `scene_break` symbol from Book Info goes between
  scenes (`—※—` unless you choose another).
- **Punctuation**: straight quotes become curly quotes, and `--` becomes a
  long dash (—).
- **Messages and letters**: a paragraph written entirely in italics, like a
  text message or a note (it can start with a name, as in
  `Anna: *Running late*`), is set apart without an indent.
- **Left out**: `%%notes%%` and ~~struck-out text~~.
- **PDF and Word only**: justified text, page numbers and running headers
  (your name on left pages, the title on right pages, none on chapter
  opening pages), a contents page, and parts and the first chapter of
  each part starting on a right-hand page.
- **Front and back matter** (Information, Acknowledgments, About the
  Author): centred.

On an e-reader, the reader's own settings (font, size, margins) also
change how the EPUB looks.

Want something laid out differently? Download the **Word** version, make
your changes there, and export your own PDF. (Changes made in Word stay in
that file: the next conversion starts fresh from your notes.)

## Your title page

The **Title Page** note is your book's title page, and you decide what's on
it. Each line is centred:

- `# A line` is printed large (use two `#` lines for a two-line title).
- `## A line` is medium, for a subtitle or "A Novel".
- `### A line` is small, for a series line or your publisher.
- A plain line is printed in bold, for your name.
- Empty lines add space; `---` adds a small ornament.

## Front and back matter

**Information** is your copyright page, and **Acknowledgments** and **About
the Author** are yours to write too. They're printed exactly as you write
them: nothing on them is filled in for you, not even from Book Info. Replace
everything in square brackets, like `[Your Name]` and `[Year]`, and any
"Replace this" text. The book check reminds you if some is left.

## Adding scenes and chapters

The **Manuscript Reading Order** note decides what goes in the book and in
what order. Folder names don't matter, only this note does.

- **A new scene**: create a note in the Manuscript folder (in the chapter's
  folder, to keep things tidy). Then, in the Reading Order, add a line
  under the chapter with the note's name between double square brackets:
  `- [[My New Scene]]`. Obsidian suggests names as you type `[[`.
- **A new chapter**: in the Reading Order, add a line starting with `## `
  and the chapter title, then the scene lines under it. The title is
  printed exactly as you type it, for example `## Chapter 2` or
  `## The Storm`.
- **Front and back matter**: these lines end with *_Front Matter_* or
  *_Back Matter_*. Delete a line to leave that page out.

### Optional extras

The starter Reading Order shows a part and some lines under the part and
chapter titles, so you can see how they look. Most books need nothing more
than chapters and scenes: delete whatever you don't want. What's possible:

- **Parts**: a line starting with `# ` begins a part, with its own page,
  for example `# Part I The Beginning`. Chapters listed under it belong to
  that part. Without any `# ` lines, the book simply has no parts.
- **Lines under a part or chapter title**: start a line with `center:`,
  `left:` or `right:` and it's printed under the title in that position. Use
  Obsidian's usual formatting inside it: `center: *A line of poetry*` is
  italic, `center: **Anna**` is bold. For example:

  ```
  # Part I The Beginning
  center: *Every road begins*
  center: *with a single step*

  ## Chapter 1
  center: **Anna**
  left: London, 1952
  - [[01 - Opening Scene]]
  ```

- **Notes to yourself**: anything between `%%` marks, like
  `%%move this chapter?%%`, is ignored. Any other line under a title that
  doesn't start with `center:`, `left:` or `right:` is left out of the book,
  and the book check lists it so you can see.

A short reminder of all of this sits at the bottom of the Reading Order
note. You'll see it while editing; it's hidden in reading view and never
printed.

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
2. Choose **Obsidian → EPUB**, **PDF** or **Word**, and pick this vault's
   folder.
3. The site checks your book first and lists anything to fix, then gives
   you the finished file to download.

Want to fine-tune the layout yourself? Download the **Word** version. It's
laid out like the print PDF (title page, contents, part pages, headers) and
opens in Word, Google Docs, LibreOffice and Pages. Make your changes there,
then export a PDF from that program. Keep writing in this vault, though:
the next conversion starts fresh from your notes, so changes made in Word
aren't carried back.

For the intended look, install the free fonts
[EB Garamond](https://fonts.google.com/specimen/EB+Garamond) and
[Linux Biolinum](https://sourceforge.net/projects/linuxlibertine/). Without
them, Word uses similar fonts.

## Printing your book

Printed books start the contents, each part, and each part's first chapter
on a right-hand page, so some left-hand pages are left blank on purpose.
Printers (and KDP or IngramSpark) need those blank pages to really be in
the PDF you upload.

1. **Simplest: use the PDF from the website.** Its blank pages are real
   pages, ready to print.
2. **Fine-tuned in Word?** Word's *Save as PDF* or *Export to PDF* keeps the
   blank pages.
3. **Using LibreOffice?** In *File → Export as PDF*, tick **Export
   automatically inserted blank pages**. Without it, LibreOffice leaves them
   out and parts can end up on left-hand pages. (LibreOffice shows these
   pages on screen with a grey "blank page" label; that's normal.)
4. **Google Docs** ignores right-hand page starts, so don't use it for the
   final print PDF.

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
