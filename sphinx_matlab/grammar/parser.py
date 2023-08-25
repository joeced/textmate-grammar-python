# %%
from .exceptions import IncludedParserNotFound, CannotCloseEnd, RegexGroupsMismatch
from .elements import ParsedElement, ParsedElementBlock
from typing import Tuple, Dict, List, Union, Optional
from io import StringIO, SEEK_END
import onigurumacffi as re
from onigurumacffi import _Pattern as Pattern


regex_nextline = re.compile("\n|$")
REGEX_LOOKBEHIND_MAX = 100
REGEX_LOOKBEHIND_STEP = 5


class GrammarParser(object):
    PARSERSTORE = {}
    STREAMENDPOS = {}

    def __init__(self, grammar: dict, key: str = "", **kwargs) -> None:
        self.grammar = grammar
        self.key = key
        self.token = grammar.get("name", "")
        self.contentToken = grammar.get("contentName", "")
        self.comment = grammar.get("comment", "")
        self.match, self.begin, self.end = None, None, None
        self.captures, self.beginCaptures, self.endCaptures = {}, {}, {}
        self.patterns = []

        if key:
            self.PARSERSTORE[key] = self

        if "match" in grammar:
            self.match = re.compile(grammar["match"])
            self.captures = self.__init_captures(grammar, key="captures")
        elif "begin" in grammar and "end" in grammar:
            self.begin = re.compile(grammar["begin"])
            self.end = re.compile(grammar["end"])
            self.beginCaptures = self.__init_captures(grammar, key="beginCaptures")
            self.endCaptures = self.__init_captures(grammar, key="endCaptures")
        if "patterns" in grammar:
            self.patterns = [self.set_parser(pattern) for pattern in grammar["patterns"]]

    def __init_captures(self, grammar: dict, key: str = "captures") -> dict:
        captures = {}
        if key in grammar:
            for groupId, pattern in grammar[key].items():
                captures[int(groupId)] = self.set_parser(pattern)
        return captures

    def __call__(self, *args, **kwargs):
        return self.parse(*args, **kwargs)

    def __repr__(self) -> str:
        repr = f"{self.__class__.__name__}:"
        if self.token or self.key:
            repr += self.token if self.token else f"<{self.key}>"
        else:
            repr += "PATTERN"
        return repr

    def set_parser(self, grammar: dict, **kwargs):
        if "include" in grammar:
            if grammar["include"] == "$self":
                return self
            else:
                return grammar["include"][1:]
        else:
            return GrammarParser(grammar, **kwargs)

    @classmethod
    def get_parser(cls, callId: Union[str, "GrammarParser"]):
        if isinstance(callId, str):
            if callId in cls.PARSERSTORE:
                callId = cls.PARSERSTORE[callId]
            else:
                raise IncludedParserNotFound(callId)
        return callId

    @classmethod
    def stream_end_pos(cls, stream: StringIO) -> int:
        if stream in cls.STREAMENDPOS:
            return cls.STREAMENDPOS[stream]
        else:
            currentPos = stream.tell()
            closePos = stream.seek(0, SEEK_END)
            stream.seek(currentPos)
            return closePos

    @staticmethod
    def stream_read_pos(stream: StringIO, start: int, end: int) -> str:
        stream.seek(start)
        content = stream.read(end - start)
        stream.seek(end)
        return content

    @staticmethod
    def nextline_pos(stream: StringIO, start: int) -> int:
        currentPos = stream.tell()
        stream.seek(start)
        text = stream.read()
        stream.seek(currentPos)
        return regex_nextline.search(text).start() + start

    def parse(
        self, stream: StringIO, startPos: int = 0, closePos: Optional[int] = None, **kwargs
    ) -> Tuple[bool, List[ParsedElement], Optional[Tuple[int, int]]]:
        """Parse the input stream using the current parser."""

        streamEndPos = self.stream_end_pos(stream)
        stream.seek(startPos)

        if self.match:
            captures, span = self.search(
                self.match,
                stream,
                parsers=self.captures,
                readSize=closePos - startPos + 1 if closePos is not None else None,
                **kwargs,
            )
            if span is None:
                return False, [], None
            elements = [ParsedElement(token=self.token, stream=stream, span=span, captures=captures)]
            parsedStart, parsedEnd = span

        elif self.begin and self.end:
            # Find begin
            beginCaptured, beginSpan = self.search(
                self.begin,
                stream,
                parsers=self.beginCaptures,
                readSize=closePos - startPos + 1 if closePos is not None else None,
                **kwargs,
            )
            if beginSpan is None:
                return False, [], None
            (parsedStart, midStartPos) = beginSpan

            # Fist attempt at finding an end
            endCaptured, endSpan = self.search(
                self.end,
                stream,
                parsers=self.endCaptures,
                readSize=closePos - midStartPos + 1 if closePos is not None else -1,
                onlyLeadingWhiteSpace=False,
                **kwargs,
            )
            if endSpan is None:
                return False, [], None
            (midClosePos, parsedEnd) = endSpan

            # Find content
            midCaptured, recursionFound = [], False
            if self.patterns:
                # Defines where to start looking for a pattern
                patternStartPos = midStartPos
                lastPatternStartPos = None

                # Get parsers and create lookup store
                parsers = [self.get_parser(callId) for callId in self.patterns]
                foundPatternElem = {parser: None for parser in parsers}
                foundPatternSpan = {parser: None for parser in parsers}

                while patternStartPos < midClosePos:
                    if patternStartPos == lastPatternStartPos:
                        break

                    # Find the next match per parser until the end of suspected end (midClosePos)
                    parsersElem, parsersSpan = [], []
                    for parser in parsers:  # Find match per parser
                        if (startPos, closePos) == (patternStartPos, midClosePos) and parser is self:
                            # Prevent recursion when start end are the same as level above
                            parsers = [parser for parser in parsers if parser is not self]
                            recursionFound = True
                            continue
                        if (
                            foundPatternElem[parser] is not None
                            and foundPatternSpan[parser][0] >= patternStartPos
                        ):
                            # Use previous found match for current parser
                            parsersElem.append(foundPatternElem[parser])
                            parsersSpan.append(foundPatternSpan[parser])
                        else:  # Try to find new match for current parser
                            matched, elem, span = parser.parse(
                                stream, startPos=patternStartPos, closePos=midClosePos, **kwargs
                            )
                            if matched:
                                parsersElem.append(elem)
                                parsersSpan.append(span)
                                foundPatternElem[parser], foundPatternSpan[parser] = elem, span

                    if parsersElem:  # There are patterns found
                        # Sort by best found pattern (starting earliest and longest)
                        bestSpan = sorted(parsersSpan, key=lambda x: (x[0], x[0] - x[1]))[0]
                        bestElem = parsersElem[parsersSpan.index(bestSpan)]

                        if bestSpan[0] <= midClosePos:
                            # Valid best element, starting before suspected end
                            midCaptured += bestElem
                            lastPatternStartPos, patternStartPos = patternStartPos, bestSpan[1]
                            stream.seek(patternStartPos)

                            if patternStartPos > midClosePos:
                                # Suspected end invalid, try to find new end
                                endCaptured, endSpan = self.search(
                                    self.end,
                                    stream,
                                    parsers=self.endCaptures,
                                    readSize=closePos - patternStartPos + 1 if closePos is not None else -1,
                                    onlyLeadingWhiteSpace=False,
                                    **kwargs,
                                )
                                if endSpan is None:
                                    return False, [], None
                                (midClosePos, parsedEnd) = endSpan
                        else:
                            break

                    else:  # No patterns found, suspected end (midClosePos) is true
                        break

            stream.seek(midClosePos)

            # Create element
            if recursionFound:
                # In recursive child, return captured as elements
                elements = midCaptured
            elif self.contentToken:
                # Return with only mid as content of element
                elements = [
                    ParsedElementBlock(
                        token=self.contentToken,
                        stream=stream,
                        span=(midStartPos, midClosePos),
                        captures=midCaptured,
                        begin=beginCaptured if beginCaptured else None,
                        end=endCaptured if endCaptured else None,
                    )
                ]
            else:
                # Return with begin and end included in content of element
                elements = [
                    ParsedElementBlock(
                        token=self.token if self.token else self.comment,
                        stream=stream,
                        span=(parsedStart, parsedEnd),
                        captures=midCaptured,
                        begin=beginCaptured if beginCaptured else None,
                        end=endCaptured if endCaptured else None,
                    )
                ]

        elif self.patterns:
            parsedEnd = closePos if closePos else streamEndPos

            # Defines where to start looking for a pattern
            patternStartPos = startPos
            lastPatternStartPos = None

            # Get parsers and create lookup store
            parsers = [self.get_parser(callId) for callId in self.patterns]
            foundPatternElem = {parser: None for parser in parsers}
            foundPatternSpan = {parser: None for parser in parsers}
            captured = []

            while patternStartPos < parsedEnd:
                if patternStartPos == lastPatternStartPos:
                    break

                # Find the next match per parser until the end of suspected end (parsedEnd)
                parsersElem, parsersSpan = [], []
                for parser in parsers:  # Find match per parser
                    if (startPos, closePos) == (patternStartPos, parsedEnd) and parser is self:
                        # Prevent recursion when start end are the same as level above
                        parsers = [parser for parser in parsers if parser is not self]
                        recursionFound = True
                        continue
                    if (
                        foundPatternElem[parser] is not None
                        and foundPatternSpan[parser][0] >= patternStartPos
                    ):
                        # Use previous found match for current parser
                        parsersElem.append(foundPatternElem[parser])
                        parsersSpan.append(foundPatternSpan[parser])
                    else:  # Try to find new match for current parser
                        matched, elem, span = parser.parse(
                            stream, startPos=patternStartPos, closePos=parsedEnd, **kwargs
                        )
                        if matched:
                            parsersElem.append(elem)
                            parsersSpan.append(span)
                            foundPatternElem[parser], foundPatternSpan[parser] = elem, span

                if parsersElem:  # There are patterns found
                    # Sort by best found pattern (starting earliest and longest)
                    bestSpan = sorted(parsersSpan, key=lambda x: (x[0], x[0] - x[1]))[0]
                    bestElem = parsersElem[parsersSpan.index(bestSpan)]

                    if bestSpan[0] <= parsedEnd:
                        # Valid best element, starting before suspected end
                        if not captured:
                            parsedStart = bestSpan[0]
                        captured += bestElem
                        lastPatternStartPos, patternStartPos = patternStartPos, bestSpan[1]
                        stream.seek(patternStartPos)
                    else:
                        break

                else:  # No patterns found, suspected end (midClosePos) is true
                    break

            patternClosePos = stream.tell()

            if captured:
                if self.token:
                    elements = [
                        ParsedElement(
                            token=self.token,
                            stream=stream,
                            span=(parsedStart, patternClosePos),
                            captures=captured,
                        )
                    ]
                else:
                    elements = captured
            else:
                return False, [], None

        else:
            if closePos is not None:
                parsedStart, parsedEnd = startPos, closePos
                elements = [
                    ParsedElement(
                        token=self.token if self.token else self.comment,
                        stream=stream,
                        span=(startPos, closePos),
                    )
                ]
            else:
                Exception("This should not happen")

        return True, elements, (parsedStart, parsedEnd)

    def search(
        self,
        regex: Pattern,
        stream: StringIO,
        parsers: Dict[int, "GrammarParser"] = [],
        readSize: Optional[int] = None,
        **kwargs,
    ) -> Tuple[List[ParsedElement], Optional[Tuple[int, int]]]:
        """Matches the stream against a capture group.

        The stream is matched against the input pattern. If there are any capture groups,
        each is then subsequently parsed by the inputted parsers. The number of parsers therefor
        must match the number of capture groups of the expression, or there must be a single parser
        and no capture groups.
        """

        initPos = stream.tell()
        lookbehind, streamCanLookbehind = 0, True
        performLookbehind = "(?<" in regex._pattern

        while lookbehind <= REGEX_LOOKBEHIND_MAX and streamCanLookbehind:
            if not performLookbehind:  # Only perform while loop once
                streamCanLookbehind = False
            if (initPos - lookbehind) < 0:  # Set to start of stream of lookbehind is maximized
                lookbehind, streamCanLookbehind = initPos, False

            if readSize:
                linePos = 0
                stream.seek(initPos - lookbehind)
                lines = [
                    line + "\n" for line in stream.read(readSize + lookbehind).split("\n")
                ]  # Add newline as tmlanguage expects it.
                for line in lines:
                    matching = regex.search(line)
                    if matching:
                        break  # Find first match encountered per line
                    linePos += len(line)
                else:
                    lookbehind += REGEX_LOOKBEHIND_STEP
                    continue
                matchingToStreamPos = linePos + initPos - lookbehind
            else:
                stream.seek(initPos - lookbehind)
                line = stream.readline()
                matching = regex.search(line)
                if not matching: 
                    lookbehind += REGEX_LOOKBEHIND_STEP
                    continue
                matchingToStreamPos = initPos + lookbehind

            if matching is not None:
                break
        else:
            stream.seek(initPos)
            return [], None

        startPos = matchingToStreamPos + matching.start()
        closePos = matchingToStreamPos + matching.end()
        stream.seek(startPos)

        # No groups, but a parser existed. Use token of parser to create element
        if 0 in parsers:
            elements = [ParsedElement(token=parsers[0].token, stream=stream, span=(startPos, closePos))]
        # Parse each capture group
        elif parsers:
            elements = []
            for groupId, parser in parsers.items():
                group = matching.group(groupId)
                if not group:
                    continue
                span = matching.span(groupId)
                groupMatched, parsed_elements, _ = parser.parse(
                    stream, startPos=span[0] + matchingToStreamPos, closePos=span[1] + matchingToStreamPos
                )
                if not groupMatched:
                    stream.seek(initPos)
                    return [], None
                elements += parsed_elements
            if not elements:
                return [], (startPos, closePos)
        # No parsers
        else:
            elements = []

        stream.seek(closePos)
        return elements, (startPos, closePos)
