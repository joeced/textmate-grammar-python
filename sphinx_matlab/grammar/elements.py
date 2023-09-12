from typing import List, Tuple, TYPE_CHECKING
from pprint import pprint

if TYPE_CHECKING:
    from io import StringIO
    from .parser import GrammarParser


class ParsedElement(object):
    """The base parsed element object."""

    def __init__(
        self,
        token: str,
        grammar: dict,
        content: str,
        span: Tuple[int, int],
        captures: List["ParsedElement"] = [],
    ) -> None:
        self.token = token
        self.grammar = grammar
        self.content = content
        self.span = span
        self.captures = captures

    def to_dict(self, content: bool = True, parse_unparsed: bool = True, **kwargs) -> dict:
        "Converts the object to dictionary."
        if parse_unparsed:
            self.parse_unparsed()
        out_dict = {"token": self.token}
        if content:
            out_dict["content"] = self.content
        if self.captures:
            out_dict["captures"] = self._list_property_to_dict(
                "captures", content=content, parse_unparsed=parse_unparsed
            )
        return out_dict

    def print(self, **kwargs) -> None:
        """Prints the current object recursively by converting to dictionary."""
        pprint(self.to_dict(**kwargs), sort_dicts=False, width=kwargs.pop("width", 150), **kwargs)

    def parse_unparsed(self):
        """Parses the unparsed elements contained in the current element."""
        self._parse_unparsed_property("captures")

    def _parse_unparsed_property(self, prop: str):
        """Parses the unparsed elements of the UnparsedElement type of a property."""
        elements, parsed_elements = getattr(self, prop, []), []
        for element in elements:
            if type(element) is UnparsedElement:
                parsed_elements += element.parse()
            else:
                parsed_elements.append(element)
        setattr(self, prop, parsed_elements)

    def _list_property_to_dict(self, prop: str, **kwargs):
        """Makes a dictionary from a property."""
        return [item.to_dict(**kwargs) if isinstance(item, ParsedElement) else item for item in getattr(self, prop, [])]

    def __repr__(self) -> str:
        content = self.content if len(self.content) < 15 else self.content[:15] + "..."
        return repr(f"{self.token}<<{content}>>({len(self.captures)})")


class ParsedElementBlock(ParsedElement):
    """A parsed element with a begin and a end"""

    def __init__(self, begin: List[ParsedElement] = [], end: List[ParsedElement] = [], **kwargs) -> None:
        super().__init__(**kwargs)
        self.begin = begin
        self.end = end

    def to_dict(self, *args, **kwargs) -> dict:
        out_dict = super().to_dict(*args, **kwargs)
        if self.begin:
            out_dict["begin"] = self._list_property_to_dict("begin", **kwargs)
        if self.end:
            out_dict["end"] = self._list_property_to_dict("end", **kwargs)

        ordered_keys = [key for key in ["token", "begin", "end", "content", "captures"] if key in out_dict]
        ordered_dict = {key: out_dict[key] for key in ordered_keys}
        return ordered_dict

    def parse_unparsed(self):
        """Parses the unparsed elements contained in the current element."""
        self._parse_unparsed_property("captures")
        self._parse_unparsed_property("begin")
        self._parse_unparsed_property("end")


class UnparsedElement(ParsedElement):
    """The to-be-parsed Element type.

    If a matched regex pattern includes groups that are to be parsed iteratively, an UnparsedElement is
    created. Unparsed elements are to be parsed at a later moment and allows for faster pattern matching.
    """

    def __init__(self, stream: "StringIO", parser: "GrammarParser", span: Tuple[int, int], **kwargs):
        super().__init__(parser.token if parser.token else parser.content_token, parser.grammar, "UNPARSED", span)
        self.stream = stream
        self.parser = parser
        self.parser_kwargs = kwargs

    def parse(self) -> List[ParsedElement]:
        """Parses the stream."""
        elements = self.parser.parse(self.stream, start_pos=self.span[0], close_pos=self.span[1])
        return elements
