import libcst as cst
import libcst.metadata as cst_meta
from pathlib import Path

from refact_transform_utils import apply_to_file


# ---------------------------------------------------------------------------
# list_to_for_append
# ---------------------------------------------------------------------------
def _collect_names_in_node_list(node: cst.CSTNode) -> set[str]:
    """Collect all Name references within a node."""
    names = set()
    if isinstance(node, cst.Name):
        names.add(node.value)
    for child in node.children:
        if isinstance(child, cst.CSTNode):
            names |= _collect_names_in_node_list(child)
    return names


def _get_target_names_list(target) -> set[str]:
    """Recursively extract all variable names from a for-loop target."""
    names = set()
    if isinstance(target, cst.Name):
        names.add(target.value)
    elif isinstance(target, (cst.Tuple, cst.List)):
        for el in target.elements:
            names |= _get_target_names_list(el.value)
    elif isinstance(target, cst.StarredElement):
        names |= _get_target_names_list(target.value)
    return names

def _is_immutable_literal(node) -> bool:
    """Return True if node is an immutable literal (safe to repeat)."""
    if isinstance(node, (cst.Integer, cst.Float, cst.Imaginary, cst.SimpleString)):
        return True
    if isinstance(node, cst.Name) and node.value in ("True", "False", "None"):
        return True
    if isinstance(node, cst.Tuple):
        return all(_is_immutable_literal(el.value) for el in node.elements)
    if isinstance(node, cst.UnaryOperation):
        return _is_immutable_literal(node.expression)
    return False


def _build_empty_assign(var_target, leading_lines) -> cst.SimpleStatementLine:
    """Build `var = []` preserving leading whitespace."""
    return cst.SimpleStatementLine(
        body=[cst.Assign(
            targets=[cst.AssignTarget(target=var_target)],
            value=cst.List(elements=[]),
        )],
        leading_lines=leading_lines,
    )


def _build_for_append(var_target, loop_target, iter_expr, val) -> cst.For:
    """Build `for loop_target in iter_expr: var.append(val)`"""
    return cst.For(
        target=loop_target,
        iter=iter_expr,
        body=cst.IndentedBlock(body=[
            cst.SimpleStatementLine(body=[
                cst.Expr(value=cst.Call(
                    func=cst.Attribute(
                        value=var_target,
                        attr=cst.Name("append"),
                        dot=cst.Dot(),
                    ),
                    args=[cst.Arg(value=val)],
                ))
            ])
        ]),
    )
    
def _match_list_to_for_append(stmt):
    """
    Return (var_target, val, iter_expr, loop_target, leading_lines) if stmt matches:
    - `x = [val] * n`              → loop_target = Name("_"), iter_expr = range(n)
    - `x = [val for t in iter]`    → loop_target = t, iter_expr = iter
    else None.
    """
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    assign = stmt.body[0]
    if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
        return None
    target = assign.targets[0].target
    if not isinstance(target, cst.Name):
        return None
    
    if target.value == "_":
        return None
    
    value = assign.value

    if isinstance(value, cst.BinaryOperation) and isinstance(value.operator, cst.Multiply):
        if isinstance(value.left, cst.List) and len(value.left.elements) == 1:
            val = value.left.elements[0].value
            n_expr = value.right
        elif isinstance(value.right, cst.List) and len(value.right.elements) == 1:
            val = value.right.elements[0].value
            n_expr = value.left
        else:
            return None
        if not _is_immutable_literal(val):
            return None
        if target.value in _collect_names_in_node_list(n_expr):
            return None
        iter_expr = cst.Call(func=cst.Name("range"), args=[cst.Arg(value=n_expr)])
        return target, val, iter_expr, cst.Name("_"), stmt.leading_lines

    if isinstance(value, cst.ListComp):
        comp_for = value.for_in
        if comp_for.ifs or comp_for.inner_for_in is not None:
            return None
        if not _is_immutable_literal(value.elt):
            return None
        if target.value in _collect_names_in_node_list(comp_for.iter):
            return None
        if target.value in _get_target_names_list(comp_for.target):
            return None
        return target, value.elt, comp_for.iter, comp_for.target, stmt.leading_lines

    return None

