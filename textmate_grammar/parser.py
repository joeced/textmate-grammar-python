from typing import TYPE_CHECKING
from abc import ABC, abstractmethod
import onigurumacffi as re

from .exceptions import IncludedParserNotFound
from .elements import ContentElement, ContentBlockElement
from .handler import ContentHandler, Pattern, POS
from .captures import Captures, parse_captures
from .logging import LOGGER

if TYPE_CHECKING:
    from .language import LanguageParser


class GrammarParser(ABC):
    """The abstract grammar parser object"""

    @staticmethod
    def initialize(grammar: dict, **kwargs):
        "Initializes the parser based on the grammar."
        if "include" in grammar:
            return grammar["include"]
        elif "match" in grammar:
            return MatchParser(grammar, **kwargs)
        elif "begin" in grammar and "end" in grammar:
            return BeginEndParser(grammar, **kwargs)
        elif "begin" in grammar and "while" in grammar:
            return BeginWhileParser(grammar, **kwargs)
        elif "patterns" in grammar:
            return PatternsParser(grammar, **kwargs)
        else:
            return TokenParser(grammar, **kwargs)

    def __init__(self, grammar: dict, language: "LanguageParser | None" = None, key: str = "", **kwargs) -> None:
        self.grammar = grammar
        self.language = language
        self.key = key
        self.token = grammar.get("name", "")
        self.comment = grammar.get("comment", "")
        self.disabled = grammar.get("disabled", False)
        self.initialized = False
        self.anchored = False
        self.injected_patterns = []

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}:<{self.key}>"

    def _init_captures(self, grammar: dict, key: str = "captures", **kwargs) -> dict:
        """Initializes a captures dictionary"""
        captures = {}
        if key in grammar:
            for group_id, pattern in grammar[key].items():
                captures[int(group_id)] = self.initialize(pattern, language=self.language)
        return captures

    def _find_include(self, key: str, **kwargs) -> "GrammarParser":
        """Find the included grammars and during repository initialization"""
        if not self.language:
            raise IncludedParserNotFound(key)

        if key in ["$self", "$base"]:  # TODO there is a difference between these
            return self.language
        elif key[0] == "#":
            return self.language.repository.get(key[1:], None)
        else:
            return self.language._find_include_scopes(key)

    def initialize_repository(self, **kwargs) -> None:
        """When the grammar has patterns, this method should called to initialize its inclusions."""
        pass

    def parse(
        self,
        handler: ContentHandler,
        starting: POS = (0, 0),
        boundary: POS | None = None,
        verbosity: int = 0,
        **kwargs,
    ) -> (bool, list[ContentElement], tuple[int, int] | None):
        """The method to parse a handler using the current grammar."""
        parsed, captures, span = self._parse(handler, starting, boundary=boundary, verbosity=verbosity, **kwargs)
        elements = parse_captures(captures)
        return parsed, elements, span

    def match_and_capture(
        self,
        handler: ContentHandler,
        pattern: Pattern,
        starting: POS,
        boundary: POS | None = None,
        parsers: dict[int, "GrammarParser"] = {},
        **kwargs,
    ) -> (tuple[POS, POS] | None, str, "list[Captures]"):
        matching, span = handler.search(pattern, starting=starting, boundary=boundary, **kwargs)

        if matching:
            captures = Captures(handler, pattern, matching, parsers, starting, boundary, **kwargs)
            return span, matching.group(), [captures]
        else:
            return span, "", []

    @abstractmethod
    def _parse(
        self,
        handler: ContentHandler,
        starting: POS,
        boundary: POS | None = None,
        verbosity: int = 0,
        **kwargs,
    ) -> (bool, list[ContentElement], tuple[int, int] | None):
        pass


class TokenParser(GrammarParser):
    """The parser for grammars for which only the token is provided."""

    def __init__(self, grammar: dict, **kwargs) -> None:
        super().__init__(grammar, **kwargs)
        self.initialized = True

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}:{self.token}"

    def _parse(
        self,
        handler: ContentHandler,
        starting: POS,
        boundary: POS,
        verbosity: int = 0,
        **kwargs,
    ) -> (bool, list[ContentElement], tuple[POS, POS] | None):
        """The parse method for grammars for which only the token is provided.

        When no regex patterns are provided. The element is created between the initial and boundary positions.
        """
        content = handler.read_pos(starting, boundary)
        elements = [
            ContentElement(
                token=self.token, grammar=self.grammar, content=content, indices=handler.range(starting, boundary)
            )
        ]
        handler.anchor = boundary[1]
        LOGGER.info(f"{self.__class__.__name__} found < {repr(content)} >", self, starting, verbosity)
        return True, elements, (starting, boundary)


