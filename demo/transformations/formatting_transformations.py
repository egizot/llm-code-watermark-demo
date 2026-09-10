import ast
import autopep8
import chardet
import tokenize
import io

AUTOPEP8_GROUPS = {
    40 : ["E225", "E226", "E227", "E228", "E241", "E242", "E251", "E252", "E231"], # spacing
    41 : ["E27"], # keywords
    42 : ["E123"], # brackets
    43 : ["W292"], # eof
    44 : ["E301", "E302", "E305", "E306"], # blank_lines
}

def run_autopep8(file_path, selected_groups=None, execute=False, content=None) -> tuple[bool, str]:
    if selected_groups is None:
        selected_groups = list(AUTOPEP8_GROUPS.keys())
        
    try:
        if content is not None:
            code = content
        else:
            with open(file_path, 'r', encoding='utf-8', newline='') as f:
                code = f.read()
                
        original_code = code 
        changed = False

        for group_id in selected_groups:
            if group_id in AUTOPEP8_GROUPS:
                group_rules = AUTOPEP8_GROUPS[group_id]
                new_code = autopep8.fix_code(code, options={'select': group_rules})
                if new_code != code:
                    changed = True
                    if execute:
                        code = new_code
            else:
                print(f"Warning: Group '{group_id}' does not exist. It will be ignored.")

        if changed:
            try:
                ast.parse(code)
            except SyntaxError as e:
                filename = file_path.name if hasattr(file_path, 'name') else str(file_path)
                print(f"Warning: autopep8 on {filename} would generate invalid code ({e}). Changes discarded.")
                return False, original_code
            
        if execute and changed:
            with open(file_path, 'w', encoding='utf-8', newline='') as f:
                f.write(code)

        return changed, code

    except Exception as e:
        print(f"Error running autopep8 on {file_path}: {str(e)}")
        return False, content or ""
    

def detect_encoding(file_path):
    with open(file_path, 'rb') as file:
        raw_data = file.read()
    result = chardet.detect(raw_data)
    return result['encoding']

def read_file_with_auto_encoding(file_path):
    encodings_to_try = ['utf-8']
    detected_encoding = detect_encoding(file_path)
    if detected_encoding:
        encodings_to_try.insert(0, detected_encoding)
    
    for encoding in encodings_to_try:
        try:
            with open(file_path, 'r', encoding=encoding) as file:
                return file.readlines(), encoding
        except UnicodeDecodeError:
            continue
    
    raise ValueError(f"Unable to determine the correct encoding for {file_path}")


def get_protected_lines(source: str) -> set[int]:
    protected = set()
    bracket_depth = 0
    bracket_open_line = None

    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    
    for tok in tokens:
        tok_type, tok_str, start, end, _ = tok

        if tok_str in ('(', '[', '{'):
            if bracket_depth == 0:
                bracket_open_line = start[0]
            bracket_depth += 1
        elif tok_str in (')', ']', '}'):
            bracket_depth -= 1
            if bracket_depth == 0:
                bracket_open_line = None

        if tok_type == tokenize.STRING:
            start_line, end_line = start[0], end[0]
            if end_line > start_line:
                for lineno in range(start_line + 1, end_line + 1):
                    protected.add(lineno)

        if bracket_depth > 0 and bracket_open_line is not None and start[0] > bracket_open_line:
            protected.add(start[0])

    return protected


def tab_formatting(file_path, spaces_per_tab=4, execute=False, content=None) -> tuple[bool, str]:
    try:
        if content is not None:
            lines = content.splitlines(keepends=True)
            encoding = 'utf-8'
        else:
            lines, encoding = read_file_with_auto_encoding(file_path)

        source = ''.join(lines)
        protected_lines = get_protected_lines(source)

        indent_stack = [0]
        new_lines = []
        is_continuation = False 

        for i, line in enumerate(lines):
            lineno = i + 1 

            currently_in_continuation = is_continuation
            
            stripped_line = line.rstrip('\r\n ')
            is_continuation = stripped_line.endswith('\\')
            
            if lineno in protected_lines or currently_in_continuation:
                new_lines.append(line)
                continue

            stripped = line.lstrip(' ')
            spaces = len(line) - len(stripped)

            if not stripped.strip():
                new_lines.append(line)
                continue

            if spaces > indent_stack[-1]:
                indent_stack.append(spaces)
            elif spaces < indent_stack[-1]:
                while len(indent_stack) > 1 and indent_stack[-1] > spaces:
                    indent_stack.pop()
                if indent_stack[-1] != spaces:
                    print(f"Warning: misaligned indentation ({spaces} spaces) in: {line.rstrip()}")

            level = len(indent_stack) - 1
            new_line = '\t' * level + stripped
            new_lines.append(new_line)

        changed = lines != new_lines
        result = ''.join(new_lines)

        if changed:
            try:
                ast.parse(result)
            except SyntaxError as e:
                print(f"Warning: tab_formatting would generate invalid code ({e}). Changes discarded.")
                return False, content if content is not None else source

        if execute and changed:
            with open(file_path, 'w', encoding=encoding) as f:
                f.writelines(new_lines)

        return changed, result

    except Exception as e:
        print(f"Error processing file {file_path}: {e}")
        return False, content if content is not None else ""