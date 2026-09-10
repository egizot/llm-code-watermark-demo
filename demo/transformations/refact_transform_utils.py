import libcst as cst
import libcst.metadata as cst_meta
import ast

from pathlib import Path
from collections import Counter


# ---------------------------------------------------------------------------
# File/folder handling
# ---------------------------------------------------------------------------

def apply_to_file(file_path: Path, transform_fn, execute=False, content=None) -> tuple[bool, str]:
    source = content if content is not None else file_path.read_text(encoding="utf-8")
    
    try:
        result = transform_fn(source)
    except Exception as e:
        print(f"Warning: Exception during transformation in {file_path.name}: {e}")
        return False, source

    changed = result != source

    if changed:
        try:
            ast.parse(result)
        except SyntaxError as e:
            print(f"Warning: The transformation on {file_path.name} would generate invalid code ({e}). Changes discarded.")
            return False, source

    if execute and changed:
        file_path.write_text(result, encoding="utf-8")
        
    return changed, result

# ---------------------------------------------------------------------------
# remove_unnecessary_else
# ---------------------------------------------------------------------------
_GUARD_TYPES = (cst.Return, cst.Raise, cst.Continue, cst.Break)

def ends_with_guard(if_node: cst.If) -> bool:
    """Return True if the last statement in the if-body is a guard statement."""
    body = if_node.body

    if isinstance(body, cst.IndentedBlock) and body.body:
        last_stmt = body.body[-1]
        if isinstance(last_stmt, cst.SimpleStatementLine) and last_stmt.body:
            return isinstance(last_stmt.body[-1], _GUARD_TYPES)

    elif isinstance(body, cst.SimpleStatementSuite) and body.body:
        return isinstance(body.body[-1], _GUARD_TYPES)
        
    return False

def has_unnecessary_else(if_node: cst.If) -> bool:
    return isinstance(if_node.orelse, cst.Else) and ends_with_guard(if_node)

# ---------------------------------------------------------------------------
# hoist_statement_from_loop
# ---------------------------------------------------------------------------
def _extract_all_names(target: cst.CSTNode) -> set[str]:
    names = set()
    if isinstance(target, cst.Name):
        names.add(target.value)
    elif isinstance(target, (cst.Tuple, cst.List)):
        for el in target.elements:
            names |= _extract_all_names(el.value)
    elif isinstance(target, cst.StarredElement):
        names |= _extract_all_names(target.value)
    return names


def _is_immutable_literal(node: cst.CSTNode) -> bool:
    if isinstance(node, (cst.Integer, cst.Float, cst.Imaginary,
                         cst.SimpleString, cst.ConcatenatedString)):
        return True
    if isinstance(node, cst.Name) and node.value in ("True", "False", "None"):
        return True
    if isinstance(node, cst.Tuple):
        return all(_is_immutable_literal(el.value) for el in node.elements)
    if isinstance(node, cst.UnaryOperation):
        return _is_immutable_literal(node.expression)
    
    return False


def _is_hoistable(stmt: cst.CSTNode, loop_var_names: set[str]) -> bool:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return False
    small = stmt.body[0]
    if not isinstance(small, cst.Assign) or len(small.targets) != 1:
        return False
    target = small.targets[0].target
    if not isinstance(target, cst.Name):
        return False
    if target.value in loop_var_names:
        return False
        
    return _is_immutable_literal(small.value)


class DeepAssignmentCounter(cst.CSTVisitor):
    def __init__(self):
        self.counts = Counter()

    def visit_AssignTarget(self, node: cst.AssignTarget) -> None:
        for name in _extract_all_names(node.target):
            self.counts[name] += 1

    def visit_AugAssign(self, node: cst.AugAssign) -> None:
        for name in _extract_all_names(node.target):
            self.counts[name] += 1

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        for name in _extract_all_names(node.target):
            self.counts[name] += 1

    def visit_For(self, node: cst.For) -> None:
        for name in _extract_all_names(node.target):
            self.counts[name] += 1

    def visit_WithItem(self, node: cst.WithItem) -> None:
        if node.asname is not None:
            for name in _extract_all_names(node.asname.name):
                self.counts[name] += 1

# ---------------------------------------------------------------------------
# use_dict_items
# ---------------------------------------------------------------------------
class _AssignTargetCollectorUseDict(cst.CSTVisitor):
    def __init__(self):
        self.assigned_names = set()

    def visit_Name(self, node: cst.Name) -> None:
        pass

    def visit_AssignTarget(self, node: cst.AssignTarget) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def visit_AugAssign(self, node: cst.AugAssign) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def visit_For(self, node: cst.For) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def _extract_names(self, node: cst.CSTNode) -> set[str]:
        names = set()
        if isinstance(node, cst.Name):
            names.add(node.value)
        elif isinstance(node, (cst.Tuple, cst.List)):
            for el in node.elements:
                names |= self._extract_names(el.value)
        elif isinstance(node, cst.StarredElement):
            names |= self._extract_names(node.value)
        return names


def _is_reassigned_in_body_dict(body_stmts: list[cst.BaseStatement], name: str) -> bool:
    collector = _AssignTargetCollectorUseDict()
    
    for stmt in body_stmts:
        stmt.visit(collector)
        
    return name in collector.assigned_names

# ---------------------------------------------------------------------------
# list_comprehension
# ---------------------------------------------------------------------------
class _AssignTargetCollectorList(cst.CSTVisitor):
    def __init__(self):
        self.assigned_names = set()

    def visit_AssignTarget(self, node: cst.AssignTarget) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def visit_AugAssign(self, node: cst.AugAssign) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def visit_For(self, node: cst.For) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def _extract_names(self, node: cst.CSTNode) -> set[str]:
        names = set()
        if isinstance(node, cst.Name):
            names.add(node.value)
        elif isinstance(node, (cst.Tuple, cst.List)):
            for el in node.elements:
                names |= self._extract_names(el.value)
        elif isinstance(node, cst.StarredElement):
            names |= self._extract_names(node.value)
        return names


def _is_reassigned_in_body_list(body_stmts: list[cst.BaseStatement], name: str) -> bool:
    collector = _AssignTargetCollectorList()
    for stmt in body_stmts:
        stmt.visit(collector)
    return name in collector.assigned_names


def _match_list_init_list(stmt: cst.BaseStatement) -> str | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
        
    assign = stmt.body[0]
    if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
        return None
        
    target = assign.targets[0].target
    if not isinstance(target, cst.Name):
        return None
        
    if not isinstance(assign.value, cst.List) or assign.value.elements:
        return None
        
    return target.value


def _match_append_call_list(stmt: cst.BaseStatement, list_name: str) -> cst.CSTNode | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
        
    expr_stmt = stmt.body[0]
    if not isinstance(expr_stmt, cst.Expr):
        return None
        
    call = expr_stmt.value
    if not isinstance(call, cst.Call):
        return None
        
    func = call.func
    if not isinstance(func, cst.Attribute):
        return None
        
    if not isinstance(func.value, cst.Name) or func.value.value != list_name:
        return None
        
    if func.attr.value != "append" or len(call.args) != 1:
        return None
        
    return call.args[0].value


