from functools import partial

import refactoring_transformations as refact
import reordering_transformations as reorder
import formatting_transformations as formatting
import my_transf as my


TRANSFORMATIONS = {
    # REFACTORING
    1:refact.simplify_len_comparison, 
    2:refact.remove_redundant_slice_index, 
    3:refact.invert_any_all, 
    4:refact.simplify_constant_sum, 
    5:refact.remove_unnecessary_cast, 
    6:refact.merge_comparisons, 
    7:refact.square_identity, 
    8:refact.boolean_if_exp_identity, 
    9:refact.merge_else_if_into_elif, 
    10:refact.remove_redundant_if, 
    11:refact.merge_duplicate_blocks, 
    12:refact.hoist_statement_from_loop,
    13:refact.swap_if_else_branches, 
    14:refact.hoist_similar_statement_from_if, 
    15:refact.assign_if_exp,
    16:refact.remove_unnecessary_else, 
    17:refact.merge_nested_ifs, 
    18:refact.switch, 
    19:refact.move_assign_in_block, 
    20:refact.for_index_underscore, 
    21:refact.use_dict_items,
    22:refact.for_append_to_extend, 
    23:refact.list_comprehension, 
    24:refact.sum_comprehension, 
    25:refact.min_max_identity, 
    26:refact.use_any, 
    27:refact.use_next, 
    28:refact.use_join, 
    29:refact.merge_list_append, 
    30:refact.merge_list_appends_into_extend,
    31:refact.identity_comprehension, 
    32:refact.comprehension_to_generator, 
    33:refact.inline_immediately_returned_variable,
    # NEW TRANSFORMATION
    34: my.list_to_for_append, 
    35: my.split_tuple_assignment, 
    36: my.split_chained_comparison, 
    37: my.in_place_to_pure_method,
    38: my.range_len_to_enumerate, 
    39: my.method_call_to_type_call,
    # FORMATTING
    40: partial(formatting.run_autopep8, selected_groups=[40]), 
    41: partial(formatting.run_autopep8, selected_groups=[41]),
    42: partial(formatting.run_autopep8, selected_groups=[42]), 
    43: partial(formatting.run_autopep8, selected_groups=[43]),
    44: partial(formatting.run_autopep8, selected_groups=[44]), 
    45: formatting.tab_formatting,
    # REORDERING
    46: reorder.transform_operations_add, 
    47: reorder.transform_operations_mult,
    48: reorder.transform_comparison_LG, 
    49: reorder.transform_comparison_LGE,
}