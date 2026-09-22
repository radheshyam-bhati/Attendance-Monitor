"""Tiny in-memory DOM used to run the attendance parser against saved HTML.

Development/test helper only — the live browser path never touches this code.
It converts HTML into a page-like object implementing exactly the Playwright
API subset that ``app.attendance.parse_attendance`` relies on, so tests and
offline re-checks exercise the same parser code path as the real portal.

Limitations (intentional, sufficient for fixtures):
- supports tag / ``#id`` / ``.class`` / ``[attr]`` / ``[attr=value]`` CSS with
  descendant combinators and comma unions only;
- supports only the one ``ancestor-or-self`` XPath shape used by the parser;
- ``inner_text`` approximates browser rendering (block elements add newlines).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Iterator, Union

_WHITESPACE_RE = re.compile(r"\s+")

_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)

#: Elements that force a line boundary when approximating ``innerText``.
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "dd", "details", "div", "dl", "dt",
        "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "summary", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)


class _FixtureTimeoutError(Exception):
    """Raised when a fixture locator finds nothing, mirroring Playwright timeouts."""


class DomNode:
    """A minimal DOM node (element or text)."""

    __slots__ = ("tag", "attrs", "text", "children", "parent", "doc_index")

    def __init__(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]] | None,
        doc_index: int,
        text: str | None = None,
    ) -> None:
        self.tag = tag
        self.text = text
        self.attrs: dict[str, str] = {}
        if attrs:
            for key, value in attrs:
                self.attrs.setdefault(key.lower(), value if value is not None else "")
        self.children: list[DomNode] = []
        self.parent: DomNode | None = None
        self.doc_index = doc_index

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    @property
    def node_id(self) -> str | None:
        return self.attrs.get("id") or None

    def is_element(self) -> bool:
        return self.text is None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DomNode {self.tag!r}>"


class _TreeBuilder(HTMLParser):
    """Build a DomNode tree, tolerating real-world malformed markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._counter = 0
        self.root = DomNode("#document", None, self._next_index())
        self._stack: list[DomNode] = [self.root]
        self._raw_depth = 0

    def _next_index(self) -> int:
        index = self._counter
        self._counter += 1
        return index

    def _append(self, node: DomNode) -> None:
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in ("script", "style"):
            self._raw_depth += 1
        node = DomNode(tag, attrs, self._next_index())
        self._append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._append(DomNode(tag.lower(), attrs, self._next_index()))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("script", "style") and self._raw_depth:
            self._raw_depth -= 1
        for position in range(len(self._stack) - 1, 0, -1):
            if self._stack[position].tag == tag:
                del self._stack[position:]
                return

    def handle_data(self, data: str) -> None:
        if self._raw_depth or not data:
            return
        self._append(DomNode("#text", None, self._next_index(), text=data))


def _parse_html(html: str) -> DomNode:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def inner_text(node: DomNode) -> str:
    """Approximate browser ``innerText``: block boundaries become newlines."""
    parts: list[str] = []

    def walk(current: DomNode) -> None:
        if current.tag == "#text":
            parts.append(current.text or "")
            return
        if current.tag == "br":
            parts.append("\n")
            return
        block = current.tag in _BLOCK_TAGS
        if block and parts and not parts[-1].endswith("\n"):
            parts.append("\n")
        for child in current.children:
            walk(child)
        if block and parts and not parts[-1].endswith("\n"):
            parts.append("\n")

    walk(node)
    return "".join(parts)


def normalized_text(node: DomNode) -> str:
    """Whitespace-collapsed inner text, as Playwright text matching sees it."""
    return _WHITESPACE_RE.sub(" ", inner_text(node)).strip()


def _iter_elements(node: DomNode) -> Iterator[DomNode]:
    for child in node.children:
        if child.is_element():
            yield child
            yield from _iter_elements(child)


def _iter_descendants(node: DomNode) -> Iterator[DomNode]:
    for child in node.children:
        yield child
        yield from _iter_descendants(child)


# ---------------------------------------------------------------------------
# CSS selection (tag / #id / .class / [attr] / [attr=value], descendants, commas)
# ---------------------------------------------------------------------------

_PART_TOKEN_RE = re.compile(r"\.([-\w]+)|#([-\w]+)|\[([-\w:]+)(?:=['\"]?([^\]'\"]*)['\"]?)?\]")
_TAG_RE = re.compile(r"[a-zA-Z][\w-]*|\*")

_Compound = tuple[str | None, set[str], str | None, list[tuple[str, str | None]]]


def _parse_compound(token: str) -> _Compound:
    """Parse one compound selector such as ``table.row[data-x='y']``."""
    tag: str | None = None
    rest_start = 0
    match = _TAG_RE.match(token)
    if match:
        tag = None if match.group() == "*" else match.group().lower()
        rest_start = match.end()

    classes: set[str] = set()
    node_id: str | None = None
    attrs: list[tuple[str, str | None]] = []
    for part in _PART_TOKEN_RE.finditer(token[rest_start:]):
        dot_class, hash_id, attr_name, attr_value = part.groups()
        if dot_class:
            classes.add(dot_class)
        elif hash_id:
            node_id = hash_id
        elif attr_name:
            attrs.append((attr_name.lower(), attr_value))
    return tag, classes, node_id, attrs


def _matches_simple(node: DomNode, compound: _Compound) -> bool:
    tag, classes, node_id, attrs = compound
    if tag is not None and node.tag != tag:
        return False
    if node_id is not None and node.node_id != node_id:
        return False
    if classes and not classes <= node.classes:
        return False
    for name, expected in attrs:
        actual = node.attrs.get(name)
        if expected is None:
            if actual is None:
                return False
        elif actual != expected:
            return False
    return True


