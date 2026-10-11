"""Small Markdown readers shared by navigation and validation (no renderer)."""

import html
import re
import unicodedata

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def markdown_lines(text):
    """Yield (line number, raw line, fence language or None, is delimiter)."""
    fence = None
    for number, line in enumerate(text.splitlines(), 1):
        match = FENCE.match(line)
        if fence:
            char, length, language = fence
            if re.fullmatch(rf" {{0,3}}{re.escape(char)}{{{length},}}\s*", line):
                yield number, line, language, True
                fence = None
            else:
                yield number, line, language, False
        elif match:
            marker, info = match.groups()
            language = info.strip().split()[0].lower() if info.strip() else ""
            fence = marker[0], len(marker), language
            yield number, line, language, True
        else:
            yield number, line, None, False


def mask_comments(text):
    """Remove HTML comments while retaining character and line positions."""
    return re.sub(r"<!--.*?(?:-->|$)", lambda m: re.sub(r"[^\n]", " ", m[0]), text, flags=re.S)


def strip_inline_code(text):
    """Mask equal-length backtick spans, respecting escaped opening runs."""
    runs = list(re.finditer(r"`+", text))
    output = list(text)
    cursor = 0
    while cursor < len(runs):
        opening = runs[cursor]
        before = text[:opening.start()]
        backslashes = len(before) - len(before.rstrip("\\"))
        if backslashes % 2:
            cursor += 1
            continue
        closing_index = next((i for i in range(cursor + 1, len(runs))
                              if len(runs[i][0]) == len(opening[0])), None)
        if closing_index is None:
            cursor += 1
            continue
        for i in range(opening.start(), runs[closing_index].end()):
            if output[i] != "\n":
                output[i] = " "
        cursor = closing_index + 1
    return "".join(output)


def prose_lines(text, *, include_mermaid=False):
    """Yield visible prose at original line numbers; code spans may cross lines."""
    pending = []
    def flush():
        visible = strip_inline_code("\n".join(line for _, line in pending)).split("\n")
        return [(number, line) for (number, _), line in zip(pending, visible)]
    for number, line, language, delimiter in markdown_lines(mask_comments(text)):
        if language is None and not delimiter and line.strip():
            pending.append((number, line))
            continue
        yield from flush()
        pending = []
        if language == "mermaid" and include_mermaid and not delimiter:
            yield number, line
    yield from flush()


def plain_title(title):
    title = re.sub(r"!?\[([^]]+)\]\([^)]*\)", r"\1", title)
    title = re.sub(r"<[^>]+>", "", title)
    return html.unescape(title.replace("`", "").replace("*", ""))


def headings(text):
    """Return (level, title, line) for ATX and Setext headings outside fences."""
    found = []
    previous = None
    for number, line, language, delimiter in markdown_lines(mask_comments(text)):
        if language is not None or delimiter:
            previous = None
            continue
        match = re.match(r"^ {0,3}(#{1,6})(?:\s+|$)(.*)$", line)
        if match:
            title = re.sub(r"\s+#+\s*$", "", match[2]).strip()
            found.append((len(match[1]), plain_title(title), number))
            previous = None
        elif previous and re.fullmatch(r" {0,3}(=+|-+)\s*", line):
            found.append((1 if line.lstrip()[0] == "=" else 2, plain_title(previous[1]), previous[0]))
            previous = None
        else:
            previous = (number, line.strip()) if line.strip() else None
    return found


def slugify(title):
    """GitHub heading rules: lowercase, strip punctuation, retain CJK/-/_."""
    title = plain_title(title).lower().strip()
    return "".join(
        "-" if char.isspace() else char
        for char in title
        if char in "-_" or not unicodedata.category(char).startswith(("P", "S"))
    )


def heading_slugs(text):
    used = set()
    for _, title, _ in headings(text):
        base = slugify(title)
        slug = base
        duplicate = 0
        while slug in used:
            duplicate += 1
            slug = f"{base}-{duplicate}"
        used.add(slug)
    return used


def links(text):
    """Yield (line, destination) for inline and reference links/images."""
    prose = list(prose_lines(text))
    definitions = {}
    for _, line in prose:
        match = re.match(r'^ {0,3}\[([^]]+)\]:\s*(?:<([^>]+)>|(\S+))', line)
        if match:
            definitions[match[1].casefold()] = match[2] or match[3]
    inline = re.compile(r'!?\[[^]\n]*\]\(\s*(?:<([^>]+)>|((?:[^()\s]|\([^()]*\))+))(?:\s+["\'][^\n]*["\'])?\s*\)')
    reference = re.compile(r'!?\[([^]\n]+)\](?:\[([^]\n]*)\])?')
    for number, line in prose:
        if re.match(r"^ {0,3}\[[^]]+\]:", line):
            continue
        for match in inline.finditer(line):
            yield number, match[1] or match[2]
        remaining = inline.sub("", line)
        for match in reference.finditer(remaining):
            key = (match[2] or match[1]).casefold()
            if key in definitions:
                yield number, definitions[key]
