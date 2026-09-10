import libcst as cst
import libcst.metadata as cst_meta
import refact_transform_utils as refact_utils
# ---------------------------------------------------------------------------
# remove_unnecessary_else
# ---------------------------------------------------------------------------
class _RemoveUnnecessaryElseTransformer(cst.CSTTransformer):

    def leave_If(
        self, original_node: cst.If, updated_node: cst.If
    ) -> cst.CSTNode | cst.FlattenSentinel:
        
        if not refact_utils.has_unnecessary_else(updated_node):
            return updated_node

        else_node: cst.Else = updated_node.orelse
        
        if isinstance(else_node.body, cst.SimpleStatementSuite):
            else_stmts = [cst.SimpleStatementLine(body=else_node.body.body)]
        else:
            else_stmts = list(else_node.body.body)

        if else_stmts and else_node.leading_lines:
            first = else_stmts[0]
            if hasattr(first, "leading_lines"):
                else_stmts[0] = first.with_changes(
                    leading_lines=list(else_node.leading_lines) + list(first.leading_lines)
                )

        new_if = updated_node.with_changes(orelse=None)

        return cst.FlattenSentinel([new_if] + else_stmts)
    
# ---------------------------------------------------------------------------
# hoist_statement_from_loop
# ---------------------------------------------------------------------------

class _HoistStatementFromLoopTransformer(cst.CSTTransformer):

    def _process_stmts(
        self, stmts: tuple[cst.BaseStatement, ...]
    ) -> tuple[list[cst.BaseStatement], bool]:
        new_stmts: list[cst.BaseStatement] = []
        changed = False

        for stmt in stmts:
            if isinstance(stmt, (cst.For, cst.While)) and isinstance(stmt.body, cst.IndentedBlock):
                
                loop_var_names = set()
                if isinstance(stmt, cst.For):
                    loop_var_names = refact_utils._extract_all_names(stmt.target)
                
                body_stmts = list(stmt.body.body)
                
                visitor = refact_utils.DeepAssignmentCounter()
                stmt.body.visit(visitor)
                name_counts = visitor.counts

                hoistable = [
                    s for s in body_stmts
                    if refact_utils._is_hoistable(s, loop_var_names)
                    and name_counts[s.body[0].targets[0].target.value] == 1
                ]
                remaining = [s for s in body_stmts if s not in hoistable]

                if hoistable:
                    changed = True
                    new_stmts.extend(hoistable)
                    new_stmts.append(stmt.with_changes(
                        body=stmt.body.with_changes(body=remaining)
                    ))
                else:
                    new_stmts.append(stmt)
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

# ---------------------------------------------------------------------------
# boolean_if_exp_identity
# ---------------------------------------------------------------------------    
class _BooleanIfExpIdentityTransformer(cst.CSTTransformer):
    def leave_IfExp(
        self, original_node: cst.IfExp, updated_node: cst.IfExp
    ) -> cst.BaseExpression:
        
        body = refact_utils._unwrap_boolean(updated_node.body)
        orelse = refact_utils._unwrap_boolean(updated_node.orelse)
        test = updated_node.test

        is_true  = lambda n: isinstance(n, cst.Name) and n.value == "True"
        is_false = lambda n: isinstance(n, cst.Name) and n.value == "False"

        if is_true(body) and is_false(orelse):
            if not refact_utils._is_bool_expr(test):
                return cst.Call(
                    func=cst.Name("bool"),
                    args=[cst.Arg(value=test)]
                )
            return test

        if is_false(body) and is_true(orelse):
            test_unwrapped = refact_utils._unwrap_boolean(test)
            if isinstance(test_unwrapped, (cst.BooleanOperation, cst.IfExp)):
                test_unwrapped = test_unwrapped.with_changes(
                    lpar=[cst.LeftParen()], rpar=[cst.RightParen()]
                )

            return cst.UnaryOperation(
                operator=cst.Not(whitespace_after=cst.SimpleWhitespace(" ")),
                expression=test_unwrapped,
            )

        return updated_node
    
# ---------------------------------------------------------------------------
# list_comprehension
# ---------------------------------------------------------------------------
class _ListComprehensionTransformer(cst.CSTTransformer):

    def _process_stmts(
        self, stmts: tuple[cst.BaseStatement, ...]
    ) -> tuple[list[cst.BaseStatement], bool]:
        new_stmts: list[cst.BaseStatement] = []
        changed = False
        i = 0
        
        while i < len(stmts):
            stmt = stmts[i]
            list_name = refact_utils._match_list_init_list(stmt)
            
            if list_name and i + 1 < len(stmts):
                next_stmt = stmts[i + 1]
                if (
                    isinstance(next_stmt, cst.For)
                    and next_stmt.orelse is None
                    and isinstance(next_stmt.body, cst.IndentedBlock)
                    and not refact_utils._is_reassigned_in_body_list(next_stmt.body.body, list_name)
                ):
                    match = refact_utils._match_append_in_body(next_stmt.body.body, list_name)
                    if match is not None:
                        append_expr, condition = match
                        comprehension = cst.ListComp(
                            elt=append_expr,
                            for_in=cst.CompFor(
                                target=next_stmt.target,
                                iter=next_stmt.iter,
                                ifs=[cst.CompIf(
                                    test=condition,
                                    whitespace_before=cst.SimpleWhitespace(" "),
                                )] if condition is not None else [],
                                whitespace_before=cst.SimpleWhitespace(" "),
                                whitespace_after_for=cst.SimpleWhitespace(" "),
                                whitespace_before_in=cst.SimpleWhitespace(" "),
                                whitespace_after_in=cst.SimpleWhitespace(" "),
                            ),
                        )

                        new_assign = stmt.body[0].with_changes(value=comprehension)
                        new_stmts.append(stmt.with_changes(body=[new_assign]))
                        changed = True
                        i += 2 
                        continue
                        
            new_stmts.append(stmt)
            i += 1
            
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

# ---------------------------------------------------------------------------
# remove_unnecessary_cast
# ---------------------------------------------------------------------------

_CAST_FNS = {"int", "str", "float", "bool"}

class _RemoveUnnecessaryCastTransformer(cst.CSTTransformer):
    def __init__(self):
        super().__init__()
        self._scope_stack: list[dict[str, str]] = [{}]

    @property
    def _scope(self) -> dict[str, str]:
        return self._scope_stack[-1]

    def _lookup_var_type(self, name: str) -> str | None:
        for scope in reversed(self._scope_stack):
            if name in scope:
                return scope[name]
        return None

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

    def leave_Assign(
        self, original_node: cst.Assign, updated_node: cst.Assign
    ) -> cst.Assign:
        if len(updated_node.targets) == 1:
            target = refact_utils._unwrap_remove(updated_node.targets[0].target)
            if isinstance(target, cst.Name):
                t = refact_utils._get_literal_type(updated_node.value)
                if t is None:
                    val = refact_utils._unwrap_remove(updated_node.value)
                    if (
                        isinstance(val, cst.Call)
                        and isinstance(val.func, cst.Name)
                        and val.func.value in _CAST_FNS
                        and len(val.args) == 1
                    ):
                        t = val.func.value
                if t:
                    self._scope[target.value] = t
                else:
                    self._scope.pop(target.value, None)
        return updated_node

    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        func = refact_utils._unwrap_remove(updated_node.func)
        if not isinstance(func, cst.Name) or func.value not in _CAST_FNS:
            return updated_node
        if len(updated_node.args) != 1:
            return updated_node
            
        arg = updated_node.args[0]
        if arg.keyword is not None or arg.star != "":
            return updated_node

        arg_value = arg.value
        cast_fn = func.value

        if refact_utils._get_literal_type(arg_value) == cast_fn:
            return arg_value

        unwrapped_arg = refact_utils._unwrap_remove(arg_value)
        if isinstance(unwrapped_arg, cst.Name):
            if self._lookup_var_type(unwrapped_arg.value) == cast_fn:
                return arg_value

        return updated_node
    
