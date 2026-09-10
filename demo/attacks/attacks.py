import ast
import io
import keyword
import os
import shutil
import subprocess
import tokenize
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUFF_CONFIG = os.path.join(SCRIPT_DIR, "ruff.toml")

def copy_folder_force(src, dst):
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"Copy {src} to {dst}")

def is_valid_syntax(content: str) -> bool:
    try:
        ast.parse(content)
        return True
    except SyntaxError:
        return False

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


def attack_e225(content: str, max_modifications=3) -> tuple[str, bool]:
    """Removes spaces around operators (E225, E226, E227, E228)."""
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(content.encode('utf-8')).readline))
    except tokenize.TokenError:
        return content, False

    target_ops = {
        # (E226)
        '+', '-', '*', '/', '%', '//', '**',
        # (E227)
        '&', '|', '^', '<<', '>>',
        # (E225)
        '==', '!=', '<=', '>=', '<', '>',
        '=', '->', ':=', '@',
        # (E225)
        '+=', '-=', '*=', '/=', '%=', '//=', '**=',
        '&=', '|=', '^=', '<<=', '>>=', '@=',
    }

    lines = content.split('\n')
    candidates_by_line = defaultdict(list)

    depth = 0
    for tok in tokens:
        if tok.type != tokenize.OP:
            continue
        if tok.string in ('(', '[', '{'):
            depth += 1
            continue
        if tok.string in (')', ']', '}'):
            depth -= 1
            continue
        if tok.string in target_ops:
            if tok.string == '=' and depth > 0:
                continue  
            candidates_by_line[tok.start[0] - 1].append(tok)

    modifications_done = 0

    for row in sorted(candidates_by_line.keys(), reverse=True):
        if modifications_done >= max_modifications:
            break

        line = lines[row]

        for tok in sorted(candidates_by_line[row], key=lambda t: t.start[1], reverse=True):
            if modifications_done >= max_modifications:
                break

            start_col = tok.start[1]
            end_col   = tok.end[1]

            has_space_before = start_col > 0 and line[start_col - 1] == ' '
            has_space_after  = end_col < len(line) and line[end_col] == ' '

            if not (has_space_before or has_space_after):
                continue

            cut_before = 1 if has_space_before else 0
            cut_after  = 1 if has_space_after  else 0

            line = line[:start_col - cut_before] + tok.string + line[end_col + cut_after:]
            modifications_done += 1

        lines[row] = line

    if modifications_done > 0:
        candidate_content = '\n'.join(lines)
        if is_valid_syntax(candidate_content):
            return candidate_content, True

    return content, False


def attack_e231(content: str, max_modifications=3) -> tuple[str, bool]:

    try:
        tokens = list(tokenize.tokenize(io.BytesIO(content.encode('utf-8')).readline))
    except tokenize.TokenError:
        return content, False

    lines = content.split('\n')
    modifications_done = 0

    for tok in reversed(tokens):
        if tok.type == tokenize.OP and tok.string in {',', ';', ':'}:
            row = tok.start[0] - 1
            end_col = tok.end[1]
            
            line = lines[row]
            
            if tok.string == ':' and end_col >= len(line.rstrip()):
                continue
            
            if end_col < len(line) and line[end_col] == ' ':

                spaces_count = 0
                while (end_col + spaces_count) < len(line) and line[end_col + spaces_count] == ' ':
                    spaces_count += 1

                lines[row] = line[:end_col] + line[end_col + spaces_count:]
                modifications_done += 1

                if modifications_done >= max_modifications:
                    break

    if modifications_done > 0:
        candidate_content = '\n'.join(lines)
        if is_valid_syntax(candidate_content):
            return candidate_content, True

    return content, False


def attack_e271(content: str, max_modifications=3) -> tuple[str, bool]:
    """
    Adds multiple spaces after keywords (rule E271), 
    ignoring strings and comments.
    """
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(content.encode('utf-8')).readline))
    except tokenize.TokenError:
        return content, False

    lines = content.split('\n')
    modifications_done = 0

    target_keywords = {
        'if', 'else', 'while', 'return', 'for', 'with', 'elif', 'def', 'class', 'import', 'from',
        'not', 'and', 'or', 'in', 'is', 'lambda', 'yield', 'async', 'await'
    }

    for tok in reversed(tokens):

        if tok.type == tokenize.NAME and keyword.iskeyword(tok.string) and tok.string in target_keywords:
            row = tok.start[0] - 1
            end_col = tok.end[1]
            line = lines[row]

            if end_col < len(line) and line[end_col] == ' ':

                if end_col + 1 < len(line) and line[end_col + 1] != ' ':

                    lines[row] = line[:end_col] + '  ' + line[end_col + 1:]
                    modifications_done += 1
                    
                    if modifications_done >= max_modifications:
                        break

    if modifications_done > 0:
        candidate_content = '\n'.join(lines)
        if is_valid_syntax(candidate_content):
            return candidate_content, True

    return content, False