def _match_append_in_body(body_stmts: list[cst.BaseStatement], list_name: str):
    if len(body_stmts) != 1:
        return None

    stmt = body_stmts[0]

    expr = _match_append_call_list(stmt, list_name)
    if expr is not None:
        return expr, None

    if (
        isinstance(stmt, cst.If)
        and stmt.orelse is None
        and isinstance(stmt.body, cst.IndentedBlock)
        and len(stmt.body.body) == 1
    ):
        expr = _match_append_call_list(stmt.body.body[0], list_name)
        if expr is not None:
            return expr, stmt.test

    return None

# ---------------------------------------------------------------------------
# merge_list_append
# ---------------------------------------------------------------------------
class _NameReferenceCollectorMerge(cst.CSTVisitor):
    def __init__(self, target_name: str):
        self.target_name = target_name
        self.references_found = False

    def visit_Name(self, node: cst.Name) -> None:
        if node.value == self.target_name:
            self.references_found = True

def _expr_references_name_merge(expr: cst.CSTNode, name: str) -> bool:
    collector = _NameReferenceCollectorMerge(name)
    expr.visit(collector)
    return collector.references_found

def _match_list_init_merge(stmt: cst.BaseStatement) -> str | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    assign = stmt.body[0]
    if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
        return None
    target = assign.targets[0].target
    if not isinstance(target, cst.Name):
        return None
    if not isinstance(assign.value, cst.List) or len(assign.value.elements) > 0:
        return None
        
    return target.value

def _match_append_call_merge(stmt: cst.BaseStatement, list_name: str) -> cst.BaseExpression | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    expr_stmt = stmt.body[0]
    if not isinstance(expr_stmt, cst.Expr):
        return None
    call = expr_stmt.value
    if not isinstance(call, cst.Call):
        return None
    func = call.func
    if not isinstance(func, cst.Attribute):
        return None
    if not isinstance(func.value, cst.Name) or func.value.value != list_name:
        return None
    if func.attr.value != "append":
        return None
    if len(call.args) != 1:
        return None
        
    return call.args[0].value


# ---------------------------------------------------------------------------
# remove_unnecessary_cast
# ---------------------------------------------------------------------------
def _unwrap_remove(node: cst.CSTNode) -> cst.CSTNode:
    if hasattr(node, "lpar") and node.lpar:
        return node.with_changes(lpar=[], rpar=[])
    return node


def _is_bool_expr(node: cst.CSTNode) -> bool:
    node = _unwrap_remove(node)
    if isinstance(node, cst.Comparison):
        return True
    if isinstance(node, cst.UnaryOperation) and isinstance(node.operator, cst.Not):
        return True
    if isinstance(node, cst.Call):
        func = _unwrap_remove(node.func)
        if isinstance(func, cst.Name) and func.value in ("isinstance", "issubclass"):
            return True
    return False


def _get_literal_type(node: cst.CSTNode) -> str | None:
    node = _unwrap_remove(node)
    if isinstance(node, cst.Integer):
        return "int"
    if isinstance(node, cst.Float):
        return "float"
    if isinstance(node, (cst.SimpleString, cst.ConcatenatedString)):
        return "str"
    if isinstance(node, cst.Name) and node.value in ("True", "False"):
        return "bool"
    if _is_bool_expr(node):
        return "bool"
    return None


# ---------------------------------------------------------------------------
# merge-comparisons
# ---------------------------------------------------------------------------
def _unwrap_merge(node: cst.CSTNode) -> cst.CSTNode:
    if hasattr(node, "lpar") and node.lpar:
        return node.with_changes(lpar=[], rpar=[])
    return node


def _flatten_or(node) -> list:
    unwrapped = _unwrap_merge(node)
    if isinstance(unwrapped, cst.BooleanOperation) and isinstance(unwrapped.operator, cst.Or):
        return _flatten_or(unwrapped.left) + _flatten_or(unwrapped.right)
    return [unwrapped]


def _match_eq_comparison(node):
    node = _unwrap_merge(node)
    if not isinstance(node, cst.Comparison) or len(node.comparisons) != 1:
        return None
    comp = node.comparisons[0]
    if not isinstance(comp.operator, cst.Equal):
        return None
    return _unwrap_merge(node.left), _unwrap_merge(comp.comparator)


def _contains_call(node: cst.CSTNode) -> bool:
    if isinstance(node, cst.Call):
        return True
    for child in node.children:
        if _contains_call(child):
            return True
    return False


def _is_safe_lhs(node) -> bool:
    return not _contains_call(node)

def _is_literal_merge(node) -> bool:
    if isinstance(node, (cst.Integer, cst.Float, cst.Imaginary,
                          cst.SimpleString, cst.ConcatenatedString)):
        return True
    if isinstance(node, cst.Name) and node.value in ("True", "False", "None"):
        return True
    if isinstance(node, (cst.Tuple, cst.List, cst.Set)):
        return all(_is_literal_merge(el.value) for el in node.elements)
    if isinstance(node, cst.Dict):
        return all(
            isinstance(el, cst.DictElement)
            and _is_literal_merge(el.key) and _is_literal_merge(el.value)
            for el in node.elements
        )
    if isinstance(node, cst.UnaryOperation):
        return _is_literal_merge(node.expression)
    return False

# ---------------------------------------------------------------------------
# sum_comprehension
# ---------------------------------------------------------------------------
def _collect_all_used_names_sum(module: cst.Module) -> set:
    names = set()
 
    class _Collector(cst.CSTVisitor):
        def visit_Name(self, node: cst.Name) -> bool:
            names.add(node.value)
            return True
 
    module.visit(_Collector())
    return names
 
 
def _pick_unused_name_sum(base: str, used_names: set) -> str:
    if base not in used_names:
        return base
    i = 1
    while f"{base}{i}" in used_names:
        i += 1
    return f"{base}{i}"
 
 
class _RenameByIdentityTransformerSum(cst.CSTTransformer):
 
    def __init__(self, rename_map: dict):
        super().__init__()
        self.rename_map = rename_map
 
    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        new_name = self.rename_map.get(id(original_node))
        if new_name is not None:
            return updated_node.with_changes(value=new_name)
        return updated_node
 
 
def deshadow_function_name(module: cst.Module, name: str) -> cst.Module:
    wrapper = cst.MetadataWrapper(module, unsafe_skip_copy=True)
    scopes = wrapper.resolve(cst_meta.ScopeProvider)
 
    all_used_names = _collect_all_used_names_sum(module)
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
            if assignment.name != name:
                continue
            if not isinstance(assignment.node, (cst.FunctionDef, cst.ClassDef)):
                continue 
 
            def_node = assignment.node.name
            new_name = _pick_unused_name_sum(name, all_used_names)
            all_used_names.add(new_name)
 
            rename_map[id(def_node)] = new_name
            for ref in assignment.references:
                rename_map[id(ref.node)] = new_name
 
    if not rename_map:
        return module
 
    return module.visit(_RenameByIdentityTransformerSum(rename_map))
 