# ---------------------------------------------------------------------------
# merge-comparisons
# ---------------------------------------------------------------------------
class _MergeComparisonsTransformer(cst.CSTTransformer):

    def leave_BooleanOperation(
        self, original_node: cst.BooleanOperation, updated_node: cst.BooleanOperation
    ) -> cst.BaseExpression:
        if not isinstance(updated_node.operator, cst.Or):
            return updated_node

        operands = refact_utils._flatten_or(updated_node)
        pairs = [refact_utils._match_eq_comparison(op) for op in operands]

        if any(p is None for p in pairs):
            return updated_node

        first_lhs = pairs[0][0]
        if not all(p[0].deep_equals(first_lhs) for p in pairs[1:]):
            return updated_node

        if not refact_utils._is_safe_lhs(first_lhs):
            return updated_node

        if not all(refact_utils._is_literal_merge(rhs) for _, rhs in pairs):
            return updated_node

        elements = [
            cst.Element(
                value=rhs,
                comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
                if i < len(pairs) - 1
                else cst.MaybeSentinel.DEFAULT,
            )
            for i, (_, rhs) in enumerate(pairs)
        ]
        
        return cst.Comparison(
            left=first_lhs,
            comparisons=[
                cst.ComparisonTarget(
                    operator=cst.In(
                        whitespace_before=cst.SimpleWhitespace(" "),
                        whitespace_after=cst.SimpleWhitespace(" "),
                    ),
                    comparator=cst.List(elements=elements),
                )
            ],
            lpar=updated_node.lpar,
            rpar=updated_node.rpar,
        )
        
# ---------------------------------------------------------------------------
# sum-comprehension
# ---------------------------------------------------------------------------
class _SumComprehensionTransformer(cst.CSTTransformer):

    def _process_stmts(
        self, stmts: tuple[cst.BaseStatement, ...]
    ) -> tuple[list[cst.BaseStatement], bool]:
        new_stmts: list[cst.BaseStatement] = []
        changed = False
        i = 0
        
        while i < len(stmts):
            stmt = stmts[i]
            var_name = refact_utils._match_zero_init(stmt)
            
            if var_name and i + 1 < len(stmts):
                next_stmt = stmts[i + 1]

                if (
                    isinstance(next_stmt, cst.For)
                    and next_stmt.orelse is None
                    and isinstance(next_stmt.body, cst.IndentedBlock)
                ):
                    match = refact_utils._match_loop_body_for_sum(next_stmt.body.body, var_name)
                    if match is not None:
                        aug_expr, condition = match
                        
                        if (
                            not refact_utils._is_reassigned_in_body_sum(next_stmt.body.body, var_name, include_aug=False)
                            and not refact_utils._expr_references_name_sum(aug_expr, var_name)
                        ):
                            comp_for = cst.CompFor(
                                target=next_stmt.target,
                                iter=next_stmt.iter,
                                ifs=[cst.CompIf(
                                    test=condition,
                                    whitespace_before=cst.SimpleWhitespace(" "),
                                )] if condition is not None else [],
                                whitespace_before=cst.SimpleWhitespace(" "),
                                whitespace_after_for=cst.SimpleWhitespace(" "),
                                whitespace_before_in=cst.SimpleWhitespace(" "),
                                whitespace_after_in=cst.SimpleWhitespace(" "),
                            )
                            
                            sum_call = cst.Call(
                                func=cst.Name("sum"),
                                args=[cst.Arg(value=cst.GeneratorExp(elt=aug_expr, for_in=comp_for))],
                            )
                            
                            new_assign = stmt.body[0].with_changes(value=sum_call)
                            new_stmts.append(stmt.with_changes(body=[new_assign]))
                            changed = True
                            i += 2 
                            continue
                            
            new_stmts.append(stmt)
            i += 1
            
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
    
# ---------------------------------------------------------------------------
# merge_list_append
# ---------------------------------------------------------------------------

class _MergeListAppendTransformer(cst.CSTTransformer):

    def _process_stmts(
        self, stmts: tuple[cst.BaseStatement, ...]
    ) -> tuple[list[cst.BaseStatement], bool]:
        new_stmts: list[cst.BaseStatement] = []
        changed = False
        i = 0
        
        while i < len(stmts):
            stmt = stmts[i]
            list_name = refact_utils._match_list_init_merge(stmt)
            
            if list_name:
                appended = []
                j = i + 1

                while j < len(stmts):
                    expr = refact_utils._match_append_call_merge(stmts[j], list_name)

                    if expr is None or refact_utils._expr_references_name_merge(expr, list_name):
                        break
                        
                    appended.append(expr)
                    j += 1

                if appended:
                    changed = True
                    elements = [
                        cst.Element(
                            value=expr,
                            comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
                            if k < len(appended) - 1
                            else cst.MaybeSentinel.DEFAULT,
                        )
                        for k, expr in enumerate(appended)
                    ]

                    new_assign = stmt.body[0].with_changes(
                        value=cst.List(elements=elements)
                    )
                    new_stmts.append(stmt.with_changes(body=[new_assign]))

                    i = j
                    continue
                    
            new_stmts.append(stmt)
            i += 1
            
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


# ---------------------------------------------------------------------------
# merge_nested_ifs
# ---------------------------------------------------------------------------

class _MergeNestedIfsTransformer(cst.CSTTransformer):

    def leave_If(
        self, original_node: cst.If, updated_node: cst.If
    ) -> cst.BaseStatement:

        if updated_node.orelse is not None:
            return updated_node

        body = updated_node.body
        if not isinstance(body, cst.IndentedBlock) or len(body.body) != 1:
            return updated_node

        inner = body.body[0]

        if not isinstance(inner, cst.If) or inner.orelse is not None:
            return updated_node

        merged_test = cst.BooleanOperation(
            left= refact_utils._maybe_parenthesize(updated_node.test),
            operator=cst.And(
                whitespace_before=cst.SimpleWhitespace(" "),
                whitespace_after=cst.SimpleWhitespace(" "),
            ),
            right= refact_utils._maybe_parenthesize(inner.test),
        )

        return updated_node.with_changes(
            test=merged_test,
            body=inner.body,
            leading_lines=updated_node.leading_lines
        )
        
