import argparse
import ast
import json
import keyword
import logging
import os
import re
import shutil
import warnings
from pathlib import Path

import libcst as cst
import torch
import transformers
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langchain_huggingface import HuggingFacePipeline
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

transformers.logging.set_verbosity_error()
warnings.filterwarnings("ignore")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("langchain_hf_codegen")

SYSTEM_PROMPT_RENAME = (
    "You are an expert Python code analyzer. "
    "Your output must be a valid JSON object only. "
    "Do NOT include any explanations, markdown formatting, "
    "code fences, or any text other than the JSON."
)

RENAME_MAPPING_PROMPT = (
    "Given the following Python code:\n\n"
    "{code}\n\n"
    "Rename the following variable and parameter names to new meaningful, semantic names.\n"
    "Names to rename: {names}\n\n"
    "STRICT RULES:\n"
    "- Use snake_case\n"
    "- Each old name must map to exactly one new name\n"
    "- The same old name must always map to the same new name\n"
    "- Do NOT rename function names, class names, or imports\n"
    "- Return ONLY a valid JSON object like: {{\"old_name\": \"new_name\", ...}}\n"
    "- No markdown, no explanations, no code fences"
)


def init_llm(model_name: str) -> HuggingFacePipeline:
    has_gpu = torch.cuda.is_available()
    device = 0 if has_gpu else -1
    dtype = torch.float16 if has_gpu else torch.float32

    logger.info(
        f"Detected device: {'GPU (' + torch.cuda.get_device_name(0) + ')' if has_gpu else 'CPU'}"
    )

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    tokenizer.padding_side = "left"

    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        torch_dtype=dtype
    )

    hf_pipe = pipeline(
        "text-generation",
        model=model,
        tokenizer=tokenizer,
        max_new_tokens=256,
        max_length=None,
        do_sample=False,
        return_full_text=False,
        device=device
    )

    return HuggingFacePipeline(pipeline=hf_pipe)


def build_rename_chain(llm: HuggingFacePipeline):
    return (
        PromptTemplate(
            template=f"<|system|>\n{SYSTEM_PROMPT_RENAME}\n<|user|>\n{RENAME_MAPPING_PROMPT}\n<|assistant|>\n",
            input_variables=["code", "names"]
        )
        | llm
        | StrOutputParser()
    )


