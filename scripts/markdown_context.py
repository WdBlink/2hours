"""Track code blocks and multiline code spans before report preprocessing."""
import re


def prose_lines(markdown):
    fence_char, fence_length = None, 0
    inline_ticks = 0
    offset = 0
    for line in markdown.splitlines(keepends=True):
        fence = re.match(r"^(?: {0,3}> ?)* {0,3}(?:(?:[-+*]|\d+[.)]) +)?(`{3,}|~{3,})", line)
        prose = fence_char is None and not inline_ticks and not line.startswith(('    ', '\t'))
        if fence:
            prose = False
            marker = fence.group(1)
            if fence_char is None:
                fence_char, fence_length = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length and not line[fence.end():].strip():
                fence_char, fence_length = None, 0
        elif fence_char is None:
            if not line.strip():
                inline_ticks = 0
            if not line.startswith(('    ', '\t')):
                for ticks in re.finditer(r"(?<!`)(`+)(?!`)", line):
                    prefix = line[:ticks.start()]
                    if (len(prefix) - len(prefix.rstrip("\\"))) % 2:
                        continue
                    length = len(ticks.group(1))
                    if not inline_ticks:
                        inline_ticks = length
                    elif inline_ticks == length:
                        inline_ticks = 0
        yield offset, line, prose
        offset += len(line)