class _AssignTargetCollectorSum(cst.CSTVisitor):
    def __init__(self, include_aug: bool = True):
        self.assigned_names = set()
        self.include_aug = include_aug

    def visit_AssignTarget(self, node: cst.AssignTarget) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def visit_AugAssign(self, node: cst.AugAssign) -> None:
        if self.include_aug:
            self.assigned_names |= self._extract_names(node.target)

    def visit_For(self, node: cst.For) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def visit_NamedExpr(self, node: cst.NamedExpr) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def _extract_names(self, node: cst.CSTNode) -> set[str]:
        names = set()
        if isinstance(node, cst.Name):
            names.add(node.value)
        elif isinstance(node, (cst.Tuple, cst.List)):
            for el in node.elements:
                names |= self._extract_names(el.value)
        elif isinstance(node, cst.StarredElement):
            names |= self._extract_names(node.value)
        return names

class _NameReferenceCollectorSum(cst.CSTVisitor):
    def __init__(self):
        self.referenced_names = set()

    def visit_Name(self, node: cst.Name) -> None:
        self.referenced_names.add(node.value)


def _is_reassigned_in_body_sum(body_stmts: list[cst.BaseStatement], name: str, include_aug: bool = True) -> bool:
    collector = _AssignTargetCollectorSum(include_aug=include_aug)
    for stmt in body_stmts:
        stmt.visit(collector)
    return name in collector.assigned_names

def _expr_references_name_sum(node: cst.CSTNode, name: str) -> bool:
    collector = _NameReferenceCollectorSum()
    node.visit(collector)
    return name in collector.referenced_names


def _match_zero_init(stmt: cst.BaseStatement) -> str | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    assign = stmt.body[0]
    if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
        return None
        
    target = assign.targets[0].target
    if not isinstance(target, cst.Name):
        return None
        
    val = assign.value
    if isinstance(val, cst.Integer) and val.value == "0":
        return target.value
    if isinstance(val, cst.Float) and val.value in ("0.0", "0."):
        return target.value
        
    return None

def _match_aug_add(stmt: cst.BaseStatement, var_name: str) -> cst.CSTNode | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    aug = stmt.body[0]
    if not isinstance(aug, cst.AugAssign):
        return None
    if not isinstance(aug.target, cst.Name) or aug.target.value != var_name:
        return None
    if not isinstance(aug.operator, cst.AddAssign):
        return None
    return aug.value

def _match_loop_body_for_sum(body_stmts: list[cst.BaseStatement], var_name: str):
    if len(body_stmts) != 1:
        return None

    stmt = body_stmts[0]

    aug_expr = _match_aug_add(stmt, var_name)
    if aug_expr is not None:
        return aug_expr, None

    if (
        isinstance(stmt, cst.If)
        and stmt.orelse is None
        and isinstance(stmt.body, cst.IndentedBlock)
        and len(stmt.body.body) == 1
    ):
        aug_expr = _match_aug_add(stmt.body.body[0], var_name)
        if aug_expr is not None:
            return aug_expr, stmt.test

    return None


# ---------------------------------------------------------------------------
# merge_nested_ifs
# ---------------------------------------------------------------------------
def _needs_parens(node: cst.BaseExpression) -> bool:
    if isinstance(node, cst.BooleanOperation) and isinstance(node.operator, cst.Or):
        return True
        
    return isinstance(node, (cst.IfExp, cst.Lambda, cst.NamedExpr))


def _maybe_parenthesize(node: cst.BaseExpression) -> cst.BaseExpression:
    if _needs_parens(node) and not node.lpar:
        return node.with_changes(
            lpar=[cst.LeftParen()],
            rpar=[cst.RightParen()],
        )
    return node

# ---------------------------------------------------------------------------
# inline_immediately_returned_variable
# ---------------------------------------------------------------------------
def _match_simple_assign_inline(stmt) -> str | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    assign = stmt.body[0]
    if not isinstance(assign, cst.Assign):
        return None
    if len(assign.targets) != 1:
        return None
    target = assign.targets[0].target
    if not isinstance(target, cst.Name):
        return None
    return target.value

def _match_return(stmt, var_name: str) -> bool:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return False
    ret = stmt.body[0]
    if not isinstance(ret, cst.Return):
        return False
    if not isinstance(ret.value, cst.Name):
        return False
    return ret.value.value == var_name

def _is_declared_global_or_nonlocal(stmts: tuple[cst.BaseStatement, ...], var_name: str) -> bool:
    for stmt in stmts:
        if isinstance(stmt, cst.SimpleStatementLine):
            for small_stmt in stmt.body:
                if isinstance(small_stmt, (cst.Global, cst.Nonlocal)):
                    for name_item in small_stmt.names:
                        if name_item.name.value == var_name:
                            return True
    return False

# ---------------------------------------------------------------------------
# for_index_underscore
# ---------------------------------------------------------------------------
class _NameCollectorForIndex(cst.CSTVisitor):
    def __init__(self):
        self.names = set()
        
    def visit_Name(self, node: cst.Name) -> None:
        self.names.add(node.value)

def _collect_names_in_node_for_index(node: cst.CSTNode) -> set[str]:
    collector = _NameCollectorForIndex()
    node.visit(collector)
    return collector.names

def _get_target_names(target: cst.CSTNode) -> set[str]:
    names = set()
    if isinstance(target, cst.Name):
        names.add(target.value)
    elif isinstance(target, (cst.Tuple, cst.List)):
        for el in target.elements:
            names |= _get_target_names(el.value)
    elif isinstance(target, cst.StarredElement):
        names |= _get_target_names(target.value)
    return names

def _replace_unused_in_target(target: cst.CSTNode, used_names: set[str]):
    if isinstance(target, cst.Name):
        if target.value == "_":
            return target
        return target if target.value in used_names else cst.Name("_")
    elif isinstance(target, (cst.Tuple, cst.List)):
        new_elements = []
        for el in target.elements:
            new_val = _replace_unused_in_target(el.value, used_names)
            new_elements.append(el.with_changes(value=new_val))
        return target.with_changes(elements=new_elements)
    elif isinstance(target, cst.StarredElement):
        new_val = _replace_unused_in_target(target.value, used_names)
        return target.with_changes(value=new_val)
    return target

def _process_comp_for(comp_for: cst.CompFor, downstream_usages: set[str]) -> cst.CompFor:
    used_here = set(downstream_usages)
    
    for comp_if in comp_for.ifs:
        used_here |= _collect_names_in_node_for_index(comp_if.test)

    if comp_for.inner_for_in is not None:
        if isinstance(comp_for.inner_for_in, cst.CompFor):
            used_here |= _collect_names_in_node_for_index(comp_for.inner_for_in.iter)
        
        new_inner = _process_comp_for(comp_for.inner_for_in, used_here)
        comp_for = comp_for.with_changes(inner_for_in=new_inner)

        used_here |= _collect_names_in_node_for_index(new_inner)
        
    target_names = _get_target_names(comp_for.target)
    unused = target_names - used_here

    if unused and "_" not in used_here:
        new_target = _replace_unused_in_target(comp_for.target, used_here)
        comp_for = comp_for.with_changes(target=new_target)
        
    return comp_for