# ---------------------------------------------------------------------------
# inline_immediately_returned_variable
# ---------------------------------------------------------------------------
class _InlineImmediatelyReturnedVariableTransformer(cst.CSTTransformer):

    def _process_stmts(
        self, stmts: tuple[cst.BaseStatement, ...]
    ) -> tuple[list[cst.BaseStatement], bool]:
        new_stmts: list[cst.BaseStatement] = []
        changed = False
        i = 0
        
        while i < len(stmts):
            stmt = stmts[i]
            
            if i + 1 < len(stmts):
                next_stmt = stmts[i + 1]
                var_name = refact_utils._match_simple_assign_inline(stmt)
                
                if var_name and refact_utils._match_return(next_stmt, var_name):
                    
                    if refact_utils._is_declared_global_or_nonlocal(stmts, var_name):
                        new_stmts.append(stmt)
                        i += 1
                        continue

                    expr = stmt.body[0].value

                    if isinstance(expr, cst.Yield):
                        new_stmts.append(stmt)
                        i += 1
                        continue

                    combined_leading_lines = stmt.leading_lines + next_stmt.leading_lines

                    new_return = next_stmt.with_changes(
                        leading_lines=combined_leading_lines,
                        body=[cst.Return(
                            value=expr,
                            whitespace_after_return=cst.SimpleWhitespace(" "),
                        )]
                    )
                    new_stmts.append(new_return)
                    changed = True
                    i += 2
                    continue
            
            new_stmts.append(stmt)
            i += 1
            
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
    
# ---------------------------------------------------------------------------
# for_index_underscore
# ---------------------------------------------------------------------------  

class _ForIndexUnderscoreTransformer(cst.CSTTransformer):

    def leave_For(
        self, original_node: cst.For, updated_node: cst.For
    ) -> cst.For:
        target_names = refact_utils._get_target_names(updated_node.target)

        used_names = refact_utils._collect_names_in_node_for_index(updated_node.body)

        if updated_node.orelse is not None:
            used_names |= refact_utils._collect_names_in_node_for_index(updated_node.orelse)

        unused = target_names - used_names
        if not unused:
            return updated_node

        if "_" in used_names:
            return updated_node

        new_target = refact_utils._replace_unused_in_target(updated_node.target, used_names)
        return updated_node.with_changes(target=new_target)

    def leave_ListComp(self, original_node: cst.ListComp, updated_node: cst.ListComp) -> cst.ListComp:
        return refact_utils._handle_comp(updated_node)

    def leave_GeneratorExp(self, original_node: cst.GeneratorExp, updated_node: cst.GeneratorExp) -> cst.GeneratorExp:
        return refact_utils._handle_comp(updated_node)

    def leave_SetComp(self, original_node: cst.SetComp, updated_node: cst.SetComp) -> cst.SetComp:
        return refact_utils._handle_comp(updated_node)
        
    def leave_DictComp(self, original_node: cst.DictComp, updated_node: cst.DictComp) -> cst.DictComp:
        return refact_utils._handle_comp(updated_node)
    
# ---------------------------------------------------------------------------
# simplify_constant_sum
# ---------------------------------------------------------------------------
class _SimplifyConstantSumTransformer(cst.CSTTransformer):

    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        # must be sum(...)
        if not isinstance(updated_node.func, cst.Name):
            return updated_node
        if updated_node.func.value != "sum":
            return updated_node
        if len(updated_node.args) != 1:
            return updated_node

        arg = updated_node.args[0]
        if arg.keyword is not None:
            return updated_node

        gen = arg.value
        if not isinstance(gen, cst.GeneratorExp):
            return updated_node

        comp_for = gen.for_in

        if comp_for.inner_for_in is not None:
            return updated_node

        if not isinstance(gen.elt, cst.Integer) or gen.elt.value != "1":
            return updated_node

        if not comp_for.ifs:
            return updated_node

        combined = refact_utils._combine_conditions(list(comp_for.ifs))
        new_elt = cst.Call(
            func=cst.Name("bool"),
            args=[cst.Arg(value=combined)],
        )
        new_for_in = comp_for.with_changes(ifs=[])
        new_gen = gen.with_changes(elt=new_elt, for_in=new_for_in)
        return updated_node.with_changes(args=[arg.with_changes(value=new_gen)])
    

# ---------------------------------------------------------------------------
# simplify_len_comparison
# ---------------------------------------------------------------------------
class _SimplifyLenComparisonTransformer(cst.CSTTransformer):

    def leave_Comparison(
            self, original_node: cst.Comparison, updated_node: cst.Comparison
        ) -> cst.BaseExpression:
            if len(updated_node.comparisons) != 1:
                return updated_node

            comp = updated_node.comparisons[0]
            op = comp.operator
            left = updated_node.left
            right = comp.comparator

            len_arg = refact_utils._match_len_call(left)
            if len_arg is not None:

                if isinstance(len_arg, cst.GeneratorExp):
                    return updated_node

                literal_val = refact_utils._match_int_zero_or_one(right)
                if literal_val is not None:
                    result = refact_utils._simplify_len_comparison(len_arg, op, literal_val)
                    if result is not None:
                        new_lpar = tuple(updated_node.lpar) + tuple(left.lpar) + tuple(result.lpar)
                        new_rpar = tuple(result.rpar) + tuple(left.rpar) + tuple(updated_node.rpar)

                        if isinstance(result, cst.GeneratorExp) and not new_lpar:
                            new_lpar = (cst.LeftParen(),)
                            new_rpar = (cst.RightParen(),)

                        return result.with_changes(lpar=new_lpar, rpar=new_rpar)

            literal_val = refact_utils._match_int_zero_or_one(left)
            if literal_val is not None:
                len_arg = refact_utils._match_len_call(right)
                if len_arg is not None:

                    if isinstance(len_arg, cst.GeneratorExp):
                        return updated_node

                    result = refact_utils._simplify_len_comparison_reversed(literal_val, op, len_arg)
                    if result is not None:
                        new_lpar = tuple(updated_node.lpar) + tuple(right.lpar) + tuple(result.lpar)
                        new_rpar = tuple(result.rpar) + tuple(right.rpar) + tuple(updated_node.rpar)
                        
                        if isinstance(result, cst.GeneratorExp) and not new_lpar:
                            new_lpar = (cst.LeftParen(),)
                            new_rpar = (cst.RightParen(),)

                        return result.with_changes(lpar=new_lpar, rpar=new_rpar)

            return updated_node
        

class _ComprehensionToGeneratorTransformer(cst.CSTTransformer):

    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if not isinstance(updated_node.func, cst.Name):
            return updated_node
        if updated_node.func.value not in refact_utils._GENERATOR_ACCEPTING_FUNCS:
            return updated_node
        if not updated_node.args:
            return updated_node

        first_arg = updated_node.args[0]
        if first_arg.keyword is not None or first_arg.star != "":
            return updated_node
        if not isinstance(first_arg.value, cst.ListComp):
            return updated_node

        listcomp = first_arg.value
        
        if refact_utils._is_async_comp(listcomp.for_in):
            return updated_node

        if refact_utils._has_named_expr(listcomp):
            return updated_node

        genexp = refact_utils._listcomp_to_genexp(listcomp)
        remaining_args = updated_node.args[1:]

        if not remaining_args:
            new_first = first_arg.with_changes(value=genexp)
        else:
            new_first = first_arg.with_changes(
                value=genexp.with_changes(
                    lpar=[cst.LeftParen()],
                    rpar=[cst.RightParen()],
                )
            )

        return updated_node.with_changes(args=[new_first, *remaining_args])
    