class MatchParser(GrammarParser):
    """The parser for grammars for which a match pattern is provided."""

    def __init__(self, grammar: dict, **kwargs) -> None:
        super().__init__(grammar, **kwargs)
        self.exp_match = re.compile(grammar["match"])
        self.parsers = self._init_captures(grammar, key="captures")
        if "\\G" in grammar["match"]:
            self.anchored = True

    def __repr__(self) -> str:
        if self.token:
            return f"{self.__class__.__name__}:{self.token}"
        else:
            identifier = self.key if self.key else "_".join(self.comment.lower().split(" "))
            return f"{self.__class__.__name__}:<{identifier}>"

    def initialize_repository(self) -> None:
        """When the grammar has patterns, this method should called to initialize its inclusions."""
        self.initialized = True
        for key, value in self.parsers.items():
            if not isinstance(value, GrammarParser):
                self.parsers[key] = self._find_include(value)
        for parser in self.parsers.values():
            if not parser.initialized:
                parser.initialize_repository()

    def _parse(
        self,
        handler: ContentHandler,
        starting: POS,
        boundary: POS | None = None,
        verbosity: int = 0,
        **kwargs,
    ) -> (bool, list[ContentElement], tuple[POS, POS] | None):
        """The parse method for grammars for which a match pattern is provided."""

        span, content, captures = self.match_and_capture(
            handler,
            pattern=self.exp_match,
            starting=starting,
            boundary=boundary,
            parsers=self.parsers,
            verbosity=verbosity,
            **kwargs,
        )

        if span is None:
            LOGGER.debug(f"{self.__class__.__name__} no match", self, starting, verbosity)
            return False, [], None

        LOGGER.info(f"{self.__class__.__name__} found < {repr(content)} >", self, starting, verbosity)

        if self.token:
            elements = [
                ContentElement(
                    token=self.token,
                    grammar=self.grammar,
                    content=content,
                    indices=handler.range(*span),
                    captures=captures,
                )
            ]
        else:
            elements = captures

        return True, elements, span


class PatternsParser(GrammarParser):
    """The parser for grammars for which several patterns are provided."""

    def __init__(self, grammar: dict, **kwargs) -> None:
        super().__init__(grammar, **kwargs)
        self.patterns = [self.initialize(pattern, language=self.language) for pattern in grammar.get("patterns", [])]

    def initialize_repository(self):
        """When the grammar has patterns, this method should called to initialize its inclusions."""
        self.initialized = True
        self.patterns = [
            parser if isinstance(parser, GrammarParser) else self._find_include(parser) for parser in self.patterns
        ]
        for parser in self.patterns:
            if not parser.initialized:
                parser.initialize_repository()

        pattern_parsers = [parser for parser in self.patterns if type(parser) == PatternsParser]
        for parser in pattern_parsers:
            parser_index = self.patterns.index(parser)
            self.patterns[parser_index : parser_index + 1] = parser.patterns

    def _parse(
        self,
        handler: ContentHandler,
        starting: POS,
        boundary: POS | None = None,
        allow_leading_all: bool = False,
        find_one: bool = True,
        injections: bool = False,
        verbosity: int = 0,
        **kwargs,
    ) -> tuple[bool, list[ContentElement], tuple[int, int]]:
        """The parse method for grammars for which a match pattern is provided."""

        if boundary is None:
            boundary = (len(handler.lines) - 1, handler.line_lengths[-1])

        parsed, elements = False, []
        patterns = [parser for parser in self.patterns if not parser.disabled]

        if find_one or injections:
            patterns = patterns + self.injected_patterns

        current = (starting[0], starting[1])

        while current < boundary:
            for parser in patterns:
                # Try to find patterns
                parsed, captures, span = parser._parse(
                    handler,
                    current,
                    boundary=boundary,
                    allow_leading_all=allow_leading_all,
                    verbosity=verbosity + 1,
                    **kwargs,
                )
                if parsed:
                    if find_one:
                        LOGGER.info(f"{self.__class__.__name__} found single element", self, current, verbosity)
                        return True, captures, span
                    elements.extend(captures)
                    current = span[1]
                    break
            else:
                if find_one:
                    break
                # Try again if previously allowed no leading white space charaters, only when multple patterns are to be found
                second_try_patterns = patterns if not allow_leading_all else []

                for parser in second_try_patterns:
                    parsed, captures, span = parser._parse(
                        handler, current, boundary=boundary, allow_leading_all=True, verbosity=verbosity + 1, **kwargs
                    )
                    if parsed:
                        if find_one:
                            LOGGER.info(f"{self.__class__.__name__} found single element", self, current, verbosity)
                            return True, captures, span
                        elements.extend(captures)
                        current = span[1]
                        break
                else:
                    break

            if current == starting:
                LOGGER.warning(
                    f"{self.__class__.__name__} handler did not move after a search round", self, starting, verbosity
                )
                break

        return bool(elements), elements, (starting, current)