def _handle_comp(updated_node: cst.CSTNode) -> cst.CSTNode:
    base_usages = set()

    if hasattr(updated_node, "elt"):
        base_usages |= _collect_names_in_node_for_index(updated_node.elt)

    if hasattr(updated_node, "key"):
        base_usages |= _collect_names_in_node_for_index(updated_node.key)
        base_usages |= _collect_names_in_node_for_index(updated_node.value)

    new_for_in = _process_comp_for(updated_node.for_in, base_usages)
    return updated_node.with_changes(for_in=new_for_in)

# ---------------------------------------------------------------------------
# simplify_constant_sum
# ---------------------------------------------------------------------------

def _unwrap_simplify(node: cst.CSTNode) -> cst.CSTNode:
    if hasattr(node, "lpar") and node.lpar:
        return node.with_changes(lpar=[], rpar=[])
    return node


def _combine_conditions(ifs: list) -> cst.BaseExpression:
    conditions = [_unwrap_simplify(c.test) for c in ifs]

    result = conditions[0]
    for cond in conditions[1:]:
        current_left = result
        current_right = cond

        if isinstance(current_left, cst.BooleanOperation) and not current_left.lpar:
            current_left = current_left.with_changes(
                lpar=[cst.LeftParen()], rpar=[cst.RightParen()]
            )

        if isinstance(current_right, cst.BooleanOperation) and not current_right.lpar:
            current_right = current_right.with_changes(
                lpar=[cst.LeftParen()], rpar=[cst.RightParen()]
            )

        result = cst.BooleanOperation(
            left=current_left,
            operator=cst.And(
                whitespace_before=cst.SimpleWhitespace(" "),
                whitespace_after=cst.SimpleWhitespace(" "),
            ),
            right=current_right,
        )

    return result

# ---------------------------------------------------------------------------
# simplify_len_comparison
# ---------------------------------------------------------------------------

def _match_len_call(node) -> cst.BaseExpression | None:
    if not isinstance(node, cst.Call):
        return None
    if not isinstance(node.func, cst.Name) or node.func.value != "len":
        return None
    if len(node.args) != 1 or node.args[0].keyword is not None:
        return None
    return node.args[0].value


def _match_int_zero_or_one(node) -> int | None:
    if not isinstance(node, cst.Integer):
        return None
    if node.value == "0":
        return 0
    if node.value == "1":
        return 1
    return None

def _make_not(expr: cst.BaseExpression) -> cst.UnaryOperation:

    if isinstance(expr, (cst.BooleanOperation, cst.IfExp, cst.Comparison)):
        expr = expr.with_changes(
            lpar=(cst.LeftParen(),),   
            rpar=(cst.RightParen(),),
        )
    return cst.UnaryOperation(
        operator=cst.Not(whitespace_after=cst.SimpleWhitespace(" ")),
        expression=expr,
    )

def _simplify_len_comparison(len_arg, op, literal_val: int) -> cst.BaseExpression | None:

    if (
        (isinstance(op, cst.GreaterThan) and literal_val == 0)
        or (isinstance(op, cst.GreaterThanEqual) and literal_val == 1)
        or (isinstance(op, cst.NotEqual) and literal_val == 0)
    ):
        return len_arg
    
    if (
        (isinstance(op, cst.Equal) and literal_val == 0)
        or (isinstance(op, cst.LessThan) and literal_val == 1)
        or (isinstance(op, cst.LessThanEqual) and literal_val == 0)
    ):
        return _make_not(len_arg)

    return None


def _simplify_len_comparison_reversed(literal_val: int, op, len_arg) -> cst.BaseExpression | None:

    if (
        (isinstance(op, cst.LessThan) and literal_val == 0)
        or (isinstance(op, cst.LessThanEqual) and literal_val == 1)
        or (isinstance(op, cst.NotEqual) and literal_val == 0)
    ):
        return len_arg
    
    if (
        (isinstance(op, cst.Equal) and literal_val == 0)
        or (isinstance(op, cst.GreaterThan) and literal_val == 1)
        or (isinstance(op, cst.GreaterThanEqual) and literal_val == 0)
    ):
        return _make_not(len_arg)

    return None

def _is_safe_boolean_context(stack: list[cst.CSTNode]) -> bool:
    for node in reversed(stack[:-1]):
        if isinstance(node, (cst.Assign, cst.AnnAssign, cst.AugAssign, cst.Return, cst.Expr,
                             cst.IndentedBlock, cst.SimpleStatementSuite, cst.SimpleStatementLine)):
            return False
        
        if isinstance(node, (cst.If, cst.While, cst.Assert, cst.IfExp, cst.CompIf)):
            return True
            
        if isinstance(node, cst.Call):
            if isinstance(node.func, cst.Name) and node.func.value == "bool":
                return True
                
    return False

# ---------------------------------------------------------------------------
# comprehension_to_generator
# ---------------------------------------------------------------------------
_GENERATOR_ACCEPTING_FUNCS = frozenset({
    "all", "any", "enumerate", "frozenset", "list",
    "max", "min", "set", "sum", "tuple"
})

def _listcomp_to_genexp(listcomp: cst.ListComp) -> cst.GeneratorExp:
    return cst.GeneratorExp(
        elt=listcomp.elt,
        for_in=listcomp.for_in,
        lpar=[],
        rpar=[],
    )

def _is_async_comp(comp_for: cst.CompFor) -> bool:
    current = comp_for
    while current is not None:
        if current.asynchronous is not None:
            return True
        current = current.inner_for_in
    return False

class _ContainsNamedExprVisitor(cst.CSTVisitor):
    def __init__(self):
        self.found = False

    def visit_NamedExpr(self, node: cst.NamedExpr) -> None:
        self.found = True

def _has_named_expr(node: cst.CSTNode) -> bool:
    visitor = _ContainsNamedExprVisitor()
    node.visit(visitor)
    return visitor.found
    
    
# ---------------------------------------------------------------------------
# use_any
# ---------------------------------------------------------------------------
class _AssignTargetCollectorUseAny(cst.CSTVisitor):
    def __init__(self, include_aug: bool = True):
        self.assigned_names = set()
        self.include_aug = include_aug

    def visit_AssignTarget(self, node: cst.AssignTarget) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def visit_AugAssign(self, node: cst.AugAssign) -> None:
        if self.include_aug:
            self.assigned_names |= self._extract_names(node.target)

    def visit_For(self, node: cst.For) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def visit_NamedExpr(self, node: cst.NamedExpr) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def _extract_names(self, node: cst.CSTNode) -> set[str]:
        names = set()
        if isinstance(node, cst.Name):
            names.add(node.value)
        elif isinstance(node, (cst.Tuple, cst.List)):
            for el in node.elements:
                names |= self._extract_names(el.value)
        elif isinstance(node, cst.StarredElement):
            names |= self._extract_names(node.value)
        return names

def _is_reassigned_in_body_any(body_stmts: list[cst.BaseStatement], name: str, include_aug: bool = True) -> bool:
    collector = _AssignTargetCollectorUseAny(include_aug=include_aug)
    for stmt in body_stmts:
        stmt.visit(collector)
    return name in collector.assigned_names

def _match_false_init(stmt: cst.BaseStatement) -> str | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    assign = stmt.body[0]
    if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
        return None
    target = assign.targets[0].target
    if not isinstance(target, cst.Name):
        return None
    if not isinstance(assign.value, cst.Name) or assign.value.value != "False":
        return None
    return target.value