# ---------------------------------------------------------------------------
# use_any
# ---------------------------------------------------------------------------

class _UseAnyTransformer(cst.CSTTransformer):
 
    def _process_stmts(self, stmts):
        new_stmts = []
        changed = False
        i = 0
 
        while i < len(stmts):
            stmt = stmts[i]
 
            var_name = refact_utils._match_false_init(stmt)
 
            if var_name and i + 1 < len(stmts):
                next_stmt = stmts[i + 1]
 
                if (
                    isinstance(next_stmt, cst.For)
                    and next_stmt.orelse is None
                    and isinstance(next_stmt.body, cst.IndentedBlock)
                ):
                    condition = refact_utils._match_any_loop_body(next_stmt.body.body, var_name)
 
                    if condition is not None:
                        any_call = cst.Call(
                            func=cst.Name("any"),
                            args=[cst.Arg(
                                value=cst.GeneratorExp(
                                    elt=condition,
                                    for_in=cst.CompFor(
                                        target=next_stmt.target,
                                        iter=next_stmt.iter,
                                        whitespace_before=cst.SimpleWhitespace(" "),
                                        whitespace_after_for=cst.SimpleWhitespace(" "),
                                        whitespace_before_in=cst.SimpleWhitespace(" "),
                                        whitespace_after_in=cst.SimpleWhitespace(" "),
                                    ),
                                )
                            )],
                        )
 
                        new_assign = stmt.body[0].with_changes(value=any_call)
                        new_stmts.append(stmt.with_changes(body=[new_assign]))
                        changed = True
                        i += 2
                        continue

            bool_match = refact_utils._match_for_if_return_bool(stmt)
 
            if bool_match is not None:
                target, iter_, condition, bool_literal = bool_match

                if isinstance(iter_, cst.Tuple) and not iter_.lpar:
                    iter_ = iter_.with_changes(
                        lpar=[cst.LeftParen()], rpar=[cst.RightParen()]
                    )
 
                any_call = cst.Call(
                    func=cst.Name("any"),
                    args=[cst.Arg(
                        value=cst.GeneratorExp(
                            elt=condition,
                            for_in=cst.CompFor(
                                target=target,
                                iter=iter_,
                                whitespace_before=cst.SimpleWhitespace(" "),
                                whitespace_after_for=cst.SimpleWhitespace(" "),
                                whitespace_before_in=cst.SimpleWhitespace(" "),
                                whitespace_after_in=cst.SimpleWhitespace(" "),
                            ),
                        )
                    )],
                )
 
                new_if = cst.If(
                    test=any_call,
                    body=cst.IndentedBlock(
                        body=[cst.SimpleStatementLine(
                            body=[cst.Return(value=bool_literal)]
                        )]
                    ),
                    leading_lines=stmt.leading_lines,
                )
 
                new_stmts.append(new_if)
                changed = True
                i += 1
                continue
 
            new_stmts.append(stmt)
            i += 1
 
        return new_stmts, changed
 
    def leave_IndentedBlock(self, original_node, updated_node):
        new_body, changed = self._process_stmts(updated_node.body)
        return updated_node.with_changes(body=new_body) if changed else updated_node
 
    def leave_Module(self, original_node, updated_node):
        new_body, changed = self._process_stmts(updated_node.body)
        return updated_node.with_changes(body=new_body) if changed else updated_node
 


# ---------------------------------------------------------------------------
# min_max_identity
# ---------------------------------------------------------------------------

class _MinMaxIdentityTransformer(cst.CSTTransformer):
 
    METADATA_DEPENDENCIES = (cst_meta.ParentNodeProvider,)
 
    def _compute_merged_stmt(self, updated_node: cst.If):

        if not isinstance(updated_node.test, cst.Comparison):
            return None
        if len(updated_node.test.comparisons) != 1:
            return None
 
        test = updated_node.test
        op = test.comparisons[0].operator
        left = test.left
        right = test.comparisons[0].comparator

        if not refact_utils._is_safe_expr_min_max(left) or not refact_utils._is_safe_expr_min_max(right):
            return None
 
        if_assign = refact_utils._match_single_assign_in_block(updated_node.body)
        if if_assign is None:
            return None
 
        if_target, if_value = if_assign

        if updated_node.orelse is None:
            if if_target.deep_equals(left) and if_value.deep_equals(right):
                if isinstance(op, (cst.GreaterThanEqual, cst.GreaterThan)):
                    call = refact_utils._make_min_max_call("min", left, right)
                    return refact_utils._make_assign_stmt(updated_node, if_target, call)
                if isinstance(op, (cst.LessThanEqual, cst.LessThan)):
                    call = refact_utils._make_min_max_call("max", left, right)
                    return refact_utils._make_assign_stmt(updated_node, if_target, call)

            if if_target.deep_equals(right) and if_value.deep_equals(left):
                if isinstance(op, (cst.LessThanEqual, cst.LessThan)):
                    call = refact_utils._make_min_max_call("min", right, left)
                    return refact_utils._make_assign_stmt(updated_node, if_target, call)
                if isinstance(op, (cst.GreaterThanEqual, cst.GreaterThan)):
                    call = refact_utils._make_min_max_call("max", right, left)
                    return refact_utils._make_assign_stmt(updated_node, if_target, call)
 
            return None

        if not isinstance(updated_node.orelse, cst.Else):
            return None
 
        else_assign = refact_utils._match_single_assign_in_block(updated_node.orelse.body)
        if else_assign is None:
            return None
 
        else_target, else_value = else_assign
 
        if not if_target.deep_equals(else_target):
            return None

        if if_value.deep_equals(left) and else_value.deep_equals(right):
            if isinstance(op, (cst.LessThan, cst.LessThanEqual)):
                call = refact_utils._make_min_max_call("min", left, right)
                return refact_utils._make_assign_stmt(updated_node, if_target, call)
            if isinstance(op, (cst.GreaterThan, cst.GreaterThanEqual)):
                call = refact_utils._make_min_max_call("max", left, right)
                return refact_utils._make_assign_stmt(updated_node, if_target, call)

        if if_value.deep_equals(right) and else_value.deep_equals(left):
            if isinstance(op, (cst.LessThan, cst.LessThanEqual)):
                call = refact_utils._make_min_max_call("max", left, right)
                return refact_utils._make_assign_stmt(updated_node, if_target, call)
            if isinstance(op, (cst.GreaterThan, cst.GreaterThanEqual)):
                call = refact_utils._make_min_max_call("min", left, right)
                return refact_utils._make_assign_stmt(updated_node, if_target, call)
 
        return None
 
    def leave_If(
        self, original_node: cst.If, updated_node: cst.If
    ) -> cst.BaseStatement:
        merged_stmt = self._compute_merged_stmt(updated_node)
        if merged_stmt is None:
            return updated_node

        parent = self.get_metadata(cst_meta.ParentNodeProvider, original_node)
        if isinstance(parent, cst.If) and parent.orelse is original_node:
            return cst.Else(body=cst.IndentedBlock(body=[merged_stmt]))
 
        return merged_stmt
    
