import sys
import logging
from pathlib import Path
from io import StringIO

sys.path.append(str(Path(__file__).parents[1]))
sys.path.append(str(Path(__file__).parents[3]))

import pytest
from textmate_grammar.language import LanguageParser
from textmate_grammar.grammars import matlab
from unit import MSG_NO_MATCH, MSG_NOT_PARSED


logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger("textmate_grammar").setLevel(logging.DEBUG)
parser = LanguageParser(matlab.GRAMMAR)
parser.initialize_repository()

test_vector = {}

# simple
test_vector["variable"] = {
    "token": "readwrite_operations",
    "content": "variable",
    "captures": [{"token": "", "content": "variable"}],
}

# property
test_vector["variable.property"] = {
    "token": "readwrite_operations",
    "content": "variable.property",
    "captures": [{"token": "", "content": "variable.property"}],
}

# subproperty
test_vector["variable.class.property"] = {
    "token": "readwrite_operations",
    "content": "variable.class.property",
    "captures": [{"token": "", "content": "variable.class.property"}],
}

# property access
test_vector["variable.property(0)"] = {
    "token": "readwrite_operations",
    "content": "variable",
    "captures": [{"token": "", "content": "variable"}],
}

# class method
test_vector["variable.function(argument)"] = {
    "token": "readwrite_operations",
    "content": "variable",
    "captures": [{"token": "", "content": "variable"}],
}


@pytest.mark.parametrize("check,expected", test_vector.items())
def test_readwrite_operation(check, expected):
    """Test read/write operations"""
    elements = parser.parse(StringIO(check))
    assert elements, MSG_NO_MATCH
    assert elements[0].to_dict() == expected, MSG_NOT_PARSED