def _match_any_loop_body(body_stmts: list[cst.BaseStatement], var_name: str) -> cst.BaseExpression | None:
    if len(body_stmts) != 1:
        return None
        
    stmt = body_stmts[0]
    if not isinstance(stmt, cst.If) or stmt.orelse is not None:
        return None

    small_stmts: list[cst.BaseSmallStatement] = []
    
    if isinstance(stmt.body, cst.IndentedBlock):
        for line in stmt.body.body:
            if not isinstance(line, cst.SimpleStatementLine):
                return None 
            small_stmts.extend(line.body)
    elif isinstance(stmt.body, cst.SimpleStatementSuite):
        small_stmts.extend(stmt.body.body)
    else:
        return None

    if not small_stmts:
        return None

    first_stmt = small_stmts[0]
    if not isinstance(first_stmt, cst.Assign) or len(first_stmt.targets) != 1:
        return None
    target = first_stmt.targets[0].target
    if not isinstance(target, cst.Name) or target.value != var_name:
        return None
    if not isinstance(first_stmt.value, cst.Name) or first_stmt.value.value != "True":
        return None

    if len(small_stmts) == 2:
        if not isinstance(small_stmts[1], cst.Break):
            return None
    elif len(small_stmts) > 2:
        return None

    return stmt.test

def _match_for_if_return_bool(stmt):
    match = _match_for_if_return(stmt)
    if match is None:
        return None
    target, iter_, condition, return_expr = match
    if isinstance(return_expr, cst.Name) and return_expr.value in ("True", "False"):
        return target, iter_, condition, return_expr
    return None



# ---------------------------------------------------------------------------
# min_max_identity
# ---------------------------------------------------------------------------

class _UnsafeNodeDetectorMinMax(cst.CSTVisitor):
    def __init__(self):
        self.is_unsafe = False

    def visit_Call(self, node: cst.Call) -> None:
        self.is_unsafe = True
        
    def visit_Await(self, node: cst.Await) -> None:
        self.is_unsafe = True
        
    def visit_Yield(self, node: cst.Yield) -> None:
        self.is_unsafe = True


def _is_safe_expr_min_max(node: cst.CSTNode) -> bool:
    detector = _UnsafeNodeDetectorMinMax()
    node.visit(detector)
    return not detector.is_unsafe


def _match_single_assign_in_block(block: cst.BaseSuite) -> tuple[cst.CSTNode, cst.CSTNode] | None:
    if isinstance(block, cst.IndentedBlock):
        if len(block.body) != 1:
            return None
        stmt = block.body[0]
        if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
            return None
        assign = stmt.body[0]
        if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
            return None
        return assign.targets[0].target, assign.value

    elif isinstance(block, cst.SimpleStatementSuite):
        if len(block.body) != 1:
            return None
        assign = block.body[0] 
        if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
            return None
        return assign.targets[0].target, assign.value

    return None


def _make_min_max_call(func_name: str, a: cst.CSTNode, b: cst.CSTNode) -> cst.Call:
    return cst.Call(
        func=cst.Name(func_name),
        args=[
            cst.Arg(value=a, comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))),
            cst.Arg(value=b),
        ],
    )


def _make_assign_stmt(original_if: cst.If, target: cst.CSTNode, value: cst.CSTNode) -> cst.SimpleStatementLine:
    return cst.SimpleStatementLine(
        body=[cst.Assign(
            targets=[cst.AssignTarget(target=target)],
            value=value,
        )],
        leading_lines=original_if.leading_lines,
        trailing_whitespace=cst.TrailingWhitespace(
            whitespace=cst.SimpleWhitespace(""),
            newline=cst.Newline(),
        ),
    )


# ---------------------------------------------------------------------------
# merge_duplicate_blocks
# ---------------------------------------------------------------------------
def _get_if_chain(node: cst.If) -> list:
    chain = []
    current = node
    while isinstance(current, cst.If):
        chain.append((current.test, current.body, False, current))
        if current.orelse is None:
            break
        elif isinstance(current.orelse, cst.Else):
            chain.append((None, current.orelse.body, True, current.orelse))
            break
        else:
            current = current.orelse
    return chain


def _bodies_equal(a: cst.BaseSuite, b: cst.BaseSuite) -> bool:
    if type(a) != type(b):
        return False

    if isinstance(a, cst.IndentedBlock) and isinstance(b, cst.IndentedBlock):
        if len(a.body) != len(b.body):
            return False
        return all(s1.deep_equals(s2) for s1, s2 in zip(a.body, b.body))

    if isinstance(a, cst.SimpleStatementSuite) and isinstance(b, cst.SimpleStatementSuite):
        if len(a.body) != len(b.body):
            return False
        return all(s1.deep_equals(s2) for s1, s2 in zip(a.body, b.body))

    return False


def _make_or(left: cst.BaseExpression, right: cst.BaseExpression) -> cst.BooleanOperation:
    def wrap_if_needed(node: cst.BaseExpression) -> cst.BaseExpression:
        if hasattr(node, "lpar") and node.lpar:
            return node
        if isinstance(node, (cst.IfExp, cst.Lambda)):
            return node.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])
        if isinstance(node, cst.BooleanOperation) and isinstance(node.operator, cst.And):
            return node.with_changes(lpar=[cst.LeftParen()], rpar=[cst.RightParen()])
        return node

    return cst.BooleanOperation(
        left=wrap_if_needed(left),
        operator=cst.Or(
            whitespace_before=cst.SimpleWhitespace(" "),
            whitespace_after=cst.SimpleWhitespace(" "),
        ),
        right=wrap_if_needed(right),
    )


def _rebuild_if_chain(original_node: cst.If, chain: list) -> cst.If:
    seen = []

    for condition, body, is_else, orig in chain:
        if is_else:
            seen.append((body, [], True, orig))
            continue

        if seen and not seen[-1][2] and _bodies_equal(seen[-1][0], body):
            seen[-1][1].append(condition)
        else:
            seen.append((body, [condition], False, orig))

    result = None
    for body, conditions, is_else, orig in reversed(seen):
        if is_else:
            result = orig
        else:
            merged_cond = conditions[0]
            for cond in conditions[1:]:
                merged_cond = _make_or(merged_cond, cond)

            result = orig.with_changes(
                test=merged_cond,
                body=body,
                orelse=result,
            )

    return result


# ---------------------------------------------------------------------------
# move_assign_in_block
# ---------------------------------------------------------------------------
def _is_literal_move(node) -> bool:
    if isinstance(node, (cst.Integer, cst.Float, cst.Imaginary,
                          cst.SimpleString, cst.ConcatenatedString)):
        return True
    if isinstance(node, cst.Name) and node.value in ("True", "False", "None"):
        return True
    if isinstance(node, (cst.Tuple, cst.List, cst.Set)):
        return all(_is_literal_move(el.value) for el in node.elements)
    if isinstance(node, cst.Dict):
        return all(
            isinstance(el, cst.DictElement)
            and _is_literal_move(el.key) and _is_literal_move(el.value)
            for el in node.elements
        )
    if isinstance(node, cst.UnaryOperation):
        return _is_literal_move(node.expression)
    return False