# ---------------------------------------------------------------------------
# merge_duplicate_blocks
# ---------------------------------------------------------------------------

class _MergeDuplicateBlocksTransformer(cst.CSTTransformer):

    def leave_If(
        self, original_node: cst.If, updated_node: cst.If
    ) -> cst.BaseStatement:
        chain = refact_utils._get_if_chain(updated_node)

        if len(chain) < 2:
            return updated_node

        non_else = [(c, b) for c, b, is_else, orig in chain if not is_else]

        has_duplicates = any(
            refact_utils._bodies_equal(non_else[i][1], non_else[i + 1][1])
            for i in range(len(non_else) - 1)
        )

        if not has_duplicates:
            return updated_node

        return refact_utils._rebuild_if_chain(updated_node, chain)
    
# ---------------------------------------------------------------------------
# square_identity
# ---------------------------------------------------------------------------
class _SquareIdentityTransformer(cst.CSTTransformer):
    def leave_BinaryOperation(
        self, original_node: cst.BinaryOperation, updated_node: cst.BinaryOperation
    ) -> cst.BaseExpression:
        
        if not isinstance(updated_node.operator, cst.Multiply):
            return updated_node
        if not updated_node.left.deep_equals(updated_node.right):
            return updated_node
        if not refact_utils._is_safe_expr_min_max(updated_node.left):
            return updated_node

        left_node = updated_node.left
        
        if not left_node.lpar and isinstance(
            left_node, 
            (cst.UnaryOperation, cst.BinaryOperation, cst.BooleanOperation, 
             cst.Comparison, cst.IfExp, cst.Await)
        ):
            left_node = left_node.with_changes(
                lpar=[cst.LeftParen()], 
                rpar=[cst.RightParen()]
            )

        return cst.BinaryOperation(
            left=left_node,
            operator=cst.Power(
                whitespace_before=cst.SimpleWhitespace(""),
                whitespace_after=cst.SimpleWhitespace(""),
            ),
            right=cst.Integer("2"),
            lpar=updated_node.lpar,
            rpar=updated_node.rpar,
        )

# ---------------------------------------------------------------------------
# move_assign_in_block
# ---------------------------------------------------------------------------
class _MoveAssignInBlockTransformer(cst.CSTTransformer):

    def _process_stmts(
        self, stmts: tuple[cst.BaseStatement, ...]
    ) -> tuple[list[cst.BaseStatement], bool]:
        stmts = list(stmts)
        changed = False
        i = 0
        while i < len(stmts):
            stmt = stmts[i]
            var_name = refact_utils._match_simple_assign(stmt)
            if var_name is None or not refact_utils._is_literal_move(stmt.body[0].value):
                i += 1
                continue

            group_indices = refact_utils._collect_init_group(stmts, i)
            group_vars = {refact_utils._match_simple_assign(stmts[k]) for k in group_indices}

            first_use_indices = {}
            safe_to_move = True

            for k in group_indices:
                gvar = refact_utils._match_simple_assign(stmts[k])
                rhs_names = refact_utils._get_rhs_names(stmts[k])
                first_use = None
                for j in range(group_indices[-1] + 1, len(stmts)):
                    if refact_utils._stmt_assigns_any(stmts[j], {gvar} | rhs_names):
                        safe_to_move = False
                        break
                    if refact_utils._stmt_uses_name(stmts[j], gvar):
                        first_use = j
                        break
                if not safe_to_move:
                    break
                if first_use is None:
                    safe_to_move = False
                    break
                first_use_indices[gvar] = first_use

            if not safe_to_move:
                i += len(group_indices)
                continue

            target_idx = min(first_use_indices.values())
            if len(set(first_use_indices.values())) > 1:
                i += len(group_indices)
                continue

            if target_idx == group_indices[-1] + 1:
                i += len(group_indices)
                continue

            group_stmts = [stmts[k] for k in group_indices]
            for k in reversed(group_indices):
                stmts.pop(k)
            insert_at = target_idx - len(group_indices)
            for s in reversed(group_stmts):
                stmts.insert(insert_at, s)
            changed = True
            i += len(group_indices)

        return stmts, changed

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


# ---------------------------------------------------------------------------
# merge_else_if_into_elif
# ---------------------------------------------------------------------------

class _MergeElseIfIntoElifTransformer(cst.CSTTransformer):

    def leave_If(
        self, original_node: cst.If, updated_node: cst.If
    ) -> cst.If:
        if not isinstance(updated_node.orelse, cst.Else):
            return updated_node

        else_body = updated_node.orelse.body
        if not isinstance(else_body, cst.IndentedBlock):
            return updated_node
        if len(else_body.body) != 1:
            return updated_node

        inner = else_body.body[0]
        if not isinstance(inner, cst.If):
            return updated_node

        return updated_node.with_changes(orelse=inner)

# ---------------------------------------------------------------------------
# use_next
# ---------------------------------------------------------------------------
 
class _UseNextTransformer(cst.CSTTransformer):
 
    METADATA_DEPENDENCIES = (cst_meta.ParentNodeProvider,)
 
    def _process_stmts(self, stmts, block_parent):
        new_stmts = []
        changed = False
        i = 0
 
        while i < len(stmts):
            stmt = stmts[i]
            match = refact_utils._match_for_if_return(stmt)
 
            if match is not None:
                target, iter_, condition, return_expr = match
 
                default = None
                skip_next = False
                can_transform = False
 
                if i + 1 < len(stmts):
                    is_return, ret_val = refact_utils._match_trailing_return(stmts[i + 1])
                    if is_return:
                        skip_next = True
                        default = ret_val if ret_val is not None else cst.Name("None")
                        can_transform = True
                    else:
                        can_transform = False
                else:
                    if isinstance(block_parent, cst.FunctionDef):
                        default = cst.Name("None")
                        can_transform = True
                    else:
                        can_transform = False
 
                if not can_transform:
                    new_stmts.append(stmt)
                    i += 1
                    continue
 
                if isinstance(return_expr, cst.Tuple) and not return_expr.lpar:
                    return_expr = return_expr.with_changes(
                        lpar=[cst.LeftParen()], rpar=[cst.RightParen()]
                    )
                if isinstance(iter_, cst.Tuple) and not iter_.lpar:
                    iter_ = iter_.with_changes(
                        lpar=[cst.LeftParen()], rpar=[cst.RightParen()]
                    )
 
                comp_for = cst.CompFor(
                    target=target,
                    iter=iter_,
                    ifs=[cst.CompIf(test=condition, whitespace_before=cst.SimpleWhitespace(" "))],
                    whitespace_before=cst.SimpleWhitespace(" "),
                    whitespace_after_for=cst.SimpleWhitespace(" "),
                    whitespace_before_in=cst.SimpleWhitespace(" "),
                    whitespace_after_in=cst.SimpleWhitespace(" "),
                )
 
                genexp = cst.GeneratorExp(
                    elt=return_expr,
                    for_in=comp_for,
                    lpar=[cst.LeftParen()],
                    rpar=[cst.RightParen()],
                )
 
                args = [
                    cst.Arg(value=genexp, comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))),
                    cst.Arg(value=default, comma=cst.MaybeSentinel.DEFAULT),
                ]
 
                next_call = cst.Call(func=cst.Name("next"), args=args)
 
                new_return = cst.SimpleStatementLine(
                    body=[cst.Return(value=next_call, whitespace_after_return=cst.SimpleWhitespace(" "))],
                    leading_lines=stmt.leading_lines,
                )
 
                new_stmts.append(new_return)
                changed = True
                i += 2 if skip_next else 1
                continue
 
            new_stmts.append(stmt)
            i += 1
 
        return new_stmts, changed
 
    def leave_IndentedBlock(self, original_node, updated_node):
        parent = self.get_metadata(cst_meta.ParentNodeProvider, original_node)
        new_body, changed = self._process_stmts(updated_node.body, parent)
        return updated_node.with_changes(body=new_body) if changed else updated_node
 
    def leave_Module(self, original_node, updated_node):
        new_body, changed = self._process_stmts(updated_node.body, block_parent=None)
        return updated_node.with_changes(body=new_body) if changed else updated_node
 

