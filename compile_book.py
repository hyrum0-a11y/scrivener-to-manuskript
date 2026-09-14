#!/usr/bin/env python3
"""Compile Silent Subversion I manuscript markdown files into an ODT document."""

import io
import os
import re
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

TITLE = "Silent Subversion I"
MANUSCRIPT_DIR = Path("/home/rum/Dev/SilentSub1/Manuscript")
OUTPUT_DIR = Path("/home/rum/Dropbox/TEXT/SS/SSBK1/Obsidian Epub Export")
SCRIPT_DIR = Path(__file__).parent
REFERENCE_ODT = SCRIPT_DIR / 'reference.odt'


def patch_styles(xml):
    """Patch Text_20_body style for novel paragraph formatting:
    justified, 0.14" first-line indent, no extra spacing between paragraphs."""
    new_props = (
        '<style:paragraph-properties '
        'fo:margin-top="0in" fo:margin-bottom="0in" '
        'fo:text-align="justify" fo:text-indent="0.14in" '
        'style:contextual-spacing="false" />'
    )

    def replace_props(m):
        block = m.group(0)
        block = re.sub(r'<style:paragraph-properties[^/]*/>', new_props, block)
        return block

    return re.sub(
        r'<style:style style:name="Text_20_body".*?</style:style>',
        replace_props,
        xml,
        flags=re.DOTALL,
    )


def build_reference_odt():
    """Generate reference.odt with novel paragraph styles from pandoc defaults."""
    result = subprocess.run(
        ['pandoc', '--print-default-data-file', 'reference.odt'],
        capture_output=True,
    )
    src = zipfile.ZipFile(io.BytesIO(result.stdout))
    dst_buf = io.BytesIO()
    with zipfile.ZipFile(dst_buf, 'w', zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == 'styles.xml':
                data = patch_styles(data.decode()).encode()
            dst.writestr(name, data)
    REFERENCE_ODT.write_bytes(dst_buf.getvalue())


def strip_frontmatter(text):
    """Remove YAML frontmatter block if present."""
    if text.startswith('---'):
        end = text.find('\n---', 3)
        if end != -1:
            return text[end + 4:]
    return text


def compile_manuscript():
    parts = sorted(p for p in MANUSCRIPT_DIR.iterdir() if p.is_dir())
    chunks = []

    for part in parts:
        chunks.append(f"# {part.name}")

        chapters = sorted(c for c in part.iterdir() if c.is_dir())

        for chapter in chapters:
            chunks.append(f"## {chapter.name}")

            scenes = sorted(s for s in chapter.iterdir() if s.suffix == '.md')

            for i, scene in enumerate(scenes):
                chunks.append(f"### {scene.stem}")

                body = strip_frontmatter(scene.read_text(encoding='utf-8')).strip()
                body = re.sub(r'\n(?!\n)', '\n\n', body)
                chunks.append(body)

                if i < len(scenes) - 1:
                    chunks.append("-<>-")

    return '\n\n'.join(chunks)


def main():
    timestamp = datetime.now().strftime('%Y%m%d%H%M')
    output_filename = f"{TITLE}_{timestamp}.odt"
    output_path = OUTPUT_DIR / output_filename

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Building reference styles...")
    build_reference_odt()

    print("Compiling manuscript...")
    content = compile_manuscript()

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', encoding='utf-8', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ['pandoc', tmp_path, '--reference-doc', str(REFERENCE_ODT), '-o', str(output_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"pandoc error:\n{result.stderr}")
            return 1
    finally:
        os.unlink(tmp_path)

    print(f"Written: {output_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
