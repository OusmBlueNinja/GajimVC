import ast
from pathlib import Path


def test_jmi_message_handler_is_not_scoped_to_child_namespace() -> None:
    """JMI lives on a child of <message/>, not on the stanza namespace itself."""

    module_path = Path(__file__).resolve().parents[1] / "gajim_calls" / "module.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    message_handlers = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "StanzaHandler":
            continue
        keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
        name = keywords.get("name")
        if isinstance(name, ast.Constant) and name.value == "message":
            message_handlers.append(keywords)

    assert len(message_handlers) == 1
    assert "ns" not in message_handlers[0]