class _ListToForAppendTransformer(cst.CSTTransformer):

    def _process_stmts(self, stmts):
        new_stmts = []
        changed = False
        for stmt in stmts:
            match = _match_list_to_for_append(stmt)
            if match is not None:
                var_target, val, iter_expr, loop_target, leading_lines = match
                new_stmts.append(_build_empty_assign(var_target, leading_lines))
                new_stmts.append(_build_for_append(var_target, loop_target, iter_expr, val))
                changed = True
            else:
                new_stmts.append(stmt)
        return new_stmts, changed

    def leave_IndentedBlock(self, original_node, updated_node):
        new_body, changed = self._process_stmts(updated_node.body)
        return updated_node.with_changes(body=new_body) if changed else updated_node

    def leave_Module(self, original_node, updated_node):
        new_body, changed = self._process_stmts(updated_node.body)
        return updated_node.with_changes(body=new_body) if changed else updated_node


def list_to_for_append(path: str, execute=False, content=None) -> tuple[bool, str]:
    """Transform `x = [val] * n` or `x = [val for t in iter]` into x = [] + for/append loop.
    """
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(_ListToForAppendTransformer()).code,
        execute=execute,
        content=content
    )



# ---------------------------------------------------------------------------
# split_tuple_assignment
# ---------------------------------------------------------------------------
def _is_safe_expr_split(node) -> bool:
    """Return True if the expression has no side effects (no function calls)."""
    if isinstance(node, cst.Call):
        return False
    if isinstance(node, (cst.Integer, cst.Float, cst.Imaginary, cst.SimpleString)):
        return True
    if isinstance(node, cst.Name):
        return True
    if isinstance(node, cst.BinaryOperation):
        return _is_safe_expr_split(node.left) and _is_safe_expr_split(node.right)
    if isinstance(node, cst.UnaryOperation):
        return _is_safe_expr_split(node.expression)
    if isinstance(node, cst.Attribute):
        return _is_safe_expr_split(node.value)
    if isinstance(node, cst.Subscript):
        return _is_safe_expr_split(node.value)
    if isinstance(node, (cst.Tuple, cst.List)):
        return all(_is_safe_expr_split(el.value) for el in node.elements)
    return False


def _get_tuple_assign_targets(target) -> list | None:
    """Return list of Name targets if target is a simple tuple of Names, else None."""
    if isinstance(target, cst.Tuple):
        names = []
        for el in target.elements:
            if not isinstance(el.value, cst.Name):
                return None
            names.append(el.value)
        return names
    return None


def _rhs_references_any(expr, names: set[str]) -> bool:
    """Return True if expr references any of the given names."""
    if isinstance(expr, cst.Name) and expr.value in names:
        return True
    for child in expr.children:
        if isinstance(child, cst.CSTNode) and _rhs_references_any(child, names):
            return True
    return False


class _SplitTupleAssignmentTransformer(cst.CSTTransformer):

    def _process_stmts(
        self, stmts: tuple[cst.BaseStatement, ...]
    ) -> tuple[list[cst.BaseStatement], bool]:
        new_stmts = []
        changed = False

        for stmt in stmts:
            if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
                new_stmts.append(stmt)
                continue

            assign = stmt.body[0]
            if not isinstance(assign, cst.Assign):
                new_stmts.append(stmt)
                continue

            if len(assign.targets) == 1:
                target = assign.targets[0].target
                names = _get_tuple_assign_targets(target)
                if names is None:
                    new_stmts.append(stmt)
                    continue

                value = assign.value
                if not isinstance(value, cst.Tuple):
                    new_stmts.append(stmt)
                    continue

                elements = [el.value for el in value.elements]
                if len(names) != len(elements):
                    new_stmts.append(stmt)
                    continue

                lhs_names = {n.value for n in names}
                if any(_rhs_references_any(el, lhs_names) for el in elements):
                    new_stmts.append(stmt)
                    continue

                changed = True
                for i, (name, val) in enumerate(zip(names, elements)):
                    new_stmts.append(stmt.with_changes(
                        body=[cst.Assign(
                            targets=[cst.AssignTarget(target=name)],
                            value=val,
                        )],
                        leading_lines=stmt.leading_lines if i == 0 else [],
                    ))

            elif len(assign.targets) >= 2:
                value = assign.value
                if not _is_safe_expr_split(value):
                    new_stmts.append(stmt)
                    continue

                changed = True
                for i, t in enumerate(assign.targets):
                    new_stmts.append(stmt.with_changes(
                        body=[cst.Assign(
                            targets=[cst.AssignTarget(target=t.target)],
                            value=value,
                        )],
                        leading_lines=stmt.leading_lines if i == 0 else [],
                    ))
            else:
                new_stmts.append(stmt)

        return new_stmts, changed

    def leave_IndentedBlock(
        self, original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock
    ) -> cst.IndentedBlock:
        new_body, changed = self._process_stmts(updated_node.body)
        return updated_node.with_changes(body=new_body) if changed else updated_node

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        new_body, changed = self._process_stmts(updated_node.body)
        return updated_node.with_changes(body=new_body) if changed else updated_node