def extract_renameable_names(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    excluded = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            excluded.add(node.name)
        elif isinstance(node, ast.ClassDef):
            excluded.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                excluded.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                excluded.add(alias.asname or alias.name)

    renameable = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            all_args = (
                node.args.args
                + node.args.posonlyargs
                + node.args.kwonlyargs
            )
            for arg in all_args:
                if arg.arg not in excluded and arg.arg not in ('self', 'cls'):
                    renameable.add(arg.arg)
            if node.args.vararg and node.args.vararg.arg not in excluded:
                renameable.add(node.args.vararg.arg)
            if node.args.kwarg and node.args.kwarg.arg not in excluded:
                renameable.add(node.args.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if node.id not in excluded:
                renameable.add(node.id)

    return sorted(renameable)


class RenameTransformer(cst.CSTTransformer):

    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping

    def leave_Name(
        self, original_node: cst.Name, updated_node: cst.Name
    ) -> cst.Name:
        if updated_node.value in self.mapping:
            return updated_node.with_changes(value=self.mapping[updated_node.value])
        return updated_node


def apply_rename_mapping(code: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return code
    try:
        tree = cst.parse_module(code)
        new_tree = tree.visit(RenameTransformer(mapping))
        return new_tree.code
    except cst.ParserSyntaxError:
        logger.warning("libcst: parsing failed, returning original code.")
        return code


def parse_mapping_response(response: str) -> dict[str, str]:
    clean = re.sub(r'```(?:json)?', '', response).replace('```', '').strip()
    try:
        mapping = json.loads(clean)
        if isinstance(mapping, dict):
            validated = {}
            for k, v in mapping.items():
                new_name = str(v)
                if new_name.isidentifier() and not keyword.iskeyword(new_name):
                    validated[str(k)] = new_name
                else:
                    logger.warning(
                        f"Invalid name ignored in LLM mapping: "
                        f"{k!r} -> {new_name!r}"
                    )
            return validated
    except json.JSONDecodeError:
        logger.warning(f"Invalid JSON in LLM response: {clean[:200]}")
    return {}


def is_valid_python(code: str) -> bool:
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def rename_code(rename_chain, code: str, file_label: str = "") -> str:
    trailing_match = re.search(r'\n+$', code)
    trailing_newlines = trailing_match.group() if trailing_match else ""

    names = extract_renameable_names(code)
    if not names:
        return code

    mapping_response = rename_chain.invoke({
        "code": code,
        "names": json.dumps(names)
    })
    mapping = parse_mapping_response(mapping_response)

    clean_code = apply_rename_mapping(code, mapping)
    clean_code = clean_code.rstrip('\n') + trailing_newlines

    if not is_valid_python(clean_code):
        label = f" for {file_label}" if file_label else ""
        logger.warning(f"Invalid output{label}; using the original code as a fallback.")
        return code

    return clean_code


def run_pipeline_folder(
    input_folder: Path,
    model_name: str,
    output_dir: Path | None = None,
    start_idx: int = 0,
    end_idx: int | None = None,
) -> Path:
    base_dir = output_dir if output_dir else input_folder.parent
    base_dir.mkdir(parents=True, exist_ok=True)

    attacked_folder = base_dir / f"{input_folder.name}_attacked"
    if attacked_folder.exists():
        shutil.rmtree(attacked_folder)
    shutil.copytree(input_folder, attacked_folder)

    llm = init_llm(model_name)
    rename_chain = build_rename_chain(llm)

    py_files = sorted(attacked_folder.rglob("*.py"))
    py_files = py_files[start_idx:end_idx + 1] if end_idx is not None else py_files[start_idx:]

    if not py_files:
        print("No .py files found in the folder")
        return attacked_folder

    for py_file in tqdm(py_files, desc="Rename"):
        original_code = py_file.read_text(encoding="utf-8")
        clean_code = rename_code(rename_chain, original_code, file_label=py_file.name)
        py_file.write_text(clean_code, encoding="utf-8")

    return attacked_folder


def run_pipeline_single_file(
    input_file: Path,
    model_name: str,
    output_dir: Path | None = None,
) -> Path:
    original_code = input_file.read_text(encoding="utf-8")

    out_dir = output_dir if output_dir else input_file.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / f"{input_file.stem}_attacked{input_file.suffix}"
    shutil.copy2(input_file, output_path)

    llm = init_llm(model_name)
    rename_chain = build_rename_chain(llm)

    print("RENAMING VARIABLES STARTING")
    clean_code = rename_code(rename_chain, original_code, file_label=input_file.name)

    output_path.write_text(clean_code, encoding="utf-8")
    print("RENAMING VARIABLES EXECUTED SUCCESSFULLY")
    return output_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Applies semantic renaming (via LLM) to Python code. "
            "Accepts a folder containing .py files (folder mode: "
            "creates <folder_name>_attacked) or a single .py file "
            "(single file mode: creates <name>_attacked.py)."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("test_LLM_code_watermark.py"),
        help="Path to a single .py file or a folder containing .py files"
    )
    parser.add_argument(
        "--model-name",
        default="Qwen/Qwen2.5-Coder-1.5B-Instruct",
        help="Name or path of the Hugging Face model to use"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: same directory as input)"
    )
    parser.add_argument(
        "--start-idx",
        type=int,
        default=0,
        help="[folder mode only] starting index among found .py files"
    )
    parser.add_argument(
        "--end-idx",
        type=int,
        default=None,
        help="[folder mode only] ending index inclusive (default: up to the last file)"
    )
    return parser


def main():
    args = build_arg_parser().parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Path not found: {args.input}")

    if args.input.is_dir():
        run_pipeline_folder(
            args.input,
            args.model_name,
            args.output_dir,
            args.start_idx,
            args.end_idx,
        )
    elif args.input.suffix == ".py":
        run_pipeline_single_file(
            args.input,
            args.model_name,
            args.output_dir,
        )
    else:
        raise ValueError(
            f"Unsupported input: '{args.input}'. Specify a .py file or a folder."
        )


if __name__ == "__main__":
    main()