# ---------------------------------------------------------------------------
# swap_if_else_branches
# ---------------------------------------------------------------------------
class _SwapIfElseBranchesTransformer(cst.CSTTransformer):

    def leave_If(
        self, original_node: cst.If, updated_node: cst.If
    ) -> cst.If:
        if not isinstance(updated_node.orelse, cst.Else):
            return updated_node

        if_body = updated_node.body
        else_body = updated_node.orelse.body

        is_pass = refact_utils._is_pass(if_body)
        is_if_guard = refact_utils._is_guard_body(if_body)
        is_else_guard = refact_utils._is_guard_body(else_body)

        should_swap = is_pass or (is_else_guard and not is_if_guard)

        if not should_swap:
            return updated_node

        negated = refact_utils._negate_condition(updated_node.test)

        current_whitespace = updated_node.whitespace_before_test
        
        if (not negated.lpar) and isinstance(current_whitespace, cst.SimpleWhitespace) and current_whitespace.value == "":
            new_whitespace = cst.SimpleWhitespace(" ")
        else:
            new_whitespace = current_whitespace

        return updated_node.with_changes(
            test=negated,
            whitespace_before_test=new_whitespace,
            body=else_body,
            orelse=updated_node.orelse.with_changes(body=if_body),
        )

# ---------------------------------------------------------------------------
# invert_any_all
# ---------------------------------------------------------------------------

class _InvertAnyAllTransformer(cst.CSTTransformer):

    def _invert_node(self, node: cst.UnaryOperation) -> cst.BaseExpression:
        if not isinstance(node.operator, cst.Not):
            return node

        expr = node.expression
        if not isinstance(expr, cst.Call):
            return node
        if not isinstance(expr.func, cst.Name):
            return node
        if expr.func.value not in ("any", "all"):
            return node
        if len(expr.args) != 1 or expr.args[0].keyword is not None:
            return node

        arg = expr.args[0].value
        if not isinstance(arg, cst.GeneratorExp):
            return node

        if arg.for_in.inner_for_in is not None:
            return node

        inverse_fn = "all" if expr.func.value == "any" else "any"

        negated_elt = refact_utils._negate_condition_any_all(arg.elt)

        if isinstance(negated_elt, cst.UnaryOperation) and isinstance(negated_elt.operator, cst.Not):
            negated_elt = self._invert_node(negated_elt)

        new_gen = arg.with_changes(elt=negated_elt)

        return expr.with_changes(
            func=cst.Name(inverse_fn),
            args=[expr.args[0].with_changes(value=new_gen)],
            lpar=node.lpar,
            rpar=node.rpar,
        )

    def leave_UnaryOperation(
        self, original_node: cst.UnaryOperation, updated_node: cst.UnaryOperation
    ) -> cst.BaseExpression:
        return self._invert_node(updated_node)

# ---------------------------------------------------------------------------
# remove_redundant_if
# ---------------------------------------------------------------------------
class _RemoveRedundantIfTransformer(cst.CSTTransformer):
    def leave_If(
        self, original_node: cst.If, updated_node: cst.If
    ) -> cst.If:
        if not isinstance(updated_node.orelse, cst.If):
            return updated_node

        elif_node = updated_node.orelse

        if elif_node.orelse is not None:
            return updated_node

        if not refact_utils._are_negations(updated_node.test, elif_node.test):
            return updated_node

        if not refact_utils._is_safe_expr_remove_red(updated_node.test):
            return updated_node

        new_else = cst.Else(
            body=elif_node.body,
            leading_lines=elif_node.leading_lines,
        )
        return updated_node.with_changes(orelse=new_else)


# ---------------------------------------------------------------------------
# use_join
# ---------------------------------------------------------------------------
class _UseJoinTransformer(cst.CSTTransformer):

    def _process_stmts(
        self, stmts: tuple[cst.BaseStatement, ...]
    ) -> tuple[list[cst.BaseStatement], bool]:
        new_stmts: list[cst.BaseStatement] = []
        changed = False
        i = 0
        
        while i < len(stmts):
            stmt = stmts[i]
            var_name = refact_utils._match_empty_string_init(stmt)
            
            if var_name and i + 1 < len(stmts):
                next_stmt = stmts[i + 1]
                if (
                    isinstance(next_stmt, cst.For)
                    and next_stmt.orelse is None
                    and isinstance(next_stmt.body, cst.IndentedBlock)
                ):
                    match = refact_utils._match_loop_body_use_join(next_stmt.body.body, var_name)
                    
                    if match is not None:
                        aug_expr, condition = match

                        if (
                            not refact_utils._is_reassigned_in_body_use_join(next_stmt.body.body, var_name, include_aug=False)
                            and not refact_utils._expr_references_name_use_join(aug_expr, var_name)
                        ):
                            comp_for = cst.CompFor(
                                target=next_stmt.target,
                                iter=next_stmt.iter,
                                ifs=[cst.CompIf(
                                    test=condition,
                                    whitespace_before=cst.SimpleWhitespace(" "),
                                )] if condition is not None else [],
                                whitespace_before=cst.SimpleWhitespace(" "),
                                whitespace_after_for=cst.SimpleWhitespace(" "),
                                whitespace_before_in=cst.SimpleWhitespace(" "),
                                whitespace_after_in=cst.SimpleWhitespace(" "),
                            )

                            join_call = cst.Call(
                                func=cst.Attribute(
                                    value=stmt.body[0].value,
                                    attr=cst.Name("join"),
                                ),
                                args=[cst.Arg(value=cst.GeneratorExp(
                                    elt=aug_expr,
                                    for_in=comp_for,
                                ))],
                            )
                            
                            new_assign = stmt.body[0].with_changes(value=join_call)
                            new_stmts.append(stmt.with_changes(body=[new_assign]))
                            changed = True
                            i += 2 
                            continue
                            
            new_stmts.append(stmt)
            i += 1
            
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