def _stmt_assigns_any(stmt, names: set[str]) -> bool:
    if not isinstance(stmt, cst.SimpleStatementLine):
        return False
    for small in stmt.body:
        if isinstance(small, cst.Assign):
            for t in small.targets:
                if isinstance(t.target, cst.Name) and t.target.value in names:
                    return True
        if isinstance(small, cst.AugAssign):
            if isinstance(small.target, cst.Name) and small.target.value in names:
                return True
    return False

def _match_simple_assign(stmt: cst.BaseStatement) -> str | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    assign = stmt.body[0]
    if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
        return None
    target = assign.targets[0].target
    if not isinstance(target, cst.Name):
        return None
    return target.value

def _collect_names_in_node_move(node: cst.CSTNode) -> set[str]:
    names = set()
    if isinstance(node, cst.Name):
        names.add(node.value)
    for child in node.children:
        if isinstance(child, cst.CSTNode):
            names |= _collect_names_in_node_move(child)
    return names

def _stmt_uses_name(stmt, name: str) -> bool:
    return name in _collect_names_in_node_move(stmt)


def _get_rhs_names(stmt) -> set[str]:
    assign = stmt.body[0]
    return _collect_names_in_node_move(assign.value)


def _collect_init_group(stmts: list, start: int) -> list[int]:
    group = []
    k = start
    while k < len(stmts):
        var = _match_simple_assign(stmts[k])
        if var is not None and _is_literal_move(stmts[k].body[0].value):
            group.append(k)
            k += 1
        else:
            break
    return group

def _match_for_if_return(stmt: cst.CSTNode) -> tuple[cst.CSTNode, cst.CSTNode, cst.BaseExpression, cst.BaseExpression] | None:

    if not isinstance(stmt, cst.For) or stmt.orelse is not None:
        return None
    if not isinstance(stmt.body, cst.IndentedBlock):
        return None
    if len(stmt.body.body) != 1:
        return None

    inner = stmt.body.body[0]
    if not isinstance(inner, cst.If) or inner.orelse is not None:
        return None

    if_small_stmts: list[cst.BaseSmallStatement] = []
    if isinstance(inner.body, cst.IndentedBlock):
        for line in inner.body.body:
            if isinstance(line, cst.SimpleStatementLine):
                if_small_stmts.extend(line.body)
            else:
                return None
    elif isinstance(inner.body, cst.SimpleStatementSuite):
        if_small_stmts.extend(inner.body.body)
    else:
        return None

    if len(if_small_stmts) != 1:
        return None

    ret_stmt = if_small_stmts[0]
    if not isinstance(ret_stmt, cst.Return) or ret_stmt.value is None:
        return None

    return stmt.target, stmt.iter, inner.test, ret_stmt.value


def _match_trailing_return(stmt: cst.CSTNode) -> tuple[bool, cst.BaseExpression | None]:

    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return False, None
    
    ret = stmt.body[0]
    if not isinstance(ret, cst.Return):
        return False, None
        
    return True, ret.value


# ---------------------------------------------------------------------------
# invert_any_all
# ---------------------------------------------------------------------------
def _negate_condition_any_all(test: cst.BaseExpression) -> cst.BaseExpression:
    if isinstance(test, cst.UnaryOperation) and isinstance(test.operator, cst.Not):
        return test.expression

    if isinstance(test, cst.Comparison) and len(test.comparisons) == 1:
        op = test.comparisons[0].operator
        inverse = {
            cst.Equal: cst.NotEqual,
            cst.NotEqual: cst.Equal,
            cst.LessThan: cst.GreaterThanEqual,
            cst.GreaterThan: cst.LessThanEqual,
            cst.LessThanEqual: cst.GreaterThan,
            cst.GreaterThanEqual: cst.LessThan,
            cst.In: cst.NotIn,
            cst.NotIn: cst.In,
            cst.Is: cst.IsNot,
            cst.IsNot: cst.Is,
        }.get(type(op))
        if inverse is not None:
            new_op = inverse(
                whitespace_before=op.whitespace_before,
                whitespace_after=op.whitespace_after,
            )
            return test.with_changes(
                comparisons=[test.comparisons[0].with_changes(operator=new_op)]
            )

    return cst.UnaryOperation(
        operator=cst.Not(whitespace_after=cst.SimpleWhitespace(" ")),
        expression=cst.ensure_type(test, cst.BaseExpression)
        if not isinstance(test, (cst.BooleanOperation, cst.IfExp))
        else test.with_changes(
            lpar=[cst.LeftParen()],
            rpar=[cst.RightParen()],
        ),
    )

# ---------------------------------------------------------------------------
# swap_if_else_branches
# ---------------------------------------------------------------------------

def _negate_condition(test: cst.BaseExpression) -> cst.BaseExpression:
    if isinstance(test, cst.UnaryOperation) and isinstance(test.operator, cst.Not):
        expr = test.expression
        outer_lpar = list(test.lpar)
        outer_rpar = list(test.rpar)
        inner_lpar = list(expr.lpar) if hasattr(expr, "lpar") else []
        inner_rpar = list(expr.rpar) if hasattr(expr, "rpar") else []
        new_lpar = outer_lpar + inner_lpar
        new_rpar = inner_rpar + outer_rpar
        if hasattr(expr, "lpar"):
            return expr.with_changes(lpar=new_lpar, rpar=new_rpar)
        return expr

    if isinstance(test, cst.Comparison) and len(test.comparisons) == 1:
        op = test.comparisons[0].operator
        inverse = {
            cst.Equal: cst.NotEqual,
            cst.NotEqual: cst.Equal,
            cst.LessThan: cst.GreaterThanEqual,
            cst.GreaterThan: cst.LessThanEqual,
            cst.LessThanEqual: cst.GreaterThan,
            cst.GreaterThanEqual: cst.LessThan,
            cst.In: cst.NotIn,
            cst.NotIn: cst.In,
            cst.Is: cst.IsNot,
            cst.IsNot: cst.Is,
        }.get(type(op))
        
        if inverse is not None:
            new_op = inverse(
                whitespace_before=op.whitespace_before,
                whitespace_after=op.whitespace_after,
            )
            return test.with_changes(
                comparisons=[test.comparisons[0].with_changes(operator=new_op)]
            )

    needs_parens = isinstance(test, (cst.BooleanOperation, cst.IfExp, cst.Lambda))
    has_parens = bool(test.lpar and test.rpar)

    wrapped_test = test
    if needs_parens and not has_parens:
        wrapped_test = test.with_changes(
            lpar=[cst.LeftParen()],
            rpar=[cst.RightParen()],
        )

    return cst.UnaryOperation(
        operator=cst.Not(whitespace_after=cst.SimpleWhitespace(" ")),
        expression=wrapped_test,
    )


def _is_pass(body: cst.BaseSuite) -> bool:

    if isinstance(body, cst.SimpleStatementSuite):
        return len(body.body) == 1 and isinstance(body.body[0], cst.Pass)
        
    if isinstance(body, cst.IndentedBlock):
        if len(body.body) != 1:
            return False
        stmt = body.body[0]
        return (
            isinstance(stmt, cst.SimpleStatementLine)
            and len(stmt.body) == 1
            and isinstance(stmt.body[0], cst.Pass)
        )
    return False