def split_tuple_assignment(path: str, execute=False, content=None) -> tuple[bool, str]:
    """Split tuple assignments and multiple assignments into separate statements.
    """
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(_SplitTupleAssignmentTransformer()).code,
        execute=execute,
        content=content
    )
    
    
# ---------------------------------------------------------------------------
# split_chained_comparison
# ---------------------------------------------------------------------------

def _is_safe_operand(node) -> bool:
    """Return True if operand has no side effects (no function calls)."""
    if isinstance(node, cst.Call):
        return False
    if isinstance(node, (cst.Integer, cst.Float, cst.Imaginary, cst.SimpleString)):
        return True
    if isinstance(node, cst.Name):
        return True
    if isinstance(node, cst.BinaryOperation):
        return _is_safe_operand(node.left) and _is_safe_operand(node.right)
    if isinstance(node, cst.UnaryOperation):
        return _is_safe_operand(node.expression)
    if isinstance(node, cst.Attribute):
        return _is_safe_operand(node.value)
    if isinstance(node, cst.Subscript):
        return _is_safe_operand(node.value)
    return False


_SAFE_CHAINED_OPS = (cst.LessThan, cst.LessThanEqual,
                     cst.GreaterThan, cst.GreaterThanEqual, cst.Equal)


def _has_explicit_parens(node) -> bool:
    """Return True if node has explicit parentheses."""
    return bool(getattr(node, 'lpar', []))


class _SplitChainedComparisonTransformer(cst.CSTTransformer):

    def leave_Comparison(
        self, original_node: cst.Comparison, updated_node: cst.Comparison
    ) -> cst.BaseExpression:
        if len(updated_node.comparisons) < 2:
            return updated_node

        if _has_explicit_parens(updated_node.left):
            return updated_node

        for comp in updated_node.comparisons:
            if not isinstance(comp.operator, _SAFE_CHAINED_OPS):
                return updated_node
            if _has_explicit_parens(comp.comparator):
                return updated_node

        all_operands = [updated_node.left] + [c.comparator for c in updated_node.comparisons]
        if not all(_is_safe_operand(op) for op in all_operands):
            return updated_node

        comparisons = list(updated_node.comparisons)
        operands = [updated_node.left] + [c.comparator for c in comparisons]

        result = None
        for i, comp in enumerate(comparisons):
            single = cst.Comparison(
                left=operands[i],
                comparisons=[comp],
            )
            if result is None:
                result = single
            else:
                result = cst.BooleanOperation(
                    left=result,
                    operator=cst.And(
                        whitespace_before=cst.SimpleWhitespace(" "),
                        whitespace_after=cst.SimpleWhitespace(" "),
                    ),
                    right=single,
                )

        return result


def split_chained_comparison(path: str, execute=False, content=None) -> tuple[bool, str]:
    """Split chained comparisons into explicit `and` expressions.
    """
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(_SplitChainedComparisonTransformer()).code,
        execute=execute,
        content=content
    )
    
    
# ---------------------------------------------------------------------------
# in_place_to_pure_method
# ---------------------------------------------------------------------------

def _is_safe_assignable_target(node) -> bool:
    """Return True if node can be safely used both as call receiver and assignment target."""
    if isinstance(node, cst.Name):
        return True
    if isinstance(node, cst.Attribute):
        return _is_safe_assignable_target(node.value)
    if isinstance(node, cst.Subscript):
        if not _is_safe_assignable_target(node.value):
            return False
        for sl in node.slice:
            if isinstance(sl.slice, cst.Index):
                if not _is_safe_index_expr(sl.slice.value):
                    return False
        return True
    return False


def _is_safe_index_expr(node) -> bool:
    """Return True if index expression has no side effects."""
    if isinstance(node, cst.Call):
        return False
    if isinstance(node, (cst.Integer, cst.Float, cst.SimpleString, cst.Name)):
        return True
    if isinstance(node, cst.UnaryOperation):
        return _is_safe_index_expr(node.expression)
    if isinstance(node, cst.BinaryOperation):
        return _is_safe_index_expr(node.left) and _is_safe_index_expr(node.right)
    return False