def attack_e272(content: str, max_modifications=3) -> tuple[str, bool]:
    """
    Adds multiple spaces before keywords (rule E272), 
    ignoring strings and comments.
    """
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(content.encode('utf-8')).readline))
    except tokenize.TokenError:
        return content, False

    lines = content.split('\n')
    modifications_done = 0

    target_keywords = {'in', 'and', 'or', 'is', 'if', 'else', 'for', 'not', 'as'}

    for tok in reversed(tokens):
        if tok.type == tokenize.NAME and keyword.iskeyword(tok.string) and tok.string in target_keywords:
            row = tok.start[0] - 1
            start_col = tok.start[1]
            line = lines[row]

            if start_col >= 1:

                if line[start_col - 1] == ' ' and (start_col == 1 or line[start_col - 2] != ' '):
                    
                    lines[row] = line[:start_col - 1] + '  ' + line[start_col:]
                    modifications_done += 1
                
                if modifications_done >= max_modifications:
                    break

    if modifications_done > 0:
        candidate_content = '\n'.join(lines)
        if is_valid_syntax(candidate_content):
            return candidate_content, True

    return content, False


def attack_w292(content: str) -> tuple[str, bool]:
    """
    Removes trailing newline of the file (rule W292)
    """
    new_content = content.rstrip("\n")
    if new_content != content and is_valid_syntax(new_content):
        return new_content, True
    return content, False


def attack_blank_lines(content: str, max_modifications=3) -> tuple[str, bool]:
    """
    Removes blank lines to trigger E301/E302/E305/E306
    """
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(content.encode('utf-8')).readline))
    except tokenize.TokenError:
        return content, False

    lines = content.split('\n')

    protected_lines = set()
    for tok in tokens:
        if tok.type == tokenize.STRING:
            for r in range(tok.start[0] - 1, tok.end[0]):
                protected_lines.add(r)

    high_impact_rows = set()
    for tok in tokens:
        if tok.type == tokenize.NAME and tok.string in ('def', 'class'):
            def_row = tok.start[0] - 1
            for r in range(max(0, def_row - 3), min(len(lines), def_row + 3)):
                if r not in protected_lines and lines[r].strip() == '':
                    high_impact_rows.add(r)

    low_impact_rows = set()
    for i, line in enumerate(lines):
        if i not in protected_lines and i not in high_impact_rows and line.strip() == '':
            low_impact_rows.add(i)

    candidates = (
        sorted(high_impact_rows, reverse=True) +
        sorted(low_impact_rows,  reverse=True)
    )

    rows_to_delete = candidates[:max_modifications]

    if not rows_to_delete:
        return content, False

    for i in sorted(rows_to_delete, reverse=True):
        del lines[i]

    candidate_content = '\n'.join(lines)
    if is_valid_syntax(candidate_content):
        return candidate_content, True

    return content, False


def attack_e241(content: str, max_modifications=3) -> tuple[str, bool]:
    """
    Adds multiple spaces after commas (rule E241).
    """
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(content.encode('utf-8')).readline))
    except tokenize.TokenError:
        return content, False

    lines = content.split('\n')
    modifications_done = 0

    for tok in reversed(tokens):
        if tok.type == tokenize.OP and tok.string in {',', ';', ':'}:
            row = tok.start[0] - 1
            end_col = tok.end[1]
            line = lines[row]

            if tok.string == ':' and end_col >= len(line.rstrip()):
                continue

            if end_col + 1 < len(line):
                if line[end_col] == ' ' and line[end_col + 1] != ' ':
                    
                    lines[row] = line[:end_col] + '   ' + line[end_col + 1:]
                    modifications_done += 1
                    
                    if modifications_done >= max_modifications:
                        break

    if modifications_done > 0:
        candidate_content = '\n'.join(lines)
        if is_valid_syntax(candidate_content):
            return candidate_content, True

    return content, False


def attack_e251(content: str, max_modifications=3) -> tuple[str, bool]:
    """
    Adds spaces around '=' sign in function parameters or keyword arguments (rule E251),
    ignoring strings, comments, and standard assignments outside functions.
    """
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(content.encode('utf-8')).readline))
    except tokenize.TokenError:
        return content, False

    lines = content.split('\n')
    modifications_done = 0

    paren_depth = 0

    for tok in reversed(tokens):
        if tok.type == tokenize.OP:
            if tok.string == ')':
                paren_depth += 1
            elif tok.string == '(':
                paren_depth -= 1
                if paren_depth < 0:
                    paren_depth = 0 

            elif tok.string == '=' and paren_depth > 0:
                row = tok.start[0] - 1
                start_col = tok.start[1]
                end_col = tok.end[1]
                line = lines[row]

                has_space_before = start_col > 0 and line[start_col - 1] == ' '
                has_space_after = end_col < len(line) and line[end_col] == ' '
                
                if not has_space_before and not has_space_after:
             
                    lines[row] = line[:start_col] + ' = ' + line[end_col:]
                    modifications_done += 1
                    
                    if modifications_done >= max_modifications:
                        break

    if modifications_done > 0:
        candidate_content = '\n'.join(lines)
        if is_valid_syntax(candidate_content):
            return candidate_content, True

    return content, False


