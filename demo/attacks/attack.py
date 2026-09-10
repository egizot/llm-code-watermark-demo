import argparse
import logging
import shutil
from pathlib import Path

from tqdm import tqdm

from rename import (
    build_rename_chain,
    init_llm,
    rename_code,
)

from attacks import (
    attack_blank_lines,
    attack_e123,
    attack_e225,
    attack_e231,
    attack_e241,
    attack_e251,
    attack_e271,
    attack_e272,
    attack_tab_formatting,
    attack_w292,
    ruff_attack,
    sourcery_attack,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("pipeline_attack")

 
def apply_rename_to_file(file_path: Path, rename_chain) -> None:
    try:
        original_code = file_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Unable to read {file_path}: {e}")
        return
 
    clean_code = rename_code(rename_chain, original_code, file_label=file_path.name)
 
    if clean_code == original_code:
        logger.info(f"[rename] No changes applied to {file_path.name}")
        return
 
    file_path.write_text(clean_code, encoding="utf-8")
    logger.info(f"[rename] Applied to {file_path.name}")
 
 
def run_rename(target: Path, model_name: str) -> None:
    llm = init_llm(model_name)
    rename_chain = build_rename_chain(llm)
 
    if target.is_file():
        apply_rename_to_file(target, rename_chain)
        print("── Step 1/4: Rename executed successfully ──")
    else:
        py_files = sorted(target.rglob("*.py"))
        if not py_files:
            logger.warning(f"[rename] No .py files found in {target}")
            return
        for py_file in tqdm(py_files, desc="Rename"):
            apply_rename_to_file(py_file, rename_chain)
        print("── Step 1/4: Rename executed successfully ──")


_FORMATTING_ATTACKS = [
    attack_e225,
    attack_e231,
    attack_e271,
    attack_e272,
    attack_w292,
    attack_blank_lines,
    attack_e241,
    attack_e251,
    attack_e123,
    attack_tab_formatting,
]


def apply_formatting_to_file(file_path: Path) -> None:
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Unable to read {file_path}: {e}")
        return

    attack_applied = False
    final_content = content

    for attack_fn in _FORMATTING_ATTACKS:
        modified_content, success = attack_fn(final_content)
        if success:
            final_content = modified_content
            attack_applied = True

    if attack_applied:
        file_path.write_text(final_content, encoding="utf-8")
        logger.info(f"[formatting] Applied to {file_path.name}")
    else:
        logger.info(f"[formatting] No applicable attack to {file_path.name}")


def run_formatting(target: Path) -> None:
    if target.is_file():
        apply_formatting_to_file(target)
        print("── Step 4/4: Formatting Attacks executed successfully ──")
    else:
        py_files = sorted(target.rglob("*.py"))
        if not py_files:
            logger.warning(f"[formatting] No .py files found in {target}")
            return
        for py_file in tqdm(py_files, desc="Formatting"):
            apply_formatting_to_file(py_file)
        print("── Step 4/4: Formatting Attacks executed successfully ──")


def create_attacked_copy(input_path: Path) -> Path:
    if input_path.is_file():
        attacked = input_path.parent / f"{input_path.stem}_attacked{input_path.suffix}"
        shutil.copy2(input_path, attacked)
        logger.info(f"File copy created: {attacked}")
    else:
        attacked = input_path.parent / f"{input_path.name}_attacked"
        if attacked.exists():
            shutil.rmtree(attacked)
        shutil.copytree(input_path, attacked)
        logger.info(f"Folder Copy Created: {attacked}")
    return attacked


def run_pipeline(
    input_path: Path,
    model_name: str,
    do_rename: bool,
    do_sourcery: bool,
    do_ruff: bool,
    do_formatting: bool,
) -> Path:

    if not any([do_rename, do_sourcery, do_ruff, do_formatting]):
        logger.warning(
            "No transformation selected. "
            "Use at least one of the following: Rename, Sourcery, Ruff, Formatting."
        )

    attacked = create_attacked_copy(input_path)

    if do_rename:
        print("── Step 1/4: Rename LLM Started ──")
        run_rename(attacked, model_name)
    else:
        print("── Step 1/4: Rename LLM [SKIPPED] ──")

    if do_sourcery:
        print("── Step 2/4: Sourcery Started ──")
        sourcery_attack(str(attacked))
    else:
        print("── Step 2/4: Sourcery [SKIPPED] ──")

    if do_ruff:
        print("── Step 3/4: Ruff Started ──")
        ruff_attack(str(attacked))
    else:
        print("── Step 3/4: Ruff [SKIPPED] ──")

    if do_formatting:
        print("── Step 4/4: Formatting attacks Started ──")
        run_formatting(attacked)
    else:
        print("── Step 4/4: Formatting attacks [SKIPPED] ──")

    logger.info(f"Pipeline completed. Output: {attacked}")
    return attacked


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Applies an attack pipeline to a .py file or folder. "
            "Always creates an _attacked copy without modifying the original."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Path to the .py file or folder to be attacked",
    )
    parser.add_argument(
        "--re",
        dest="do_rename",
        action="store_true",
        help="Enable semantic renaming via LLM",
    )
    parser.add_argument(
        "--so",
        dest="do_sourcery",
        action="store_true",
        help="Enable refactoring via Sourcery",
    )
    parser.add_argument(
        "--ru",
        dest="do_ruff",
        action="store_true",
        help="Enable linting with --fix via Ruff",
    )
    parser.add_argument(
        "--fo",
        dest="do_formatting",
        action="store_true",
        help="Enable formatting attacks (E225, E231, E271, ...)",
    )
    parser.add_argument(
        "--model-name",
        default="Qwen/Qwen2.5-Coder-1.5B-Instruct",
        help=(
            "HuggingFace model for renaming (default: Qwen/Qwen2.5-Coder-1.5B-Instruct).\n"
            "Ignored if --re is not enabled."
        ),
    )
    return parser


def main():
    args = build_arg_parser().parse_args()

    if not args.input.exists():
        raise FileNotFoundError(f"Path not found: {args.input}")

    if args.input.is_file() and args.input.suffix != ".py":
        raise ValueError(
            f"Input not supported: '{args.input.suffix}'. "
            "Specify a .py file or a folder containing .py files"
        )

    run_pipeline(
        input_path=args.input,
        model_name=args.model_name,
        do_rename=args.do_rename,
        do_sourcery=args.do_sourcery,
        do_ruff=args.do_ruff,
        do_formatting=args.do_formatting,
    )


if __name__ == "__main__":
    main()