def _match_in_place_call(stmt):
    """
    Return (method_name, receiver, call_args) if stmt is `receiver.method(...)` 
    with method in {sort, reverse, clear} and receiver is safely assignable, else None.
    """
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    expr_stmt = stmt.body[0]
    if not isinstance(expr_stmt, cst.Expr) or not isinstance(expr_stmt.value, cst.Call):
        return None
    call = expr_stmt.value
    func = call.func
    if not isinstance(func, cst.Attribute):
        return None
    method = func.attr.value
    if method not in ("sort", "reverse", "clear"):
        return None
    if not _is_safe_assignable_target(func.value):
        return None
    if method == "clear" and call.args:
        return None 
    if method == "reverse" and call.args:
        return None 
    return method, func.value, call.args


class _InPlaceToPureMethodTransformer(cst.CSTTransformer):

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine:
        match = _match_in_place_call(updated_node)
        if match is None:
            return updated_node

        method, receiver, call_args = match

        if method == "sort":
            new_value = cst.Call(
                func=cst.Name("sorted"),
                args=[cst.Arg(value=receiver, comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")))
                      if call_args else cst.Arg(value=receiver), *call_args],
            )
        elif method == "reverse":
            new_value = cst.Call(
                func=cst.Name("list"),
                args=[cst.Arg(value=cst.Call(
                    func=cst.Name("reversed"),
                    args=[cst.Arg(value=receiver)],
                ))],
            )
        elif method == "clear":
            new_value = cst.List(elements=[])

        return updated_node.with_changes(
            body=[cst.Assign(
                targets=[cst.AssignTarget(target=receiver)],
                value=new_value,
            )]
        )


def in_place_to_pure_method(path: str, execute=False, content=None) -> tuple[bool, str]:
    """Replace in-place mutating methods (sort, reverse, clear) with pure equivalents.
    """
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(_InPlaceToPureMethodTransformer()).code,
        execute=execute,
        content=content
    )
    
# ---------------------------------------------------------------------------
# range_len_to_enumerate
# ---------------------------------------------------------------------------
def _match_range_len(iter_expr, loop_var: str):
    """Return the iterable `a` if iter_expr is `range(len(a))`, else None."""
    if not isinstance(iter_expr, cst.Call):
        return None
    if not isinstance(iter_expr.func, cst.Name) or iter_expr.func.value != "range":
        return None
    if len(iter_expr.args) != 1:
        return None
    arg = iter_expr.args[0].value
    if not isinstance(arg, cst.Call):
        return None
    if not isinstance(arg.func, cst.Name) or arg.func.value != "len":
        return None
    if len(arg.args) != 1:
        return None
    return arg.args[0].value


def _collect_names_range(node) -> set:
    """Collect all Name references within a node."""
    names = set()
    if isinstance(node, cst.Name):
        names.add(node.value)
    for child in node.children:
        if isinstance(child, cst.CSTNode):
            names |= _collect_names_range(child)
    return names


def _source(node) -> str:
    return cst.Module([]).code_for_node(node)


def _flatten_assign_target(t, sources: set) -> None:
    if isinstance(t, (cst.Tuple, cst.List)):
        for el in t.elements:
            _flatten_assign_target(el.value, sources)
    elif isinstance(t, cst.StarredElement):
        _flatten_assign_target(t.value, sources)
    else:
        sources.add(_source(t))


def _get_assign_target_sources(body) -> set:
    sources: set = set()

    class _Collector(cst.CSTVisitor):
        def visit_Assign(self, node: cst.Assign) -> bool:
            for t in node.targets:
                _flatten_assign_target(t.target, sources)
            return True

        def visit_AugAssign(self, node: cst.AugAssign) -> bool:
            _flatten_assign_target(node.target, sources)
            return True

        def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
            _flatten_assign_target(node.target, sources)
            return True

    body.visit(_Collector())
    return sources