class BeginEndParser(PatternsParser):
    """The parser for grammars for which a begin/end pattern is provided."""

    def __init__(self, grammar: dict, **kwargs) -> None:
        super().__init__(grammar, **kwargs)
        if "contentName" in grammar:
            self.token = grammar["contentName"]
            self.between_content = True
        else:
            self.token = grammar.get("name", None)
            self.between_content = False
        self.apply_end_pattern_last = grammar.get("applyEndPatternLast", False)
        self.exp_begin = re.compile(grammar["begin"])
        self.exp_end = re.compile(grammar["end"])
        self.parsers_begin = self._init_captures(grammar, key="beginCaptures")
        self.parsers_end = self._init_captures(grammar, key="endCaptures")
        if "\\G" in grammar["begin"]:
            self.anchored = True

    def __repr__(self) -> str:
        if self.token:
            return f"{self.__class__.__name__}:{self.token}"
        else:
            identifier = self.key if self.key else "_".join(self.comment.lower().split(" "))
            return f"{self.__class__.__name__}:<{identifier}>"

    def initialize_repository(self) -> None:
        """When the grammar has patterns, this method should called to initialize its inclusions."""
        self.initialized = True
        super().initialize_repository()
        for key, value in self.parsers_end.items():
            if not isinstance(value, GrammarParser):
                self.parsers_end[key] = self._find_include(value)
        for key, value in self.parsers_begin.items():
            if not isinstance(value, GrammarParser):
                self.parsers_begin[key] = self._find_include(value)
        for parser in self.parsers_begin.values():
            if not parser.initialized:
                parser.initialize_repository()
        for parser in self.parsers_end.values():
            if not parser.initialized:
                parser.initialize_repository()

    def _parse(
        self,
        handler: ContentHandler,
        starting: POS,
        boundary: POS | None = None,
        allow_leading_all: bool = False,
        verbosity: int = 0,
        **kwargs,
    ) -> (bool, list[ContentElement], tuple[POS, POS] | None):
        """The parse method for grammars for which a begin/end pattern is provided."""

        begin_span, _, begin_elements = self.match_and_capture(
            handler,
            self.exp_begin,
            starting,
            boundary=boundary,
            parsers=self.parsers_begin,
            allow_leading_all=allow_leading_all,
        )

        if not begin_span:
            LOGGER.debug(f"{self.__class__.__name__} no begin match", self, starting, verbosity)
            return False, [], None
        LOGGER.info(f"{self.__class__.__name__} found begin", self, starting, verbosity)

        # Get initial and boundary positions
        current = begin_span[1]
        if boundary is None:
            boundary = (len(handler.lines) - 1, handler.line_lengths[-1])

        # Define loop parameters
        end_elements, mid_elements = [], []
        patterns = [parser for parser in self.patterns if not parser.disabled]
        first_run = True

        while current <= boundary:
            parsed = False

            # Create boolean that is enabled when a parser is recursively called. In this its end pattern should
            # be applied last, otherwise the same span will be recognzed as the end pattern by the upper level parser
            apply_end_pattern_last = False

            # Try to find patterns first with no leading whitespace charaters allowed
            for parser in patterns:
                parsed, capture_elements, capture_span = parser._parse(
                    handler, current, boundary=boundary, allow_leading_all=False, verbosity=verbosity + 1, **kwargs
                )
                if parsed:
                    if parser == self:
                        apply_end_pattern_last = True
                    LOGGER.debug(f"{self.__class__.__name__} found pattern (no ws)", self, current, verbosity)
                    break

            # Try to find the end pattern with no leading whitespace charaters allowed
            end_span, _, end_elements = self.match_and_capture(
                handler,
                self.exp_end,
                current,
                boundary=boundary,
                parsers=self.parsers_end,
                allow_leading_all=False,
            )

            if not parsed and not end_span:
                # Try to find the patterns and end pattern allowing for leading whitespace charaters
                for parser in patterns:
                    parsed, capture_elements, capture_span = parser._parse(
                        handler, current, boundary=boundary, allow_leading_all=True, verbosity=verbosity + 1, **kwargs
                    )
                    if parsed:
                        if parser == self:
                            apply_end_pattern_last = True
                        LOGGER.debug(f"{self.__class__.__name__} found pattern (ws)", self, current, verbosity)
                        break

                end_span, end_content, end_elements = self.match_and_capture(
                    handler,
                    self.exp_end,
                    current,
                    boundary=boundary,
                    parsers=self.parsers_end,
                    allow_leading_all=True,
                )

            if end_span:
                if parsed:
                    # Check whether the capture pattern has the same closing positions as the end pattern
                    capture_before_end = handler.prev(capture_span[1])
                    if handler.read_length(capture_before_end, 1, skip_newline=False) == "\n":
                        # If capture pattern ends with \n, both left and right of \n is considered end
                        pattern_at_end = end_span[1] in [capture_before_end, capture_span[1]]
                    else:
                        pattern_at_end = end_span[1] == capture_span[1]

                    end_before_pattern = end_span[0] <= capture_span[0]
                    empty_span_end = end_span[1] == end_span[0]

                    if pattern_at_end and (end_before_pattern or empty_span_end):
                        if empty_span_end:
                            # Both found capture pattern and end pattern are accepted, break pattern search
                            LOGGER.debug(
                                f"{self.__class__.__name__} capture+end: both accepted, break",
                                self,
                                current,
                                verbosity,
                            )
                            mid_elements.extend(capture_elements)
                            closing = end_span[0] if self.between_content else end_span[1]
                            break
                        elif not self.apply_end_pattern_last and not apply_end_pattern_last:
                            # End pattern prioritized over capture pattern, break pattern search
                            LOGGER.debug(
                                f"{self.__class__.__name__} capture+end: end prioritized, break",
                                self,
                                current,
                                verbosity,
                            )
                            closing = end_span[0] if self.between_content else end_span[1]
                            break
                        else:
                            # Capture pattern prioritized over end pattern, continue pattern search
                            LOGGER.debug(
                                f"{self.__class__.__name__} capture+end: capture prioritized, continue",
                                self,
                                current,
                                verbosity,
                            )
                            mid_elements.extend(capture_elements)
                            current = capture_span[1]

                    elif capture_span[0] < end_span[0]:
                        # Capture pattern found before end pattern, continue pattern search
                        LOGGER.debug(
                            f"{self.__class__.__name__} capture<end: leading capture, continue",
                            self,
                            current,
                            verbosity,
                        )
                        mid_elements.extend(capture_elements)
                        current = capture_span[1]
                    else:
                        # End pattern found before capture pattern, break pattern search
                        LOGGER.debug(
                            f"{self.__class__.__name__} end<capture: leading end, break",
                            self,
                            current,
                            verbosity,
                        )
                        closing = end_span[0] if self.between_content else end_span[1]
                        break
                else:
                    # No capture pattern found, accept end pattern and break pattern search
                    LOGGER.debug(f"{self.__class__.__name__} end: break", self, current, verbosity)
                    closing = end_span[0] if self.between_content else end_span[1]
                    break
            else:  # No end pattern found
                if parsed:
                    # Append found capture pattern and find next starting position
                    mid_elements.extend(capture_elements)

                    if handler.read_length(capture_span[1], 1, skip_newline=False) == "\n":
                        # Next character after capture pattern is newline

                        LOGGER.debug(
                            f"{self.__class__.__name__} capture: next is newline, continue",
                            self,
                            current,
                            verbosity,
                        )

                        end_span, _, _ = self.match_and_capture(
                            handler,
                            self.exp_end,
                            capture_span[1],
                            boundary=boundary,
                            parsers=self.parsers_end,
                            allow_leading_all=False,
                        )

                        if end_span and end_span[1] <= handler.next(capture_span[1]):
                            # Potential end pattern can be found directly after the found capture pattern
                            current = capture_span[1]
                        else:
                            # Skip the newline character in the next pattern search round
                            current = handler.next(capture_span[1])
                    else:
                        LOGGER.debug(f"{self.__class__.__name__} capture: continue", self, current, verbosity)
                        current = capture_span[1]
                else:
                    # No capture patterns nor end patterns found. Skip the current line.
                    line = handler.read_line(current)

                    if line and not line.isspace() :
                        LOGGER.warning(
                            f"No patterns found in line, skipping < {repr(line)} >", self, current, verbosity
                        )
                    current = handler.next((current[0], handler.line_lengths[current[0]]))

            if apply_end_pattern_last:
                current = handler.next(current)

            if first_run:
                # Skip all parsers that were anchored to the begin pattern after the first round
                patterns = [parser for parser in patterns if not parser.anchored]
                first_run = False
        else:
            # Did not break out of while loop, set closing to boundary
            closing = boundary
            end_span = (None, boundary)

        start = begin_span[1] if self.between_content else begin_span[0]

        content = handler.read_pos(start, closing)
        LOGGER.info(f"{self.__class__.__name__} found < {repr(content)} >", self, start, verbosity)

        # Construct output elements
        if self.token:
            elements = [
                ContentBlockElement(
                    token=self.token,
                    grammar=self.grammar,
                    content=content,
                    indices=handler.range(start, closing),
                    captures=mid_elements,
                    begin=begin_elements,
                    end=end_elements,
                )
            ]
        else:
            elements = begin_elements + mid_elements + end_elements

        return True, elements, (begin_span[0], end_span[1])