# ---------------------------------------------------------------------------
# for_append_to_extend
# ---------------------------------------------------------------------------
class _ForAppendToExtendTransformer(cst.CSTTransformer):

    def _process_stmts(
        self, stmts: tuple[cst.BaseStatement, ...]
    ) -> tuple[list[cst.BaseStatement], bool]:
        new_stmts = []
        changed = False
        
        for stmt in stmts:
            match = refact_utils._match_for_append(stmt)
            
            if match is not None:
                list_node, append_expr, condition = match

                comp_for = cst.CompFor(
                    target=stmt.target,
                    iter=stmt.iter,
                    ifs=[cst.CompIf(
                        test=condition,
                        whitespace_before=cst.SimpleWhitespace(" "),
                    )] if condition is not None else [],
                    whitespace_before=cst.SimpleWhitespace(" "),
                    whitespace_after_for=cst.SimpleWhitespace(" "),
                    whitespace_before_in=cst.SimpleWhitespace(" "),
                    whitespace_after_in=cst.SimpleWhitespace(" "),
                )
                
                extend_call = cst.Call(
                    func=cst.Attribute(
                        value=list_node,
                        attr=cst.Name("extend"),
                        dot=cst.Dot(),
                    ),
                    args=[cst.Arg(value=cst.GeneratorExp(
                        elt=append_expr,
                        for_in=comp_for,
                    ))],
                )
                
                new_stmts.append(cst.SimpleStatementLine(
                    body=[cst.Expr(value=extend_call)],
                    leading_lines=stmt.leading_lines,
                ))
                changed = True
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


# ---------------------------------------------------------------------------
# hoist_similar_statement_from_if
# ---------------------------------------------------------------------------
class _HoistSimilarStatementFromIfTransformer(cst.CSTTransformer):

    def leave_If(
        self, original_node: cst.If, updated_node: cst.If
    ) -> cst.CSTNode | cst.FlattenSentinel:

        if not isinstance(updated_node.orelse, cst.Else):
            return updated_node

        if_stmts = refact_utils._get_indented_stmts(updated_node.body)
        else_stmts = refact_utils._get_indented_stmts(updated_node.orelse.body)

        if if_stmts is None or else_stmts is None:
            return updated_node

        hoisted_before = []
        hoisted_after = []

        while if_stmts and else_stmts and if_stmts[0].deep_equals(else_stmts[0]):
            hoisted_before.append(if_stmts.pop(0))
            else_stmts.pop(0)

        while if_stmts and else_stmts and if_stmts[-1].deep_equals(else_stmts[-1]):
            hoisted_after.insert(0, if_stmts.pop())
            else_stmts.pop()

        if not hoisted_before and not hoisted_after:
            return updated_node

        new_if_body = updated_node.body.with_changes(
            body=if_stmts if if_stmts else [refact_utils._make_pass_node()]
        )
 
        if else_stmts:
            new_else = updated_node.orelse.with_changes(
                body=updated_node.orelse.body.with_changes(body=else_stmts)
            )
        else:
            new_else = None

        new_if = updated_node.with_changes(
            body=new_if_body,
            orelse=new_else
        )

        return cst.FlattenSentinel(
            hoisted_before + [new_if] + hoisted_after
        )

# ---------------------------------------------------------------------------
# identity_comprehension
# ---------------------------------------------------------------------------

class _IdentityComprehensionTransformer(cst.CSTTransformer):

    def leave_ListComp(
        self, original_node: cst.ListComp, updated_node: cst.ListComp
    ) -> cst.BaseExpression:
        comp_for = updated_node.for_in

        if comp_for.asynchronous is not None or comp_for.ifs or comp_for.inner_for_in is not None:
            return updated_node
            
        if not refact_utils._is_identity_comp(comp_for.target, updated_node.elt):
            return updated_node

        return cst.Call(
            func=cst.Name("list"),
            args=[cst.Arg(value=cst.Call(
                func=cst.Name("iter"),
                args=[cst.Arg(value=comp_for.iter)],
            ))],
        )

    def leave_SetComp(
        self, original_node: cst.SetComp, updated_node: cst.SetComp
    ) -> cst.BaseExpression:
        comp_for = updated_node.for_in

        if comp_for.asynchronous is not None or comp_for.ifs or comp_for.inner_for_in is not None:
            return updated_node
            
        if not refact_utils._is_identity_comp(comp_for.target, updated_node.elt):
            return updated_node
            
        return cst.Call(
            func=cst.Name("set"),
            args=[cst.Arg(value=comp_for.iter)],
        )

    def leave_DictComp(
        self, original_node: cst.DictComp, updated_node: cst.DictComp
    ) -> cst.BaseExpression:
        comp_for = updated_node.for_in

        if comp_for.asynchronous is not None or comp_for.ifs or comp_for.inner_for_in is not None:
            return updated_node

        if not refact_utils._is_kv_identity_comp(comp_for.target, updated_node.key, updated_node.value):
            return updated_node

        iter_node = refact_utils._is_items_call(comp_for.iter) or comp_for.iter

        return cst.Call(
            func=cst.Name("dict"),
            args=[cst.Arg(value=iter_node)],
        )


    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> cst.BaseExpression:
        if not isinstance(updated_node.func, cst.Name):
            return updated_node
        if updated_node.func.value != "dict":
            return updated_node
        if len(updated_node.args) != 1 or updated_node.args[0].keyword is not None:
            return updated_node
            
        obj = refact_utils._is_items_call(updated_node.args[0].value)
        if obj is None:
            return updated_node
            
        return updated_node.with_changes(
            args=[updated_node.args[0].with_changes(value=obj)]
        )

# ---------------------------------------------------------------------------
# merge_list_appends_into_extend
# ---------------------------------------------------------------------------
class _MergeListAppendsIntoExtendTransformer(cst.CSTTransformer):

    def _process_stmts(
        self, stmts: tuple[cst.BaseStatement, ...]
    ) -> tuple[list[cst.BaseStatement], bool]:
        new_stmts = []
        changed = False
        i = 0
        
        while i < len(stmts):
            stmt = stmts[i]
            match = refact_utils._match_append_or_extend(stmt)
            
            if match is None:
                new_stmts.append(stmt)
                i += 1
                continue

            list_name, elements = match
            j = i + 1

            while j < len(stmts):
                next_match = refact_utils._match_append_or_extend(stmts[j])
                if next_match is None or next_match[0] != list_name:
                    break

                has_state_dependency = any(
                    refact_utils._expr_references_name_merge(el, list_name) for el in next_match[1]
                )
                if has_state_dependency:
                    break 
                    
                elements.extend(next_match[1])
                j += 1

            if j == i + 1:
                new_stmts.append(stmt)
                i += 1
                continue

            changed = True

            cst_elements = [
                cst.Element(
                    value=el,
                    comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
                    if k < len(elements) - 1
                    else cst.MaybeSentinel.DEFAULT,
                )
                for k, el in enumerate(elements)
            ]
            
            extend_call = cst.Call(
                func=cst.Attribute(
                    value=cst.Name(list_name),
                    attr=cst.Name("extend"),
                    dot=cst.Dot(),
                ),
                args=[cst.Arg(value=cst.List(elements=cst_elements))],
            )
            
            new_stmts.append(stmt.with_changes(
                body=[cst.Expr(value=extend_call)]
            ))
            i = j

        return new_stmts, changed

    def leave_IndentedBlock(self, original_node, updated_node):
        new_body, changed = self._process_stmts(updated_node.body)
        return updated_node.with_changes(body=new_body) if changed else updated_node

    def leave_Module(self, original_node, updated_node):
        new_body, changed = self._process_stmts(updated_node.body)
        return updated_node.with_changes(body=new_body) if changed else updated_node