def _check_container_safety(a_node: cst.CSTNode, regions: list, loop_var: str):
    a_source = _source(a_node)

    all_assign_targets: set = set()
    for region in regions:
        all_assign_targets |= _get_assign_target_sources(region)

    container_unsafe = False
    has_write_target = False
    safe_value_ids = set()

    class _SafePatternFinder(cst.CSTVisitor):
        def visit_Subscript(self, node: cst.Subscript) -> bool:
            nonlocal has_write_target
            value_source = _source(node.value)
            if value_source != a_source:
                return True
            if len(node.slice) != 1:
                return True
            sl = node.slice[0].slice
            if not isinstance(sl, cst.Index):
                return True
            safe_value_ids.add(id(node.value))
            if isinstance(sl.value, cst.Name) and sl.value.value == loop_var:
                if _source(node) in all_assign_targets:
                    has_write_target = True
            return True

    for region in regions:
        region.visit(_SafePatternFinder())
        
    class _BareRefFinder(cst.CSTVisitor):
        def on_visit(self, node: cst.CSTNode) -> bool:
            nonlocal container_unsafe
            if isinstance(node, cst.BaseExpression) and id(node) not in safe_value_ids:
                try:
                    if _source(node) == a_source:
                        container_unsafe = True
                except Exception:
                    pass
            return True

    for region in regions:
        region.visit(_BareRefFinder())

    return container_unsafe, has_write_target


def _find_subscript_uses(body, loop_var: str, a_node: cst.CSTNode):
    """Find all Subscript nodes matching `a[loop_var]` within body. Return list of nodes."""
    a_source = _source(a_node)
    matches = []

    class _Finder(cst.CSTVisitor):
        def visit_Subscript(self, node: cst.Subscript) -> bool:
            value_source = _source(node.value)
            if value_source != a_source:
                return True
            if len(node.slice) != 1:
                return True
            sl = node.slice[0].slice
            if isinstance(sl, cst.Index) and isinstance(sl.value, cst.Name) and sl.value.value == loop_var:
                matches.append(node)
            return True

    body.visit(_Finder())
    return matches


def _pick_unused_name(base: str, used_names: set) -> str:
    """Return base, or base with numeric suffix if base is already used."""
    if base not in used_names:
        return base
    i = 2
    while f"{base}{i}" in used_names:
        i += 1
    return f"{base}{i}"


class _ReplaceSubscriptTransformer(cst.CSTTransformer):
    """Replace all occurrences of `a[loop_var]` with a Name(val_name)."""

    def __init__(self, a_source: str, loop_var: str, val_name: str):
        super().__init__()
        self.a_source = a_source
        self.loop_var = loop_var
        self.val_name = val_name

    def leave_Subscript(
        self, original_node: cst.Subscript, updated_node: cst.Subscript
    ) -> cst.BaseExpression:
        value_source = cst.Module([]).code_for_node(updated_node.value)
        if value_source != self.a_source:
            return updated_node
        if len(updated_node.slice) != 1:
            return updated_node
        sl = updated_node.slice[0].slice
        if isinstance(sl, cst.Index) and isinstance(sl.value, cst.Name) and sl.value.value == self.loop_var:
            return cst.Name(self.val_name)
        return updated_node


def _build_enumerate_replacement(
    a_node, loop_var: str, body_or_elt, scope_names: set, is_expr: bool,
    force_no_substitution: bool = False,
):
    subscript_uses = [] if force_no_substitution else _find_subscript_uses(body_or_elt, loop_var, a_node)

    if subscript_uses:
        val_name = _pick_unused_name("val", scope_names)
        a_source = _source(a_node)
        replacer = _ReplaceSubscriptTransformer(a_source, loop_var, val_name)
        new_body_or_elt = body_or_elt.visit(replacer)
        new_target = cst.Tuple(elements=[
            cst.Element(value=cst.Name(loop_var), comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))),
            cst.Element(value=cst.Name(val_name)),
        ])
    else:
        new_body_or_elt = body_or_elt
        new_target = cst.Tuple(elements=[
            cst.Element(value=cst.Name(loop_var), comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))),
            cst.Element(value=cst.Name("_")),
        ])

    new_iter = cst.Call(
        func=cst.Name("enumerate"),
        args=[cst.Arg(value=a_node)],
    )

    return new_target, new_iter, new_body_or_elt

