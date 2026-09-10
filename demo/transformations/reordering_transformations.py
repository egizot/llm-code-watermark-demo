import ast
import libcst as cst
import hashlib
import os

def get_node_source(node: cst.CSTNode) -> str:
    return cst.Module([]).code_for_node(node)


def get_node_hash(node: cst.CSTNode) -> str:
    source = get_node_source(node).strip()
    return hashlib.sha256(source.encode()).hexdigest()


class RiskyDescendantVisitor(cst.CSTVisitor):
    def __init__(self):
        super().__init__()
        self.has_risky = False

    def visit_BinaryOperation(self, node: cst.BinaryOperation) -> bool:
        if self.has_risky:
            return False

        if isinstance(node.operator, (cst.Add, cst.Multiply)):
            self.has_risky = True
            return False

        return True



def has_complex_operand(node: cst.CSTNode) -> bool:
    visitor = RiskyDescendantVisitor()
    node.visit(visitor)
    return visitor.has_risky


class AddSwapTransformer(cst.CSTTransformer):
    def leave_BinaryOperation(self, original_node: cst.BinaryOperation,
                              updated_node: cst.BinaryOperation) -> cst.BinaryOperation:
        if isinstance(updated_node.operator, cst.Add):
            if has_complex_operand(updated_node.left) or has_complex_operand(updated_node.right):
                return updated_node
            if isinstance(updated_node.left, cst.BinaryOperation):
                if isinstance(updated_node.left.operator, cst.Subtract):
                    return updated_node
            try:
                hash_left = get_node_hash(updated_node.left)
                hash_right = get_node_hash(updated_node.right)
            except Exception:
                return updated_node

            if hash_left < hash_right:
                return updated_node.with_changes(
                    left=updated_node.right,
                    right=updated_node.left
                )
        return updated_node


class MultSwapTransformer(cst.CSTTransformer):
    def leave_BinaryOperation(self, original_node: cst.BinaryOperation,
                              updated_node: cst.BinaryOperation) -> cst.BinaryOperation:
        if isinstance(updated_node.operator, cst.Multiply):
            if has_complex_operand(updated_node.left) or has_complex_operand(updated_node.right):
                return updated_node

            try:
                hash_left = get_node_hash(updated_node.left)
                hash_right = get_node_hash(updated_node.right)
            except Exception:
                return updated_node

            if hash_left < hash_right:
                return updated_node.with_changes(
                    left=updated_node.right,
                    right=updated_node.left
                )
        return updated_node


class GtLtSwapTransformer(cst.CSTTransformer):
    def leave_Comparison(self, original_node: cst.Comparison, updated_node: cst.Comparison) -> cst.Comparison:
        if len(updated_node.comparisons) != 1:
            return updated_node

        target = updated_node.comparisons[0]
        op = target.operator
        comparator = target.comparator
        left = updated_node.left

        if isinstance(op, (cst.GreaterThan, cst.LessThan)):
            if has_complex_operand(left) or has_complex_operand(comparator):
                return updated_node

            try:
                hash_left = get_node_hash(left)
                hash_right = get_node_hash(comparator)
            except Exception:
                return updated_node

            if hash_left < hash_right:
                new_op = None
                if isinstance(op, cst.GreaterThan):
                    new_op = cst.LessThan()
                elif isinstance(op, cst.LessThan):
                    new_op = cst.GreaterThan()

                new_target = target.with_changes(operator=new_op, comparator=left)
                return updated_node.with_changes(left=comparator, comparisons=[new_target])

        return updated_node


class GtELtESwapTransformer(cst.CSTTransformer):
    def leave_Comparison(self, original_node: cst.Comparison, updated_node: cst.Comparison):
        if len(updated_node.comparisons) != 1:
            return updated_node

        target = updated_node.comparisons[0]
        op = target.operator
        comparator = target.comparator
        left = updated_node.left

        if isinstance(op, (cst.GreaterThanEqual, cst.LessThanEqual)):
            if has_complex_operand(left) or has_complex_operand(comparator):
                return updated_node

            try:
                hash_left = get_node_hash(left)
                hash_right = get_node_hash(comparator)
            except Exception:
                return updated_node

            if hash_left < hash_right:
                new_op = None
                if isinstance(op, cst.GreaterThanEqual):
                    new_op = cst.LessThanEqual()
                elif isinstance(op, cst.LessThanEqual):
                    new_op = cst.GreaterThanEqual()

                new_target = target.with_changes(operator=new_op, comparator=left)
                return updated_node.with_changes(left=comparator, comparisons=[new_target])

        return updated_node



def _apply_cst_transformer(file_path, transformer_class, execute=False, content=None) -> tuple[bool, str]:
    if content is None:
        if not os.path.exists(file_path):
            return False, ""
        with open(file_path, 'r', encoding='utf-8') as f:
            source_code = f.read()
    else:
        source_code = content

    try:
        module = cst.parse_module(source_code)
    except Exception as e:
        print(f"Warning: LibCST initial parsing failure on {file_path}: {e}")
        return False, source_code
    try:
        transformer = transformer_class()
        new_module = module.visit(transformer)
        result_code = new_module.code
    except Exception as e:
        print(f"Warning: Exception in transformer {transformer_class.__name__} for {file_path}: {e}")
        return False, source_code

    changed = result_code != source_code

    if changed:
        try:
            ast.parse(result_code)
        except SyntaxError as e:
            filename = file_path.name if hasattr(file_path, 'name') else str(file_path)
            print(f"Warning: Transformer {transformer_class.__name__} on {filename} would generate invalid code ({e}). Changes discarded.")
            return False, source_code

    if execute and changed:
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(result_code)
        except Exception as e:
            print(f"Warning: Failed to write '{file_path}': {e}. Changes not saved.")
            return False, source_code

    return changed, result_code


def transform_operations_add(file_path, execute=False, content=None) -> tuple[bool, str]:
    return _apply_cst_transformer(file_path, AddSwapTransformer, execute=execute, content=content)

def transform_operations_mult(file_path, execute=False, content=None) -> tuple[bool, str]:
    return _apply_cst_transformer(file_path, MultSwapTransformer, execute=execute, content=content)

def transform_comparison_LG(file_path, execute=False, content=None) -> tuple[bool, str]:
    return _apply_cst_transformer(file_path, GtLtSwapTransformer, execute=execute, content=content)

def transform_comparison_LGE(file_path, execute=False, content=None) -> tuple[bool, str]:
    return _apply_cst_transformer(file_path, GtELtESwapTransformer, execute=execute, content=content)