# ---------------------------------------------------------------------------
# use_dict_items
# ---------------------------------------------------------------------------

class _UseDictItemsTransformer(cst.CSTTransformer):

    def leave_For(
        self, original_node: cst.For, updated_node: cst.For
    ) -> cst.For:
        if not isinstance(updated_node.target, cst.Name):
            return updated_node

        if not isinstance(updated_node.iter, cst.Name):
            return updated_node

        if not isinstance(updated_node.body, cst.IndentedBlock):
            return updated_node

        key_name = updated_node.target.value
        dict_name = updated_node.iter.value
        body_stmts = list(updated_node.body.body)

        if not body_stmts:
            return updated_node

        first = body_stmts[0]
        if not isinstance(first, cst.SimpleStatementLine) or len(first.body) != 1:
            return updated_node
            
        assign = first.body[0]
        if not isinstance(assign, cst.Assign) or len(assign.targets) != 1:
            return updated_node
            
        value_target = assign.targets[0].target
        if not isinstance(value_target, cst.Name):
            return updated_node

        rhs = assign.value
        if not isinstance(rhs, cst.Subscript):
            return updated_node
        if not isinstance(rhs.value, cst.Name) or rhs.value.value != dict_name:
            return updated_node
        if len(rhs.slice) != 1:
            return updated_node
            
        subscript_slice = rhs.slice[0].slice
        if not isinstance(subscript_slice, cst.Index):
            return updated_node
        if not isinstance(subscript_slice.value, cst.Name):
            return updated_node
        if subscript_slice.value.value != key_name:
            return updated_node

        value_name = value_target.value

        if not body_stmts[1:]:
            return updated_node
        
        if refact_utils._is_reassigned_in_body_dict(body_stmts[1:], key_name):
            return updated_node

        new_target = cst.Tuple(
            elements=[
                cst.Element(
                    value=cst.Name(key_name),
                    comma=cst.Comma(whitespace_after=cst.SimpleWhitespace(" ")),
                ),
                cst.Element(value=cst.Name(value_name)),
            ]
        )

        new_iter = cst.Call(
            func=cst.Attribute(
                value=cst.Name(dict_name),
                attr=cst.Name("items"),
                dot=cst.Dot(),
            ),
            args=[],
        )

        return updated_node.with_changes(
            target=new_target,
            iter=new_iter,
            body=updated_node.body.with_changes(body=body_stmts[1:]),
        )

# ---------------------------------------------------------------------------
# remove_redundant_slice_index
# ---------------------------------------------------------------------------

class _RemoveRedundantSliceIndexTransformer(cst.CSTTransformer):
    def leave_Subscript(
        self, original_node: cst.Subscript, updated_node: cst.Subscript
    ) -> cst.Subscript:
        if len(updated_node.slice) != 1:
            return updated_node
            
        slice_element = updated_node.slice[0]
        if not isinstance(slice_element.slice, cst.Slice):
            return updated_node

        slc = slice_element.slice
        sliced = updated_node.value

        lower = slc.lower
        upper = slc.upper

        lower_is_redundant = (
            lower is None
            or refact_utils._is_zero(lower)
            or refact_utils._is_none(lower)
        )
        upper_is_redundant = (
            upper is None
            or refact_utils._is_none(upper)
            or refact_utils._is_len_of(upper, sliced)
        )

        if not lower_is_redundant and not upper_is_redundant:
            return updated_node

        new_lower = None if lower_is_redundant else lower
        new_upper = None if upper_is_redundant else upper

        new_slice = slc.with_changes(lower=new_lower, upper=new_upper)
        return updated_node.with_changes(
            slice=[slice_element.with_changes(slice=new_slice)]
        )

# ---------------------------------------------------------------------------
# assign_if_exp
# ---------------------------------------------------------------------------
class _AssignIfExpTransformer(cst.CSTTransformer):
 
    METADATA_DEPENDENCIES = (cst_meta.ParentNodeProvider,)
 
    def leave_If(
        self, original_node: cst.If, updated_node: cst.If
    ) -> cst.CSTNode:

        if not isinstance(updated_node.orelse, cst.Else):
            return updated_node
 
        if_stmts = refact_utils._get_indented_stmts_assign(updated_node.body)
        else_stmts = refact_utils._get_indented_stmts_assign(updated_node.orelse.body)
 
        if not if_stmts or not else_stmts or len(if_stmts) != 1 or len(else_stmts) != 1:
            return updated_node
 
        if_assign = refact_utils._match_single_assign_if_exp(if_stmts[0])
        else_assign = refact_utils._match_single_assign_if_exp(else_stmts[0])
 
        if not if_assign or not else_assign:
            return updated_node

        if_target, if_value, if_stmt = if_assign
        else_target, else_value, else_stmt = else_assign
 
        if not if_target.deep_equals(else_target):
            return updated_node

        if_value = refact_utils._ensure_parens_for_tuple(if_value)
        else_value = refact_utils._ensure_parens_for_tuple(else_value)
 
        if_exp = cst.IfExp(
            test=updated_node.test,
            body=if_value,
            orelse=else_value,
            whitespace_before_if=cst.SimpleWhitespace(" "),
            whitespace_after_if=cst.SimpleWhitespace(" "),
            whitespace_before_else=cst.SimpleWhitespace(" "),
            whitespace_after_else=cst.SimpleWhitespace(" "),
        )

        comment = if_stmt.trailing_whitespace.comment or else_stmt.trailing_whitespace.comment
 
        merged_stmt = cst.SimpleStatementLine(
            body=[cst.Assign(
                targets=[cst.AssignTarget(target=if_target)],
                value=if_exp,
            )],
            trailing_whitespace=cst.TrailingWhitespace(
                whitespace=cst.SimpleWhitespace("  ") if comment else cst.SimpleWhitespace(""),
                comment=comment,
                newline=cst.Newline(),
            ),
        )
 
        parent = self.get_metadata(cst_meta.ParentNodeProvider, original_node)
        if refact_utils._is_elif_of_parent(original_node, parent):
            return cst.Else(
                body=cst.IndentedBlock(body=[merged_stmt]),
                leading_lines=updated_node.leading_lines,
            )

 
        return merged_stmt.with_changes(leading_lines=updated_node.leading_lines)
 
    def leave_Module(self, original_node, updated_node):
        return updated_node