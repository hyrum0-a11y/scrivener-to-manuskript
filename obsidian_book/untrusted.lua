-- Used by build_epub(untrusted=True) for vaults uploaded to the web tool.
-- pandoc's EPUB writer reads every image (and every src= in raw HTML) from
-- disk or the network, so a note containing ![](/etc/passwd) would embed a
-- server file in the book. Keep only images that are relative paths inside
-- the vault; drop raw HTML that references any file or URL.

local function unsafe_path(src)
  return src:match("^%a[%w+.-]*:")   -- a URL or Windows drive (http:, file:, C:)
      or src:match("^[/\\~]")        -- absolute or home-relative
      or src:find("..", 1, true)     -- parent directory
      or src:find("\\", 1, true)     -- backslash paths
end

function Image(img)
  if unsafe_path(img.src) then
    return pandoc.Span(img.caption)
  end
end

local RISKY = { "src", "href", "poster", "data", "url(", "@import", "xlink" }

local function raw(el)
  if not el.format:match("html") then
    return {}  -- latex/other raw formats aren't wanted in an epub anyway
  end
  local text = el.text:lower()
  for _, word in ipairs(RISKY) do
    if text:find(word, 1, true) then
      return {}
    end
  end
end

RawInline = raw
RawBlock = raw
