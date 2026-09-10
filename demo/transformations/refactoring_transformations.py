import libcst as cst
from pathlib import Path

from refact_transform_utils import apply_to_file, deshadow_function_name
import refact_transformer_classes as transf


def remove_unnecessary_else(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._RemoveUnnecessaryElseTransformer()).code, 
        execute=execute,
        content=content
    )
    

def hoist_statement_from_loop(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._HoistStatementFromLoopTransformer()).code, 
        execute=execute,
        content=content
    )
    

def boolean_if_exp_identity(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._BooleanIfExpIdentityTransformer()).code, 
        execute=execute,
        content=content
    )


def list_comprehension(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._ListComprehensionTransformer()).code, 
        execute=execute,
        content=content
    )
    

def remove_unnecessary_cast(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._RemoveUnnecessaryCastTransformer()).code, 
        execute=execute,
        content=content
    )
    

def merge_comparisons(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._MergeComparisonsTransformer()).code, 
        execute=execute,
        content=content
    )
    

def sum_comprehension(path: str, execute=False, content=None) -> tuple[bool, str]:
    def _run(src: str) -> str:
        module = cst.parse_module(src)
        module = deshadow_function_name(module, "sum") 
        module = module.visit(transf._SumComprehensionTransformer()) 
        return module.code

    return apply_to_file(
        Path(path),
        _run,
        execute=execute,
        content=content
    )
    

def merge_list_append(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._MergeListAppendTransformer()).code, 
        execute=execute,
        content=content
    )
    
def merge_nested_ifs(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._MergeNestedIfsTransformer()).code, 
        execute=execute,
        content=content
    )
    
def inline_immediately_returned_variable(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._InlineImmediatelyReturnedVariableTransformer()).code, 
        execute=execute,
        content=content
    )
    
def for_index_underscore(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._ForIndexUnderscoreTransformer()).code, 
        execute=execute,
        content=content
    )
    
def simplify_constant_sum(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._SimplifyConstantSumTransformer()).code, 
        execute=execute,
        content=content
    )
    
def simplify_len_comparison(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._SimplifyLenComparisonTransformer()).code, 
        execute=execute,
        content=content
    )
    
def comprehension_to_generator(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._ComprehensionToGeneratorTransformer()).code, 
        execute=execute,
        content=content
    )
    
def use_any(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._UseAnyTransformer()).code, 
        execute=execute,
        content=content
    )
    
def min_max_identity(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.MetadataWrapper(cst.parse_module(src)).visit(transf._MinMaxIdentityTransformer()).code,
        execute=execute,
        content=content
    )
    
def merge_duplicate_blocks(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._MergeDuplicateBlocksTransformer()).code, 
        execute=execute,
        content=content
    )
    
def square_identity(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._SquareIdentityTransformer()).code, 
        execute=execute,
        content=content
    )

def move_assign_in_block(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._MoveAssignInBlockTransformer()).code, 
        execute=execute,
        content=content
    )
    
def merge_else_if_into_elif(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._MergeElseIfIntoElifTransformer()).code, 
        execute=execute,
        content=content
    )
    
def use_next(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.MetadataWrapper(cst.parse_module(src)).visit(transf._UseNextTransformer()).code,
        execute=execute,
        content=content
    )
    
def swap_if_else_branches(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._SwapIfElseBranchesTransformer()).code, 
        execute=execute,
        content=content
    )
    
def switch(path: str, execute=False, content=None) -> tuple[bool, str]:
    changed1, content = merge_duplicate_blocks(path, execute=execute, content=content)
    changed2, content = merge_comparisons(path, execute=execute, content=content)
    return changed1 or changed2, content
    
def invert_any_all(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._InvertAnyAllTransformer()).code, 
        execute=execute,
        content=content
    )
    
def remove_redundant_if(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._RemoveRedundantIfTransformer()).code, 
        execute=execute,
        content=content
    )
    
def use_join(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._UseJoinTransformer()).code, 
        execute=execute,
        content=content
    )

def for_append_to_extend(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._ForAppendToExtendTransformer()).code, 
        execute=execute,
        content=content
    )

def hoist_similar_statement_from_if(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._HoistSimilarStatementFromIfTransformer()).code, 
        execute=execute,
        content=content
    )
    
def identity_comprehension(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._IdentityComprehensionTransformer()).code, 
        execute=execute,
        content=content
    )
    
def merge_list_appends_into_extend(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._MergeListAppendsIntoExtendTransformer()).code, 
        execute=execute,
        content=content
    )

def use_dict_items(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._UseDictItemsTransformer()).code, 
        execute=execute,
        content=content
    )
    
def remove_redundant_slice_index(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.parse_module(src).visit(transf._RemoveRedundantSliceIndexTransformer()).code, 
        execute=execute,
        content=content
    )
    
def assign_if_exp(path: str, execute=False, content=None) -> tuple[bool, str]:
    return apply_to_file(
        Path(path),
        lambda src: cst.MetadataWrapper(cst.parse_module(src)).visit(transf._AssignIfExpTransformer()).code,
        execute=execute,
        content=content
    )