class _RangeLenToEnumerateTransformer(cst.CSTTransformer):
 
    def __init__(self):
        super().__init__()
        self._scope_stack: list[set] = [set()]
 
    def _collect_scope_names(self, node) -> None:
        self._scope_stack[-1] |= _collect_names_range(node)
 
    def visit_Module(self, node: cst.Module) -> bool:
        self._collect_scope_names(node)
        return True
 
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._scope_stack.append(set())
        self._collect_scope_names(node)
        return True
 
    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        self._scope_stack.pop()
        return updated_node
 
    def leave_For(
        self, original_node: cst.For, updated_node: cst.For
    ) -> cst.For:
        if not isinstance(updated_node.target, cst.Name):
            return updated_node
        if not isinstance(updated_node.body, cst.IndentedBlock):
            return updated_node
 
        loop_var = updated_node.target.value
        a_node = _match_range_len(updated_node.iter, loop_var)
        if a_node is None:
            return updated_node
 
        container_unsafe, has_write_target = _check_container_safety(
            a_node, [updated_node.body], loop_var
        )
        if container_unsafe:
            return updated_node
 
        scope_names = self._scope_stack[-1]
        new_target, new_iter, new_body = _build_enumerate_replacement(
            a_node, loop_var, updated_node.body, scope_names, is_expr=False,
            force_no_substitution=has_write_target,
        )
 
        return updated_node.with_changes(target=new_target, iter=new_iter, body=new_body)
 
    def _handle_comp_for(self, updated_node):
        """Apply the transformation to a comprehension/generator's for_in clause."""
        comp_for = updated_node.for_in
        if not isinstance(comp_for.target, cst.Name):
            return updated_node
        if comp_for.inner_for_in is not None:
            return updated_node
 
        loop_var = comp_for.target.value
        a_node = _match_range_len(comp_for.iter, loop_var)
        if a_node is None:
            return updated_node

        regions = [updated_node.elt] + [cond.test for cond in comp_for.ifs]
        container_unsafe, has_write_target = _check_container_safety(
            a_node, regions, loop_var
        )
        if container_unsafe:
            return updated_node
 
        scope_names = self._scope_stack[-1]
        new_target, new_iter, new_elt = _build_enumerate_replacement(
            a_node, loop_var, updated_node.elt, scope_names, is_expr=True,
            force_no_substitution=has_write_target,
        )
 
        new_for_in = comp_for.with_changes(target=new_target, iter=new_iter)
        return updated_node.with_changes(elt=new_elt, for_in=new_for_in)
 
    def leave_GeneratorExp(
        self, original_node: cst.GeneratorExp, updated_node: cst.GeneratorExp
    ) -> cst.GeneratorExp:
        return self._handle_comp_for(updated_node)
 
    def leave_ListComp(
        self, original_node: cst.ListComp, updated_node: cst.ListComp
    ) -> cst.ListComp:
        return self._handle_comp_for(updated_node)
 
    def leave_SetComp(
        self, original_node: cst.SetComp, updated_node: cst.SetComp
    ) -> cst.SetComp:
        return self._handle_comp_for(updated_node)


def range_len_to_enumerate(path: str, execute=False, content=None) -> tuple[bool, str]:
    """Replace `for i in range(len(a))` with `for i, val in enumerate(a)` in for-statements
    and comprehensions/generators, substituting a[i] uses with the value variable when present.
    """
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(_RangeLenToEnumerateTransformer()).code,
        execute=execute,
        content=content
    )
    
    
# ---------------------------------------------------------------------------
# method_call_to_type_call
# ---------------------------------------------------------------------------

_KNOWN_TYPE_NAMES = {"str", "list", "dict", "set", "tuple", "int", "float", "bool"}
 
 
def _collect_all_used_names(module: cst.Module) -> set:
    names = set()
 
    class _Collector(cst.CSTVisitor):
        def visit_Name(self, node: cst.Name) -> bool:
            names.add(node.value)
            return True
 
    module.visit(_Collector())
    return names
 
 
def _pick_unused_name(base: str, used_names: set) -> str:
    if base not in used_names:
        return base
    i = 1
    while f"{base}{i}" in used_names:
        i += 1
    return f"{base}{i}"
 
 
def _definition_name_node(assignment_node):
    if isinstance(assignment_node, cst.Name):
        return assignment_node
    if isinstance(assignment_node, cst.Param):
        return assignment_node.name
    return None
 
 
class _RenameByIdentityTransformer(cst.CSTTransformer):
 
    def __init__(self, rename_map: dict):
        super().__init__()
        self.rename_map = rename_map
 
    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        new_name = self.rename_map.get(id(original_node))
        if new_name is not None:
            return updated_node.with_changes(value=new_name)
        return updated_node
 
 
