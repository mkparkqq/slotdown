"""Static checks: cross-reference the template's variable usage with the schema."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from jinja2 import TemplateSyntaxError, meta, nodes

from slotdown.config import Config
from slotdown.loader import Document
from slotdown.render import make_environment
from slotdown.schema import RESERVED_NAMES, DocSchema, FieldSpec


@dataclass
class Issue:
    level: Literal["error", "warning"]
    message: str
    line: int | None = None

    def __str__(self) -> str:
        where = f"line {self.line}: " if self.line else ""
        return f"{self.level}: {where}{self.message}"


@dataclass(frozen=True)
class _Ref:
    """A template expression resolved to a schema field."""

    spec: FieldSpec
    path: str


# Filters whose result is the same list (possibly reordered/filtered), so loop
# variables over `xs | sort` still resolve to the list's item spec.
_LIST_PRESERVING_FILTERS = frozenset(
    {"list", "reverse", "sort", "unique", "select", "reject", "selectattr", "rejectattr"}
)
# A `.sd` file is self-contained: there is no loader to resolve other templates.
_EXTERNAL_TEMPLATE_NODES = (nodes.Include, nodes.Import, nodes.FromImport, nodes.Extends)

# Attributes Jinja can reach on scalar/list values (methods such as `upper`, `count`).
_VALUE_ATTRS = {
    "string": set(dir(str)),
    "integer": set(dir(int)),
    "number": set(dir(float)),
    "boolean": set(dir(bool)),
    "enum": set(dir(str)) | set(dir(int)) | set(dir(float)),
    "list": set(dir(list)),
}

# `x is defined` / `x is none` only inspect presence, not the value's contents.
_TRUTHINESS_TESTS = frozenset({"defined", "undefined", "none"})


def check_document(doc: Document, config: Config | None = None) -> list[Issue]:
    env = make_environment(config or Config())
    offset = doc.template_line_offset
    try:
        ast = env.parse(doc.template)
    except TemplateSyntaxError as exc:
        return [Issue("error", f"template syntax error: {exc.message}", _line(exc.lineno, offset))]

    issues: list[Issue] = []
    schema = doc.schema

    known = set(schema.fields) | {"inputs"} | set(env.globals)
    for name in sorted(meta.find_undeclared_variables(ast) - known):
        first = next((n for n in ast.find_all((nodes.Name, nodes.NSRef)) if n.name == name), None)
        line = _line(first.lineno, offset) if first else None
        issues.append(Issue("error", f"'{name}' is not defined in fields or inputs", line))

    analyzer = _Analyzer(schema, offset)
    analyzer.visit(ast, {})
    issues.extend(analyzer.issues)
    issues.extend(analyzer.unused_warnings())
    return issues


def _line(lineno: int | None, offset: int) -> int | None:
    return lineno + offset if lineno else None


class _Analyzer:
    def __init__(self, schema: DocSchema, offset: int):
        self.offset = offset
        self.issues: list[Issue] = []
        self.fields = schema.fields
        self.inputs = _Ref(FieldSpec(name="inputs", type="object", fields=schema.inputs), "inputs")
        # Unused-field warnings cover AI-generated fields only: inputs often exist
        # just to give the AI context (e.g. raw notes) and never reach the template.
        # path -> parent reportable path (None for top-level fields)
        self.declared: dict[str, str | None] = {}
        for name, spec in schema.fields.items():
            self._declare(spec, name, None)
        self.used: set[str] = set()
        self.used_whole: set[str] = set()

    # -- usage bookkeeping -------------------------------------------------

    def _declare(self, spec: FieldSpec, path: str, parent: str | None) -> None:
        self.declared[path] = parent
        self._declare_children(spec, path, path)

    def _declare_children(self, spec: FieldSpec, path: str, reportable: str) -> None:
        if spec.type == "object":
            for name, child in spec.fields.items():
                self._declare(child, f"{path}.{name}", reportable)
        elif spec.type == "list" and spec.items is not None:
            self._declare_children(spec.items, f"{path}[]", reportable)

    def _mark(self, ref: _Ref, whole: bool) -> None:
        self.used.add(ref.path)
        if whole:
            self.used_whole.add(ref.path)

    def _is_used(self, path: str) -> bool:
        if path in self.used:
            return True
        return any(path.startswith(w) and path[len(w)] in ".[" for w in self.used_whole if len(path) > len(w))

    def unused_warnings(self) -> list[Issue]:
        warnings = []
        for path, parent in self.declared.items():
            if self._is_used(path):
                continue
            # Report only the outermost unused field; its children are implied.
            if parent is None or self._is_used(parent):
                warnings.append(Issue("warning", f"'{path}' is defined but never used in the template"))
        return warnings

    def _error(self, node: nodes.Node, message: str) -> None:
        self.issues.append(Issue("error", message, _line(node.lineno, self.offset)))

    # -- traversal ---------------------------------------------------------

    def visit(self, node: nodes.Node, scope: dict[str, _Ref | None]) -> None:
        if isinstance(node, (nodes.Name, nodes.Getattr, nodes.Getitem)):
            ref = self.resolve(node, scope)
            if ref is not None:
                self._mark(ref, whole=True)
        elif isinstance(node, nodes.For):
            self._visit_for(node, scope)
        elif isinstance(node, _EXTERNAL_TEMPLATE_NODES):
            tag = type(node).__name__.lower().replace("fromimport", "from")
            self._error(node, f"'{{% {tag} %}}' is not supported: a .sd template cannot load other files")
        elif isinstance(node, (nodes.If, nodes.CondExpr)):
            self.visit_test(node.test, scope)
            for child in node.iter_child_nodes(exclude=("test",)):
                self.visit(child, scope)
        elif isinstance(node, nodes.Test) and node.name in _TRUTHINESS_TESTS:
            self.visit_test(node.node, scope)
        elif isinstance(node, (nodes.Assign, nodes.AssignBlock)):
            for child in node.iter_child_nodes(exclude=("target",)):
                self.visit(child, scope)
            self._shadow(node.target, scope)
        elif isinstance(node, (nodes.Macro, nodes.CallBlock)):
            for default in node.defaults:
                self.visit(default, scope)
            if isinstance(node, nodes.CallBlock):
                self.visit(node.call, scope)
            inner = dict(scope)
            for arg in node.args:
                self._shadow(arg, inner)
            for name in ("varargs", "kwargs", "caller"):
                inner[name] = None
            for child in node.body:
                self.visit(child, inner)
        elif isinstance(node, nodes.With):
            for value in node.values:
                self.visit(value, scope)
            inner = dict(scope)
            for target in node.targets:
                self._shadow(target, inner)
            for child in node.body:
                self.visit(child, inner)
        else:
            for child in node.iter_child_nodes():
                self.visit(child, scope)

    def visit_test(self, node: nodes.Node, scope: dict[str, _Ref | None]) -> None:
        """Visit an expression used only for its truthiness: `{% if xs %}` does not use `xs`'s children."""
        if isinstance(node, (nodes.Name, nodes.Getattr, nodes.Getitem)):
            ref = self.resolve(node, scope)
            if ref is not None:
                self._mark(ref, whole=False)
        elif isinstance(node, (nodes.Not, nodes.And, nodes.Or)):
            for child in node.iter_child_nodes():
                self.visit_test(child, scope)
        elif isinstance(node, nodes.Test) and node.name in _TRUTHINESS_TESTS:
            self.visit_test(node.node, scope)
        else:
            self.visit(node, scope)

    def _visit_for(self, node: nodes.For, scope: dict[str, _Ref | None]) -> None:
        iter_ref = self._resolve_iterable(node.iter, scope)
        inner = dict(scope)
        self._shadow(node.target, inner)
        inner["loop"] = None
        if (
            iter_ref is not None
            and iter_ref.spec.type == "list"
            and iter_ref.spec.items is not None
            and isinstance(node.target, nodes.Name)
        ):
            inner[node.target.name] = _Ref(iter_ref.spec.items, f"{iter_ref.path}[]")
        if node.test is not None:
            self.visit(node.test, inner)
        for child in node.body:
            self.visit(child, inner)
        for child in node.else_:
            self.visit(child, scope)

    def _resolve_iterable(self, node: nodes.Node, scope: dict[str, _Ref | None]) -> _Ref | None:
        if isinstance(node, nodes.Filter) and node.name in _LIST_PRESERVING_FILTERS and node.node is not None:
            for child in node.iter_child_nodes(exclude=("node",)):
                self.visit(child, scope)
            return self._resolve_iterable(node.node, scope)
        if isinstance(node, (nodes.Name, nodes.Getattr, nodes.Getitem)):
            ref = self.resolve(node, scope)
            if ref is not None:
                self._mark(ref, whole=False)
            return ref
        self.visit(node, scope)
        return None

    def _shadow(self, target: nodes.Node, scope: dict[str, _Ref | None]) -> None:
        for name_node in [target] if isinstance(target, nodes.Name) else target.find_all(nodes.Name):
            scope[name_node.name] = None

    # -- expression resolution ---------------------------------------------

    def resolve(self, node: nodes.Node, scope: dict[str, _Ref | None]) -> _Ref | None:
        """Resolve a Name/Getattr/Getitem chain to a schema field, marking parents as used."""
        if isinstance(node, nodes.Name):
            if node.name in scope:
                return scope[node.name]
            if node.name in self.fields:
                return _Ref(self.fields[node.name], node.name)
            if node.name == "inputs":
                return self.inputs
            return None

        if not isinstance(node, (nodes.Getattr, nodes.Getitem)):
            self.visit(node, scope)
            return None

        base = self.resolve(node.node, scope)
        if isinstance(node, nodes.Getitem) and not isinstance(node.arg, nodes.Const):
            self.visit(node.arg, scope)
        if base is None:
            return None

        key = node.attr if isinstance(node, nodes.Getattr) else getattr(node.arg, "value", None)

        if base.spec.type == "object" and isinstance(key, str):
            if key in base.spec.fields:
                self._mark(base, whole=False)
                return _Ref(base.spec.fields[key], f"{base.path}.{key}")
            if isinstance(node, nodes.Getattr) and key in RESERVED_NAMES:
                # dict method such as `inputs.items()`: the whole object is consumed.
                self._mark(base, whole=True)
                return None
            label = "inputs" if base is self.inputs else f"'{base.path}'"
            self._error(node, f"{label} has no field '{key}'")
            return None
        if (
            base.spec.type == "object"
            and isinstance(node, nodes.Getitem)
            and isinstance(node.arg, nodes.Const)
        ):
            self._error(node, f"'{base.path}' is an object; index it by field name, not {key!r}")
            return None

        if base.spec.type == "list" and base.spec.items is not None:
            if isinstance(node, nodes.Getitem) and isinstance(node.arg, nodes.Slice):
                self._mark(base, whole=False)
                return base
            if isinstance(node, nodes.Getitem) and not isinstance(key, str):
                self._mark(base, whole=False)
                return _Ref(base.spec.items, f"{base.path}[]")

            if isinstance(node, nodes.Getitem) and isinstance(key, str):
                self._error(node, f"'{base.path}' is a list; it has no field {key!r}")
                return None

        attrs = _VALUE_ATTRS.get(base.spec.type)
        if isinstance(node, nodes.Getattr) and attrs is not None and key not in attrs:
            self._error(node, f"'{base.path}' is a {base.spec.type}; it has no attribute '{key}'")
            return None
        if isinstance(node, nodes.Getitem) and base.spec.type != "list" and isinstance(key, str):
            self._error(node, f"'{base.path}' is a {base.spec.type}; it has no field {key!r}")
            return None

        # Method call or other access on a scalar/list value: the value is consumed as-is.
        self._mark(base, whole=True)
        return None
