import json
import os
import shutil
import sys
import libcst as cst

import joblib
import pandas as pd

from transformations import TRANSFORMATIONS

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(SCRIPT_DIR, "saved_models")

DETECTION_TEMP = 'detection_temp'

MODEL_CONFIGS = {
    'random_forest': {
        'file': 'model_random_forest.pkl',
        'label': 'Random Forest',
    },
    'logreg_l1': {
        'file': 'model_logreg_l1.pkl',
        'label': 'LogReg L1 (Lasso)',
    },
    'logreg_l2': {
        'file': 'model_logreg_l2.pkl',
        'label': 'LogReg L2 (Ridge)',
    },
}

THRESHOLD_CHOICES = ['default', 'fpr_5pct', 'fpr_1pct']

def is_valid_python(file_path: str) -> bool:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        cst.parse_module(source)
        return True
    except Exception:
        return False
    
def copy_folder_force(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    print(f"Copy folder {src} to {dst}")
    

def detection(file_or_folder_path):
    if not os.path.exists(file_or_folder_path):
        print(f"Error: Path does not exist: {file_or_folder_path}")
        return {}
    
    shutil.rmtree(DETECTION_TEMP, ignore_errors=True)

    original_python_files = []
    if os.path.isfile(file_or_folder_path):
        if not file_or_folder_path.endswith('.py'):
            print(f"Error: The file '{file_or_folder_path}' is not a Python file (.py).")
            return {}
        original_python_files.append(file_or_folder_path)
    elif os.path.isdir(file_or_folder_path):
        for root, _, files in os.walk(file_or_folder_path):
            for f in files:
                if f.endswith('.py') and not f.endswith('_transformed.py'):
                    original_python_files.append(os.path.join(root, f))
                    
        if not original_python_files:
            print("Warning: No .py files found.")
            return {}

    file_pairs = []
    if os.path.isfile(file_or_folder_path):
        transformed_path = os.path.join(DETECTION_TEMP, os.path.basename(file_or_folder_path))
        os.makedirs(DETECTION_TEMP, exist_ok=True)
        shutil.copy2(file_or_folder_path, transformed_path)
        file_pairs.append((file_or_folder_path, transformed_path))
    elif os.path.isdir(file_or_folder_path):
        transformed_dir = os.path.join(DETECTION_TEMP, os.path.basename(file_or_folder_path))
        copy_folder_force(file_or_folder_path, transformed_dir)
        for orig_file in original_python_files:
            rel_path = os.path.relpath(orig_file, file_or_folder_path)
            trans_file = os.path.join(transformed_dir, rel_path)
            file_pairs.append((orig_file, trans_file))

    keys = list(TRANSFORMATIONS.keys()) 
    total = len(file_pairs)
    
    MAX_PASSES = 10 
    
    all_feature_vectors = {}

    for idx, (orig_file, trans_file) in enumerate(file_pairs, 1):
        
        if not is_valid_python(trans_file):
            print(f"Syntax or indentation errors found in {trans_file}; skipped")
            continue
        
        print(f"  [{idx}/{total}] Processing: {os.path.basename(trans_file)}...", flush=True)
        
        current_content = None
        
        file_vector = [0] * len(keys) 

        for pass_num in range(MAX_PASSES):
            pass_has_changed = False 
            
            for i, key in enumerate(keys):
       
                has_changed, current_content = TRANSFORMATIONS[key](
                    trans_file, execute = True, content=current_content
                )
     
                if has_changed:
                    file_vector[i] = 1
                    pass_has_changed = True
         
            if not pass_has_changed:
                print(f"  [{idx}/{total}] Processed: {os.path.basename(trans_file)}...", flush=True)
                break
        else:
            print(f"  [{idx}/{total}] Processed: {os.path.basename(trans_file)}...", flush=True)
            
        all_feature_vectors[orig_file] = file_vector
            
    
    shutil.rmtree(DETECTION_TEMP, ignore_errors=True)
    
    return all_feature_vectors


def load_model(model_key):
    
    if model_key not in MODEL_CONFIGS:
        print(f"Error: Model '{model_key}' not recognized. Valid options: {list(MODEL_CONFIGS.keys())}")
        sys.exit(1)

    config = MODEL_CONFIGS[model_key]
    path = os.path.join(MODELS_DIR, config['file'])
    if not os.path.exists(path):
        print(f"Error: Model not found at '{path}'. Run training first.")
        sys.exit(1)

    model = joblib.load(path)
    # print(f"-> Model '{config['label']}' loaded from '{path}'")
    return model, config['label']


def load_threshold(model_label, threshold_key):

    if threshold_key == 'default':
        return 0.5

    thresholds_path = os.path.join(MODELS_DIR, "thresholds.json")
    if not os.path.exists(thresholds_path):
        print(f"Error: Thresholds file not found at '{thresholds_path}'.")
        sys.exit(1)

    with open(thresholds_path, 'r') as f:
        thresholds = json.load(f)

    model_thresholds = thresholds.get(model_label)
    if model_thresholds is None:
        print(f"Error: No threshold saved for model '{model_label}' in '{thresholds_path}'.")
        sys.exit(1)

    t_info = model_thresholds.get(threshold_key)
    if t_info is None:
        print(f"Error: Constraint '{threshold_key}' is not achievable for '{model_label}' "
              f"(was not satisfiable on the validation set during training).")
        sys.exit(1)

    return t_info['threshold']


def build_feature_dataframe(feature_vectors, model):

    keys = list(TRANSFORMATIONS.keys())
    file_paths = list(feature_vectors.keys())
    data = [feature_vectors[fp] for fp in file_paths]

    if hasattr(model, 'feature_names_in_'):
        columns = list(model.feature_names_in_)
        if len(columns) != len(keys):
            print(f"Warning: The model expects {len(columns)} features, "
                  f"but detection() produces {len(keys)}. Check consistency with training.")
        df = pd.DataFrame(data, index=file_paths, columns=columns)
    else:
        df = pd.DataFrame(data, index=file_paths, columns=[str(k) for k in keys])

    return df


def classify(file_or_folder_path, model_key, threshold_key):

    feature_vectors = detection(file_or_folder_path)
    if not feature_vectors:
        print("No files to classify.")
        return {}

    model, model_label = load_model(model_key)
    threshold = load_threshold(model_label, threshold_key)
    print(f"\n INFO CLASSIFICATION:\n MODEL = {model_key}\n CONSTRAINT = {threshold_key}\n OPTIMAL THRESHOLD = {threshold:.4f}")

    X = build_feature_dataframe(feature_vectors, model)
    probs = model.predict_proba(X)[:, 1]
    preds = (probs >= threshold).astype(int)

    results = {}

    for file_path, prob, pred in zip(X.index, probs, preds):
        esito = "LLM GENERATED" if pred == 1 else "HUMAN"

        results[file_path] = {
            'prediction': esito,
            'probability': float(prob),
        }

    return results