def attack_e123(content: str, max_modifications=3) -> tuple[str, bool]:
    """
    Offsets the closing bracket indentation in a multi-line block (rule E123).
    """
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(content.encode('utf-8')).readline))
    except tokenize.TokenError:
        return content, False

    stack = []
    pairs = []
    
    for tok in tokens:

        if tok.exact_type in {tokenize.LPAR, tokenize.LSQB, tokenize.LBRACE}:
            stack.append(tok)

        elif tok.exact_type in {tokenize.RPAR, tokenize.RSQB, tokenize.RBRACE}:
            if stack:
                open_tok = stack.pop()
                pairs.append((open_tok, tok))

    lines = content.split('\n')
    modifications_done = 0

    for open_tok, close_tok in reversed(pairs):
        open_row = open_tok.start[0] - 1
        close_row = close_tok.start[0] - 1

        if open_row != close_row:
            close_line = lines[close_row]
            close_col = close_tok.start[1]
            

            if close_line[:close_col].strip() == "":
                
                open_line = lines[open_row]

                open_indent_len = len(open_line) - len(open_line.lstrip())

                bad_indent = " " * (open_indent_len + 3)

                new_line = bad_indent + close_line[close_col:]
                if new_line == close_line:
                    continue 
                lines[close_row] = new_line
                modifications_done += 1
                
                if modifications_done >= max_modifications:
                    break

    if modifications_done > 0:
        candidate_content = '\n'.join(lines)
        if is_valid_syntax(candidate_content):
            return candidate_content, True

    return content, False


def attack_tab_formatting(content: str, spaces_per_tab=4) -> tuple[str, bool]:
    """
    Converts all indentation tabs to spaces (opposite of tab_formatting),
    introducing violations that tab_formatting would fix.
    """
    try:
        tokens = list(tokenize.tokenize(io.BytesIO(content.encode('utf-8')).readline))
    except tokenize.TokenError:
        return content, False

    protected_lines = get_protected_lines(content)

    lines = content.split('\n')

    continuation_lines = set()
    for i, line in enumerate(lines):
        if line.rstrip('\r\n ').endswith('\\') and i + 1 < len(lines):
            continuation_lines.add(i + 2) 

    modifications_done = 0

    for i in range(len(lines)):
        lineno = i + 1
        
        if lineno in protected_lines or lineno in continuation_lines:
            continue

        line = lines[i]

        tab_count = len(line) - len(line.lstrip('\t'))
        
        if tab_count > 0:
            spaces = ' ' * (tab_count * spaces_per_tab)
            lines[i] = spaces + line[tab_count:]
            modifications_done += 1

    if modifications_done > 0:
        candidate_content = '\n'.join(lines)
        if is_valid_syntax(candidate_content):
            return candidate_content, True
            
    return content, False


def attack_formatting(folder_path: str):
    # List of all available attack functions
    attack_functions = [
        attack_e225,
        attack_e231,
        attack_e271,
        attack_e272,
        attack_w292,
        attack_blank_lines,
        attack_e241,
        attack_e251,
        attack_e123,
        attack_tab_formatting
    ]
    
    print("── Formatting Attacks starting ──")
    for py_file in Path(folder_path).rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")

            attack_applied = False
            final_content = content
            
            for attack in attack_functions:
                modified_content, success = attack(final_content)
                if success:
                    final_content = modified_content
                    attack_applied = True
            
            if attack_applied:
                py_file.write_text(final_content, encoding="utf-8")
            else:
                print(f"[{py_file.name}] No applicable attack (code unchanged)")

        except Exception as e:
            print(f"Error on {py_file}: {e}")
    print("── Formatting Attacks executed successfully ──")
    
            
# RUFF ATTACK        
def ruff_attack(folder_path):
    command = [
        "ruff",
        "check",
        "--config",
        RUFF_CONFIG,
        "--fix",
        folder_path
    ]
    
    command_str = ' '.join(command)
    result = subprocess.run(command_str, text=True, capture_output=True)
    
    if result.returncode == 0:
        print("── Step 3/4: Ruff executed successfully ──")
    else:
        print("Something went wrong.")
        

def sourcery_login(token=None):
    token = token or os.environ.get("SOURCERY_TOKEN")
    if not token:
        raise ValueError("Sourcery token not found")
    
    comm = ['sourcery', 'login', '--token', token]
    result = subprocess.run(comm, text=True)
    return result.returncode == 0


def sourcery_attack(folder_path, token=None):
    if not sourcery_login(token):
        print("Cannot proceed without authentication.")
        return

    comm = ['sourcery', 'review', '--fix', folder_path]
    result = subprocess.run(comm, text=True)
    if result.returncode == 0:
        print("── Step 2/4: Sourcery executed successfully ──")