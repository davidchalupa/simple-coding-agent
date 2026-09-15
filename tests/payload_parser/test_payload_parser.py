import pytest

from coding_agent.payload_parser import parse_robust_tool_call


def test_write_file_normal_json_escapes():
    tool_json = (
        '{"name":"write_file","args":{"filepath":"example.py",'
        '"content":"def hello():\\n    print(\\"hello\\")\\n"}}'
    )

    result = parse_robust_tool_call(tool_json, tool_json)

    assert result["name"] == "write_file"
    assert result["args"]["filepath"] == "example.py"
    assert result["args"]["content"] == (
        'def hello():\n'
        '    print("hello")\n'
    )


def test_write_file_double_escaped_newlines():
    tool_json = (
        '{"name":"write_file","args":{"filepath":"example.py",'
        '"content":"def hello():\\\\n    print(\\"hello\\")\\\\n"}}'
    )

    result = parse_robust_tool_call(tool_json, tool_json)

    assert result["args"]["content"] == (
        'def hello():\n'
        '    print("hello")\n'
    )


def test_write_file_literal_backslash_n_is_preserved():
    tool_json = (
        '{"name":"write_file","args":{"filepath":"example.py",'
        '"content":"print(\\"\\\\n\\")\\n"}}'
    )

    result = parse_robust_tool_call(tool_json, tool_json)

    assert result["args"]["content"] == 'print("\\n")\n'



def test_write_file_multiline_python_with_quotes():
    tool_json = (
        '{"name":"write_file","args":{"filepath":"example.py",'
        '"content":"def foo(x):\\n'
        '    if x == \\"hello\\":\\n'
        '        return \\"world\\"\\n"}}'
    )

    result = parse_robust_tool_call(tool_json, tool_json)

    assert result["args"]["content"] == (
        'def foo(x):\n'
        '    if x == "hello":\n'
        '        return "world"\n'
    )


def test_replace_lines_json_content():
    tool_json = (
        '{"name":"replace_lines","args":{'
        '"filepath":"example.py",'
        '"start_line":10,'
        '"end_line":12,'
        '"expected_start_snippet":"def foo():",'
        '"expected_end_snippet":"def bar():",'
        '"content":"def replacement():\\n    return 42\\n"'
        '}}'
    )

    result = parse_robust_tool_call(tool_json, tool_json)

    assert result["name"] == "replace_lines"
    assert result["args"]["start_line"] == 10
    assert result["args"]["end_line"] == 12
    assert result["args"]["content"] == (
        'def replacement():\n'
        '    return 42\n'
    )


def test_replace_lines_external_payload():
    tool_json = (
        '{"name":"replace_lines","args":{'
        '"filepath":"example.py",'
        '"start_line":10,'
        '"end_line":12,'
        '"expected_start_snippet":"def foo():",'
        '"expected_end_snippet":"def bar():"'
        '}}'
    )

    response = (
        '<tool_call>'
        + tool_json
        + '</tool_call>'
        '<payload>\n'
        'def replacement():\n'
        '    print("hello")\n'
        '</payload>'
    )

    result = parse_robust_tool_call(
        response,
        tool_json,
        search_start=response.index("</tool_call>") + len("</tool_call>")
    )

    assert result["name"] == "replace_lines"
    assert result["args"]["content"] == (
        'def replacement():\n'
        '    print("hello")'
    )

def test_write_file_escaping_heavy_python_content():
    tool_json = (
        '{"name":"write_file","args":{"filepath":"example.py",'
        '"content":"import re\\n'
        'path = \\"C:\\\\\\\\Users\\\\\\\\test\\\\\\\\file.py\\"\\n'
        'pattern = re.compile(r\\"^foo\\\\\\\\d+$\\")\\n'
        'text = \\"line1\\\\nline2\\"\\n'
        'print(text)\\n"}}'
    )

    result = parse_robust_tool_call(tool_json, tool_json)

    assert result["args"]["content"] == (
        'import re\n'
        'path = "C:\\\\Users\\\\test\\\\file.py"\n'
        'pattern = re.compile(r"^foo\\\\d+$")\n'
        'text = "line1\\nline2"\n'
        'print(text)\n'
    )

def test_replace_lines_payload_with_blank_lines_and_indentation():
    tool_json = (
        '{"name":"replace_lines","args":{'
        '"filepath":"example.py",'
        '"start_line":10,'
        '"end_line":15,'
        '"expected_start_snippet":"def foo():",'
        '"expected_end_snippet":"def bar():"'
        '}}'
    )

    response = (
        '<tool_call>' + tool_json + '</tool_call>'
        '<payload>\n'
        'def replacement():\n'
        '    value = "hello"\n'
        '\n'
        '    return value\n'
        '</payload>'
    )

    result = parse_robust_tool_call(
        response,
        tool_json,
        search_start=response.index("</tool_call>") + len("</tool_call>")
    )

    assert result["args"]["content"] == (
        'def replacement():\n'
        '    value = "hello"\n'
        '\n'
        '    return value'
    )

def test_write_file_double_escaped_newlines_with_python_string_literal():
    tool_json = (
        '{"name":"write_file","args":{"filepath":"benchmark.py",'
        '"content":"import random\\\\n'
        'print(\\"hello\\\\nworld\\")\\\\n'
        'print(\\"done\\")"}}'
    )

    result = parse_robust_tool_call(tool_json, tool_json)

    assert result["args"]["content"] == (
        'import random\n'
        'print("hello\\nworld")\n'
        'print("done")'
    )
