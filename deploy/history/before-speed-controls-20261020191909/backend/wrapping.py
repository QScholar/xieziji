"""Word-boundary hints shared by notebook and legacy text layout."""
import re


def preferred_space(sources, following):
    """Keep a Latin word intact without moving Chinese prose after a space.

    Return a token index, so a formula's multi-character source stays atomic.
    A standalone list label is not useful text to leave on a line of its own.
    """
    split = next((i for i in range(len(sources) - 1, -1, -1) if sources[i] == ' '), -1)
    if split <= 0:
        return -1
    word = sources[split + 1:] + [following]
    if not all(len(char) == 1 and char.isascii() and not char.isspace() for char in word):
        return -1
    prefix = ''.join(sources[:split]).strip()
    if re.fullmatch(r'(?:\d+[.)、．）]|[（(]\d+[)）]|[一二三四五六七八九十百]+[、.)．）]|[-*+•])', prefix):
        return -1
    return split