def _is_guard_body(body: cst.BaseSuite) -> bool:

    if isinstance(body, cst.SimpleStatementSuite):
        if not body.body: return False
        last = body.body[-1]
        return isinstance(last, (cst.Return, cst.Raise, cst.Break, cst.Continue))

    if isinstance(body, cst.IndentedBlock):
        if not body.body: return False
        last = body.body[-1]
        return (
            isinstance(last, cst.SimpleStatementLine)
            and last.body
            and isinstance(last.body[-1], (cst.Return, cst.Raise, cst.Break, cst.Continue))
        )
    return False

# ---------------------------------------------------------------------------
# remove_redundant_if
# ---------------------------------------------------------------------------
def _are_negations(cond_a: cst.BaseExpression, cond_b: cst.BaseExpression) -> bool:
    return (
        _negate_condition(cond_a).deep_equals(cond_b)
        or _negate_condition(cond_b).deep_equals(cond_a)
    )
    
def _unwrap_remove_red(node: cst.CSTNode) -> cst.CSTNode:

    if hasattr(node, "lpar") and hasattr(node, "rpar"):
        if node.lpar or node.rpar:
            return node.with_changes(lpar=[], rpar=[])
    return node

def _is_safe_expr_remove_red(node: cst.CSTNode) -> bool:
    node = _unwrap_remove_red(node)

    if isinstance(node, (cst.Call, cst.Await, cst.Yield)):
        return False

    if isinstance(node, (cst.Name, cst.Integer, cst.Float,
                         cst.SimpleString, cst.FormattedString)):
        return True

    if isinstance(node, cst.Attribute):
        return _is_safe_expr_remove_red(node.value)

    if isinstance(node, cst.Subscript):
        if not _is_safe_expr_remove_red(node.value):
            return False
        for slice_node in node.slice:
            if isinstance(slice_node.slice, cst.Index):
                if not _is_safe_expr_remove_red(slice_node.slice.value):
                    return False
        return True

    for child in node.children:
        if isinstance(child, cst.CSTNode) and not _is_safe_expr_remove_red(child):
            return False

    return True

# ---------------------------------------------------------------------------
# use_join
# ---------------------------------------------------------------------------

class _AssignTargetCollectorUseJoin(cst.CSTVisitor):

    def __init__(self, include_aug: bool = True):
        self.assigned_names = set()
        self.include_aug = include_aug

    def visit_AssignTarget(self, node: cst.AssignTarget) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def visit_AugAssign(self, node: cst.AugAssign) -> None:
        if self.include_aug:
            self.assigned_names |= self._extract_names(node.target)

    def visit_For(self, node: cst.For) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def visit_NamedExpr(self, node: cst.NamedExpr) -> None:
        self.assigned_names |= self._extract_names(node.target)

    def _extract_names(self, node: cst.CSTNode) -> set[str]:
        names = set()
        if isinstance(node, cst.Name):
            names.add(node.value)
        elif isinstance(node, (cst.Tuple, cst.List)):
            for el in node.elements:
                names |= self._extract_names(el.value)
        elif isinstance(node, cst.StarredElement):
            names |= self._extract_names(node.value)
        return names


class _NameReferenceCollectorUseJoin(cst.CSTVisitor):
    def __init__(self, target_name: str):
        self.target_name = target_name
        self.references_found = False

    def visit_Name(self, node: cst.Name) -> None:
        if node.value == self.target_name:
            self.references_found = True


def _is_reassigned_in_body_use_join(body_stmts: list[cst.BaseStatement], name: str, include_aug: bool = True) -> bool:
    collector = _AssignTargetCollectorUseJoin(include_aug=include_aug)
    for stmt in body_stmts:
        stmt.visit(collector)
    return name in collector.assigned_names


def _expr_references_name_use_join(expr: cst.CSTNode, name: str) -> bool:
    collector = _NameReferenceCollectorUseJoin(name)
    expr.visit(collector)
    return collector.references_found


def _match_empty_string_init(stmt: cst.BaseStatement) -> str | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    assign = stmt.body[0]
    if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
        return None
    target = assign.targets[0].target
    if not isinstance(target, cst.Name):
        return None
    if not isinstance(assign.value, cst.SimpleString):
        return None
    if assign.value.evaluated_value not in ("", b""):
        return None
    return target.value


def _match_aug_add_use_join(small_stmt: cst.BaseSmallStatement, var_name: str) -> cst.BaseExpression | None:
    if not isinstance(small_stmt, cst.AugAssign):
        return None
    if not isinstance(small_stmt.target, cst.Name) or small_stmt.target.value != var_name:
        return None
    if not isinstance(small_stmt.operator, cst.AddAssign):
        return None
    return small_stmt.value


def _match_loop_body_use_join(body_stmts: list[cst.BaseStatement], var_name: str) -> tuple[cst.BaseExpression, cst.BaseExpression | None] | None:

    if len(body_stmts) != 1:
        return None

    stmt = body_stmts[0]

    if isinstance(stmt, cst.SimpleStatementLine) and len(stmt.body) == 1:
        aug_expr = _match_aug_add_use_join(stmt.body[0], var_name)
        if aug_expr is not None:
            return aug_expr, None
        
    if not isinstance(stmt, cst.If) or stmt.orelse is not None:
        return None

    if_small_stmts: list[cst.BaseSmallStatement] = []
    if isinstance(stmt.body, cst.IndentedBlock):
        if len(stmt.body.body) != 1 or not isinstance(stmt.body.body[0], cst.SimpleStatementLine):
            return None
        if_small_stmts.extend(stmt.body.body[0].body)
    elif isinstance(stmt.body, cst.SimpleStatementSuite):
        if_small_stmts.extend(stmt.body.body)
    else:
        return None

    if len(if_small_stmts) != 1:
        return None

    aug_expr = _match_aug_add_use_join(if_small_stmts[0], var_name)
    if aug_expr is not None:
        return aug_expr, stmt.test

    return None


# ---------------------------------------------------------------------------
# for_append_to_extend
# ---------------------------------------------------------------------------

def _collect_target_names(target: cst.CSTNode) -> set:
    names: set = set()
    if isinstance(target, cst.Name):
        names.add(target.value)
    elif isinstance(target, (cst.Tuple, cst.List)):
        for el in target.elements:
            names |= _collect_target_names(el.value)
    elif isinstance(target, cst.StarredElement):
        names |= _collect_target_names(target.value)
    return names
 
 
def _collect_free_names(node: cst.CSTNode) -> set:
    names: set = set()
 
    class _Collector(cst.CSTVisitor):
        def visit_Name(self, n: cst.Name) -> bool:
            names.add(n.value)
            return True
 
    node.visit(_Collector())
    return names
 
 
def _references_any(node: cst.CSTNode, forbidden_names: set) -> bool:
    if not forbidden_names:
        return False
    return bool(_collect_free_names(node) & forbidden_names)
 
 
