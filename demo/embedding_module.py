import os
import shutil
import libcst as cst
from transformations import TRANSFORMATIONS

def is_valid_python(file_path: str) -> bool:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        cst.parse_module(source)
        return True
    except Exception:
        return False

def embedding(file_or_folder_path):
    if not os.path.exists(file_or_folder_path):
        return f"Error: Path does not exist: {file_or_folder_path}"
    
    if os.path.isfile(file_or_folder_path):
        if not file_or_folder_path.endswith('.py'):
            return f"Error: The file '{file_or_folder_path}' is not a Python file (.py). Operation canceled."
        
        base, ext = os.path.splitext(file_or_folder_path)
        watermark_path = f"{base}_watermark{ext}"
        
        if os.path.exists(watermark_path):
            os.remove(watermark_path)
        
        shutil.copy2(file_or_folder_path, watermark_path)
        
    elif os.path.isdir(file_or_folder_path):
        normalized_path = file_or_folder_path.rstrip(os.sep)
        watermark_path = f"{normalized_path}_watermark"
        
        if os.path.exists(watermark_path):
            shutil.rmtree(watermark_path)
        
        shutil.copytree(file_or_folder_path, watermark_path)
        
    else:
        return f"Error: '{file_or_folder_path}' is not a valid path type."

    file_or_folder_path = watermark_path
    
    python_files = []

    if os.path.isfile(file_or_folder_path):
        python_files.append(file_or_folder_path)
        
    elif os.path.isdir(file_or_folder_path):
        for root, _, files in os.walk(file_or_folder_path):
            for f in files:
                if f.endswith('.py') and not f.endswith('_transformed.py'):
                    python_files.append(os.path.join(root, f))
                
        if not python_files:
            return f"Warning: No .py files found in '{file_or_folder_path}'."
    
    keys = list(TRANSFORMATIONS.keys())
    total = len(python_files)

    MAX_PASSES = 10 
    
    for idx, file in enumerate(python_files, 1):
        if not is_valid_python(file):
            print(f"Syntax or indentation errors found in {file}; skipped")
            continue
        
        print(f"  [{idx}/{total}] Processing: {os.path.basename(file)}...", flush=True)
        
        current_content = None
        pass_has_changes = True
        pass_count = 0
        
        while pass_has_changes and pass_count < MAX_PASSES:
            pass_count += 1
            pass_has_changes = False
            
            for key in keys:
                changed, current_content = TRANSFORMATIONS[key](
                    file, 
                    execute = True ,
                    content=current_content
                )
                
                if changed:
                    pass_has_changes = True
        
        print(f"  [{idx}/{total}] Completed", flush=True)

    return None