class BeginWhileParser(PatternsParser):
    """The parser for grammars for which a begin/end pattern is provided."""

    def __init__(self, grammar: dict, **kwargs) -> None:
        super().__init__(grammar, **kwargs)
        if "contentName" in grammar:
            self.token = grammar["contentName"]
            self.between_content = True
        else:
            self.token = grammar.get("name", None)
            self.between_content = False
        self.exp_begin = re.compile(grammar["begin"])
        self.exp_while = re.compile(grammar["while"])
        self.parsers_begin = self._init_captures(grammar, key="beginCaptures")
        self.parsers_while = self._init_captures(grammar, key="whileCaptures")

    def __repr__(self) -> str:
        if self.token:
            return f"{self.__class__.__name__}:{self.token}"
        else:
            identifier = self.key if self.key else "_".join(self.comment.lower().split(" "))
            return f"{self.__class__.__name__}:<{identifier}>"

    def initialize_repository(self):
        """When the grammar has patterns, this method should called to initialize its inclusions."""
        self.initialized = True
        super().initialize_repository()
        for key, value in self.parsers_end.items():
            if not isinstance(value, GrammarParser):
                self.parsers_end[key] = self._find_include(value)
        for key, value in self.parsers_while.items():
            if not isinstance(value, GrammarParser):
                self.parsers_while[key] = self._find_include(value)
        for parser in self.parsers_begin.values():
            if not parser.initialized:
                parser.initialize_repository()
        for parser in self.parsers_while.values():
            if not parser.initialized:
                parser.initialize_repository()

    def _parse(
        self,
        handler: ContentHandler,
        starting: POS,
        boundary: POS | None = None,
        verbosity: int = 0,
        **kwargs,
    ) -> (bool, list[ContentElement], tuple[POS, POS] | None):
        """The parse method for grammars for which a begin/while pattern is provided."""
        raise NotImplementedError