def _matches_chain(node: DomNode, compounds: list[_Compound]) -> bool:
    if not _matches_simple(node, compounds[-1]):
        return False
    if len(compounds) == 1:
        return True
    ancestor = node.parent
    while ancestor is not None and ancestor.tag != "#document":
        if _matches_chain(ancestor, compounds[:-1]):
            return True
        ancestor = ancestor.parent
    return False


def _select(root: DomNode, selector: str) -> list[DomNode]:
    """Return elements under ``root`` matching the selector, in document order."""
    compound_groups = [
        [_parse_compound(token) for token in part.split()] for part in selector.split(",") if part.strip()
    ]
    matches = {
        node
        for node in _iter_descendants(root)
        if node.is_element() and any(_matches_chain(node, compounds) for compounds in compound_groups)
    }
    return sorted(matches, key=lambda n: n.doc_index)


# ---------------------------------------------------------------------------
# XPath: only the ancestor-or-self pattern used by app.attendance
# ---------------------------------------------------------------------------

_XPATH_TITLE_RE = re.compile(r"normalize-space\(text\(\)\)='([^']*)'")
_XPATH_SELF_RE = re.compile(r"self::([a-zA-Z][\w-]*)")


def _select_xpath(root: DomNode, expression: str) -> list[DomNode]:
    """Emulate ``//*[normalize-space(text())='X']/ancestor-or-self::*[self::…][1]``.

    Returns the nearest ancestor-or-self element (in the allowed tag set) of
    the first element whose direct text equals ``X`` — the equivalent of the
    parser's ``.first`` usage.
    """
    title_match = _XPATH_TITLE_RE.search(expression)
    self_tags = set(_XPATH_SELF_RE.findall(expression))
    if not title_match or not self_tags:
        return []  # Unsupported expression shape for the fixture engine.
    wanted = _WHITESPACE_RE.sub(" ", title_match.group(1)).strip()
    for node in _iter_elements(root):
        direct_texts = [
            _WHITESPACE_RE.sub(" ", child.text or "").strip() for child in node.children if child.tag == "#text"
        ]
        if wanted in direct_texts:
            current: DomNode | None = node
            while current is not None and current.tag != "#document":
                if current.tag in self_tags:
                    return [current]
                current = current.parent
            return []
    return []


# ---------------------------------------------------------------------------
# Locator / page
# ---------------------------------------------------------------------------

Selector = Union[str, tuple[str, str, bool]]


def _matches_under(bases: list[DomNode], selector: Selector) -> list[DomNode]:
    found: set[DomNode] = set()
    for base in bases:
        if isinstance(selector, tuple):  # ("text", text, exact)
            _, text, exact = selector
            wanted = _WHITESPACE_RE.sub(" ", text).strip()
            for node in _iter_elements(base):
                value = normalized_text(node)
                if value == wanted if exact else wanted.lower() in value.lower():
                    found.add(node)
        elif isinstance(selector, str) and selector.startswith("xpath="):
            found.update(_select_xpath(base, selector[len("xpath=") :]))
        else:
            found.update(_select(base, selector))
    return sorted(found, key=lambda n: n.doc_index)


class _FixtureLocator:
    """Playwright-like locator over the in-memory DOM (small API subset)."""

    def __init__(self, base: Union[DomNode, "_FixtureLocator"], selector: Selector, index: int | None = None) -> None:
        self._base = base
        self._selector = selector
        self._index = index

    @property
    def first(self) -> "_FixtureLocator":
        return _FixtureLocator(self._base, self._selector, index=0)

    def locator(self, selector: str) -> "_FixtureLocator":
        return _FixtureLocator(self, selector)

    def get_by_text(self, text: str, *, exact: bool = False) -> "_FixtureLocator":
        return _FixtureLocator(self, ("text", text, exact))

    def _base_nodes(self) -> list[DomNode]:
        if isinstance(self._base, DomNode):
            return [self._base]
        return self._base._matches()

    def _matches(self) -> list[DomNode]:
        return _matches_under(self._base_nodes(), self._selector)

    async def count(self) -> int:
        return len(self._matches())

    async def all(self) -> list["_FixtureLocator"]:
        total = len(self._matches())
        return [_FixtureLocator(self._base, self._selector, index=i) for i in range(total)]

    async def inner_text(self) -> str:
        nodes = self._matches()
        index = 0 if self._index is None else self._index
        if index >= len(nodes):
            raise _FixtureTimeoutError(f"No element matching {self._selector!r} at index {index}.")
        return inner_text(nodes[index])

    async def wait_for(self, *, timeout: float | None = None) -> None:
        del timeout  # Fixture matching is instantaneous.
        if not self._matches():
            raise _FixtureTimeoutError(f"Element {self._selector!r} was not found.")


class _FixturePage:
    """Page-like object wrapping saved HTML for the attendance parser."""

    def __init__(self, root: DomNode, url: str, source: str) -> None:
        self._root = root
        self._source = source
        self.url = url

    def locator(self, selector: str) -> _FixtureLocator:
        return _FixtureLocator(self._root, selector)

    def get_by_text(self, text: str, *, exact: bool = False) -> _FixtureLocator:
        return _FixtureLocator(self._root, ("text", text, exact))

    async def content(self) -> str:
        return self._source


def page_from_html(html: str, *, url: str = "file://fixture/today_attendance.html") -> _FixturePage:
    """Build a parser-compatible page from saved HTML (test/dev helper)."""
    root = _parse_html(html)
    if not _select(root, "body"):
        root = _parse_html(f"<html><body>{html}</body></html>")
    return _FixturePage(root, url, source=html)