def deshadow_known_type_names(module: cst.Module) -> cst.Module:
    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    scopes = wrapper.resolve(cst_meta.ScopeProvider)
 
    all_used_names = _collect_all_used_names(module)
    rename_map = {}
 
    seen_scope_ids = set()
    for scope in scopes.values():
        if id(scope) in seen_scope_ids:
            continue
        seen_scope_ids.add(id(scope))
 
        if not isinstance(scope, (cst_meta.FunctionScope, cst_meta.GlobalScope)):
            continue
 
        for assignment in scope.assignments:
            if not isinstance(assignment, cst_meta.Assignment):
                continue
            if assignment.name not in _KNOWN_TYPE_NAMES:
                continue
 
            def_node = _definition_name_node(assignment.node)
            if def_node is None:
                continue
 
            new_name = _pick_unused_name(assignment.name, all_used_names)
            all_used_names.add(new_name)
 
            rename_map[id(def_node)] = new_name
            for ref in assignment.references:
                rename_map[id(ref.node)] = new_name
 
    if not rename_map:
        return module
 
    return module.visit(_RenameByIdentityTransformer(rename_map))


def _infer_literal_type(node) -> str | None:
    """Return the type name if node is a literal of known type, else None."""
    if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
        return "str"
    if isinstance(node, cst.List):
        return "list"
    if isinstance(node, cst.Dict):
        return "dict"
    if isinstance(node, cst.Set):
        return "set"
    if isinstance(node, cst.Tuple):
        return "tuple"
    if isinstance(node, cst.Integer):
        return "int"
    if isinstance(node, cst.Float):
        return "float"
    if isinstance(node, cst.Name) and node.value in ("True", "False"):
        return "bool"
    return None


def _infer_call_type(node) -> str | None:
    """Return the type name if node is `input()` or a known type constructor call."""
    if not isinstance(node, cst.Call):
        return None
    if not isinstance(node.func, cst.Name):
        return None
    if node.func.value == "input":
        return "str"
    if node.func.value in _KNOWN_TYPE_NAMES:
        return node.func.value
    return None


class _MethodCallToTypeCallTransformer(cst.CSTTransformer):

    def __init__(self):
        super().__init__()
        self._scope_stack: list[dict[str, str]] = [{}]

    @property
    def _scope(self) -> dict[str, str]:
        return self._scope_stack[-1]

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        self._scope_stack.append({})
        return True

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        self._scope_stack.pop()
        return updated_node

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self._scope_stack.append({})
        return True

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        self._scope_stack.pop()
        return updated_node

    def visit_Assign(self, node: cst.Assign) -> bool:
        if len(node.targets) == 1:
            target = node.targets[0].target
            if isinstance(target, cst.Name):
                t = _infer_literal_type(node.value) or _infer_call_type(node.value)
                if t:
                    self._scope[target.value] = t
                else:
                    self._scope.pop(target.value, None)
        return True

    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        func = updated_node.func
        if not isinstance(func, cst.Attribute):
            return updated_node

        receiver = func.value
        method_name = func.attr.value

        type_name = None
        if isinstance(receiver, cst.Name):
            type_name = self._scope.get(receiver.value)
        else:
            type_name = _infer_literal_type(receiver) or _infer_call_type(receiver)

        if type_name is None or type_name not in _KNOWN_TYPE_NAMES:
            return updated_node

        fixed_args = list(updated_node.args)
        if fixed_args:
            for i, arg in enumerate(fixed_args):
                if isinstance(arg.value, cst.GeneratorExp) and not arg.value.lpar:
                    fixed_args[i] = arg.with_changes(
                        value=arg.value.with_changes(
                            lpar=[cst.LeftParen()],
                            rpar=[cst.RightParen()],
                        )
                    )

        new_first_arg = cst.Arg(
            value=receiver,
            comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
            if fixed_args else cst.MaybeSentinel.DEFAULT,
        )
        new_args = [new_first_arg, *fixed_args]

        new_func = cst.Attribute(
            value=cst.Name(type_name),
            attr=cst.Name(method_name),
            dot=cst.Dot(),
        )

        return updated_node.with_changes(func=new_func, args=new_args)


def method_call_to_type_call(path: str, execute=False, content=None) -> tuple[bool, str]:
    def _run(src: str) -> str:
        module = cst.parse_module(src)
        module = deshadow_known_type_names(module)
        module = module.visit(_MethodCallToTypeCallTransformer())
        return module.code

    return apply_to_file(
        Path(path),
        _run,
        execute=execute,
        content=content
    )