def _match_for_append(stmt: cst.BaseStatement):
    
    if not isinstance(stmt, cst.For) or stmt.orelse is not None:
        return None
    if not isinstance(stmt.body, cst.IndentedBlock):
        return None
 
    body_stmts = list(stmt.body.body)
    if len(body_stmts) != 1:
        return None
 
    inner = body_stmts[0]
    target_names = _collect_target_names(stmt.target)
 
    if isinstance(inner, cst.SimpleStatementLine) and len(inner.body) == 1:
        expr_stmt = inner.body[0]
        if isinstance(expr_stmt, cst.Expr) and isinstance(expr_stmt.value, cst.Call):
            call = expr_stmt.value
            if (
                isinstance(call.func, cst.Attribute)
                and call.func.attr.value == "append"
                and len(call.args) == 1
            ):
                list_node = call.func.value
                if _references_any(list_node, target_names):
                    return None
                return list_node, call.args[0].value, None
 
    if (
        isinstance(inner, cst.If)
        and inner.orelse is None
        and isinstance(inner.body, cst.IndentedBlock)
        and len(inner.body.body) == 1
    ):
        if_inner = inner.body.body[0]
        if isinstance(if_inner, cst.SimpleStatementLine) and len(if_inner.body) == 1:
            expr_stmt = if_inner.body[0]
            if isinstance(expr_stmt, cst.Expr) and isinstance(expr_stmt.value, cst.Call):
                call = expr_stmt.value
                if (
                    isinstance(call.func, cst.Attribute)
                    and call.func.attr.value == "append"
                    and len(call.args) == 1
                ):
                    list_node = call.func.value
                    if _references_any(list_node, target_names):
                        return None
                    return list_node, call.args[0].value, inner.test
 
    return None

# ---------------------------------------------------------------------------
# hoist_similar_statement_from_if
# ---------------------------------------------------------------------------

def _get_indented_stmts(body: cst.BaseSuite) -> list[cst.BaseStatement] | None:
    if not isinstance(body, cst.IndentedBlock):
        return None
    return list(body.body)


def _make_pass_node() -> cst.SimpleStatementLine:
    return cst.SimpleStatementLine(body=[cst.Pass()])


# ---------------------------------------------------------------------------
# identity_comprehension
# ---------------------------------------------------------------------------

def _is_identity_comp(target, elt) -> bool:
    return (
        isinstance(target, cst.Name)
        and isinstance(elt, cst.Name)
        and target.value == elt.value
    )


def _is_kv_identity_comp(target, key, value) -> bool:
    if not isinstance(target, cst.Tuple) or len(target.elements) != 2:
        return False
    k = target.elements[0].value
    v = target.elements[1].value
    return (
        isinstance(k, cst.Name) and isinstance(v, cst.Name)
        and isinstance(key, cst.Name) and isinstance(value, cst.Name)
        and k.value == key.value and v.value == value.value
    )


def _is_items_call(node) -> cst.BaseExpression | None:
    if not isinstance(node, cst.Call):
        return None
    if node.args:
        return None
    func = node.func
    if not isinstance(func, cst.Attribute):
        return None
    if func.attr.value != "items":
        return None
    return func.value


# ---------------------------------------------------------------------------
# merge_list_appends_into_extend
# ---------------------------------------------------------------------------
def _match_append_or_extend(stmt):
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    
    expr_stmt = stmt.body[0]
    if not isinstance(expr_stmt, cst.Expr):
        return None
        
    call = expr_stmt.value
    if not isinstance(call, cst.Call) or not isinstance(call.func, cst.Attribute):
        return None
        
    if not isinstance(call.func.value, cst.Name):
        return None
        
    if len(call.args) != 1 or call.args[0].keyword is not None:
        return None

    list_name = call.func.value.value
    method = call.func.attr.value
    arg = call.args[0].value

    if method == "append":
        return list_name, [arg]

    if method == "extend":
        if isinstance(arg, (cst.List, cst.Tuple)):
            if any(isinstance(el, cst.StarredElement) for el in arg.elements):
                return None
            return list_name, [el.value for el in arg.elements]
            
    return None


# ---------------------------------------------------------------------------
# boolean_if_exp_identity
# ---------------------------------------------------------------------------   
def _unwrap_boolean(node: cst.CSTNode) -> cst.CSTNode:

    if hasattr(node, "lpar") and hasattr(node, "rpar"):
        if node.lpar or node.rpar:
            return node.with_changes(lpar=[], rpar=[])
    return node


def _is_bool_expr(node: cst.CSTNode) -> bool:

    node = _unwrap_boolean(node)

    if isinstance(node, cst.Comparison):
        return True

    if isinstance(node, cst.UnaryOperation) and isinstance(node.operator, cst.Not):
        return True

    if isinstance(node, cst.Call):
        func = _unwrap_boolean(node.func)
        if isinstance(func, cst.Name) and func.value in ("isinstance", "issubclass"):
            return True
            
    return False


# ---------------------------------------------------------------------------
# remove_redundant_slice_index
# ---------------------------------------------------------------------------
def _unwrap_remove_redundant(node: cst.CSTNode) -> cst.CSTNode:
    if hasattr(node, "lpar") and hasattr(node, "rpar"):
        if node.lpar or node.rpar:
            return node.with_changes(lpar=[], rpar=[])
    return node


def _is_zero(node: cst.CSTNode) -> bool:
    node = _unwrap_remove_redundant(node)
    return isinstance(node, cst.Integer) and node.value == "0"


def _is_none(node: cst.CSTNode) -> bool:
    node = _unwrap_remove_redundant(node)
    return isinstance(node, cst.Name) and node.value == "None"


def _is_safe_node(node: cst.CSTNode) -> bool:

    node = _unwrap_remove_redundant(node)
    if isinstance(node, cst.Name):
        return True
    if isinstance(node, cst.Attribute):
        return _is_safe_node(node.value)
    return False


def _is_len_of(node: cst.CSTNode, sliced_node: cst.CSTNode) -> bool:
    node = _unwrap_remove_redundant(node)
    if not isinstance(node, cst.Call):
        return False
    if not isinstance(node.func, cst.Name) or node.func.value != "len":
        return False
    if len(node.args) != 1 or node.args[0].keyword is not None:
        return False
    
    len_arg = _unwrap_remove_redundant(node.args[0].value)
    target = _unwrap_remove_redundant(sliced_node)
    
    if not _is_safe_node(target):
        return False
        
    return len_arg.deep_equals(target)

# ---------------------------------------------------------------------------
# assign_if_exp
# ---------------------------------------------------------------------------
def _get_indented_stmts_assign(body: cst.BaseSuite) -> list[cst.BaseStatement] | None:
    if not isinstance(body, cst.IndentedBlock):
        return None
    return list(body.body)

def _match_single_assign_if_exp(stmt: cst.BaseStatement):
    if not isinstance(stmt, cst.SimpleStatementLine) or len(stmt.body) != 1:
        return None
    assign = stmt.body[0]
    
    if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
        return None
        
    return assign.targets[0].target, assign.value, stmt

def _ensure_parens_for_tuple(node: cst.BaseExpression) -> cst.BaseExpression:
    if isinstance(node, (cst.Tuple, cst.IfExp, cst.Lambda)) and not node.lpar:
        return node.with_changes(
            lpar=[cst.LeftParen()],
            rpar=[cst.RightParen()]
        )
    return node

def _is_elif_of_parent(node, parent):
    return isinstance(parent, cst.If) and parent.orelse is node