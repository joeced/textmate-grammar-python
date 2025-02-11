import logging
import pytest
from pathlib import Path

from textmate_grammar.grammars import matlab
from textmate_grammar.language import LanguageParser

from . import MODULE_ROOT, RegressionTestClass

logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger("textmate_grammar").setLevel(logging.INFO)
parser = LanguageParser(matlab.GRAMMAR)

test_files = (
    [
        str(MODULE_ROOT.parents[1] / "syntaxes" / "matlab" / (file + ".m"))
        for file in [
            "Account",
            "AnEnum",
            "argumentValidation",
            "CircleArea",
            "controlFlow",
            "lineContinuations",
            "PropertyValidation",
        ]
    ]
    + [str(test) for test in (MODULE_ROOT.parents[1] / "syntaxes" / "matlab" / "test").glob("*.m")]
    + [
        str(Path(__file__).parent.resolve() / "matlab" / (file + ".m"))
        for file in ["test_multiple_inheritance_ml"]
    ]
)


@pytest.mark.parametrize("source", test_files)
class TestMatlabRegression(RegressionTestClass):
    """The regression test class for MATLAB."""

    scope = "source.matlab"

    @property
    def parser(self):
        return parser

    def test(self, source: Path | str):
        py_tokens = self.parse_python(source)
        node_tokens = self.parse_node(source)
        assert py_tokens == node_tokens
