# Book Info

Your book's details. The converters read the lines inside the grey boxes
below. Change only the text **between the quote marks**, and keep the
quote marks.

## Fill these in

```book-info
title: "My Novel"
author: "Your Name"
author_file_as: "Last, First"
publisher: "Self-Published"
cover: "cover.jpg"
output_dir: "~/Downloads"
```

- **title**, **author**: as they should appear on the book.
- **author_file_as**: your name the way a library sorts it, surname first.
- **publisher**: your imprint, or leave it as Self-Published.
- **cover**: the file name of your cover image. Every book needs one. The
  `cover.jpg` in this vault's top folder is an example: replace it with
  your own cover, keeping the name `cover.jpg`, and nothing here needs to
  change. If your cover has a different name (say `My Cover.png`), put it
  in the same top folder and write that name here instead.
- **output_dir**: where finished books are saved when you convert on your
  own computer. `~/Downloads` means your Downloads folder. To save
  somewhere else, write that folder's full location, for example
  `~/Documents/My Novel` (Mac or Linux) or `C:/Users/You/Documents/My Novel`
  (Windows). The website ignores this line: you download the book from the
  page instead.

## Optional

Leave any of these as `""` if you don't need them.

```book-info
isbn: ""
subtitle: ""
title_page_lines: ""
series_position: ""
series_length: ""
trim_size: ""
running_header: ""
pov_signs_dir: ""
pov_signs: ""
```

- **isbn**: your e-book ISBN, digits only.
- **subtitle**: a subtitle line for the printed title page.
- **title_page_lines**: the title split across lines on the printed title
  page, with `|` where each new line starts, for example `THE LONG|ROAD`.
- **series_position**, **series_length**: for example `2` and `5`, to print
  a row of dots showing where this book sits in the series.
- **trim_size**: the printed page size, for example `5.25in 8in` (the
  default) or `6in 9in`.
- **running_header**: the text at the top of printed pages, if it should
  differ from the title.
- **pov_signs_dir**, **pov_signs**: small symbols printed under the chapter
  title for each point-of-view character. `pov_signs_dir` is a folder of
  images inside the vault, for example `pov-signs`, and `pov_signs` pairs
  names with files, for example `Anna=anna.png, Ben=ben.png`. Most books
  skip this.
