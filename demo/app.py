import html
import sys
import os
import traceback
from pathlib import Path

import streamlit as st

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, os.path.join(SCRIPT_DIR, "transformations"))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "attacks"))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "utils"))
from embedding_module import embedding
from detection_module import MODEL_CONFIGS, THRESHOLD_CHOICES, classify
from attack import run_pipeline
from stream_utils import diff_pairs, run_with_live_log
from file_picker import TKINTER_AVAILABLE, pick_path

st.set_page_config(page_title="Watermarking System Demo", layout="wide")
st.title("Demo: Watermarking System For Python Code")

AI_COLOR = "#ef4444"   
HUMAN_COLOR = "#22c55e"  

def render_detection_table(results: dict) -> str:
    rows_html = []
    for file_path, info in results.items():
        file_name = html.escape(Path(file_path).name)
        label = str(info["prediction"])
        label_safe = html.escape(label)
        prob_ai = max(0.0, min(100.0, info["probability"] * 100))
        label_upper = label.upper()
        is_human = "HUMAN" in label_upper
        label_color = HUMAN_COLOR if is_human else AI_COLOR

        pie_chart = (
            f'<div style="width:32px; height:32px; margin:0 auto; '
            f'background: conic-gradient({AI_COLOR} 0% {prob_ai:.1f}%, {HUMAN_COLOR} {prob_ai:.1f}% 100%); '
            f'border-radius:50%;"></div>'
            f'<div style="font-size:11px; opacity:0.7; margin-top:2px;">{prob_ai:.1f}%</div>'
        )

        row = (
            "<tr>"
            f'<td style="padding:8px 6px; border-bottom:1px solid rgba(128,128,128,0.25);">{file_name}</td>'
            f'<td style="padding:8px 6px; border-bottom:1px solid rgba(128,128,128,0.25); color:{label_color}; font-weight:600;">{label_safe}</td>'
            f'<td style="padding:8px 6px; border-bottom:1px solid rgba(128,128,128,0.25); text-align:center;">{pie_chart}</td>'
            "</tr>"
        )
        rows_html.append(row)

    css_style = """
    <style>
    .sticky-table-container {
        max-height: 300px; 
        overflow-y: auto; 
        border-bottom: 1px solid rgba(128,128,128,0.25);
    }
    .sticky-table {
        width: 100%; 
        border-collapse: separate; 
        border-spacing: 0; 
        font-size: 13px;
    }
    .sticky-header-th {
        position: sticky; 
        top: 0; 
        z-index: 10; 
        /* Native fallback and Streamlit variable so that it adapts to the theme in real time */
        background-color: #0e1117; 
        background-color: var(--background-color, #0e1117); 
        padding: 6px; 
        border-bottom: 1px solid rgba(128,128,128,0.5);
    }
    /* Guaranteed fallback in case the client is using a light theme */
    @media (prefers-color-scheme: light) {
        .sticky-header-th {
            background-color: #ffffff;
            background-color: var(--background-color, #ffffff);
        }
    }
    </style>
    """

    header_html = (
        "<thead>"
        "<tr>"
        '<th class="sticky-header-th" style="text-align: left; font-weight: normal;">'
        '<span style="font-size:12px; opacity:0.7;">file</span>'
        '</th>'
        '<th class="sticky-header-th" style="text-align: left; font-weight: normal;">'
        '<span style="font-size:12px; opacity:0.7;">result</span>'
        '</th>'
        '<th class="sticky-header-th" style="text-align:center; font-weight: normal;">'
        '<div style="font-size:12px; opacity:0.7;">🔴 LLM-generated &nbsp; 🟢 Human</div>'
        '<div style="font-size:11px; opacity:0.7; margin-top:2px; white-space:nowrap;">AI probability</div>'
        "</th>"
        "</tr>"
        "</thead>"
    )

    return (
        css_style +
        '<div class="sticky-table-container">'
        '<table class="sticky-table">'
        + header_html
        + "<tbody>"
        + "".join(rows_html)
        + "</tbody>"
        + "</table>"
        '</div>'
    )

def show_diff_section(original_root: Path, modified_root: Path, before_label: str, after_label: str):
    results = diff_pairs(original_root, modified_root)
    if not results:
        st.info("No .py files were found to compare")
        return

    changed = [r for r in results if r["changed"]]
    st.write(f"Modified files: **{len(changed)} / {len(results)}**")

    for r in results:
        icon = "✅" if r["changed"] else "⚪"
        with st.expander(f"{icon} {r['rel_path']}", expanded=False):
            if r["changed"]:
                col1, col2 = st.columns(2)
                col1.caption(before_label)
                col1.code(r["original"], language="python")
                col2.caption(after_label)
                col2.code(r["modified"], language="python")
            else:
                st.caption("No changes have been made to this file")


def _pick_and_set(key: str, kind: str):
    picked = pick_path(kind)
    if picked:
        st.session_state[key] = picked


def path_input_with_picker(label: str, key: str) -> str:

    col_input, col_file, col_folder = st.columns([6, 1, 1])

    with col_input:
        st.text_input(
            label,
            key=key,
            placeholder="Enter a path or use the buttons on the right →",
        )

    if TKINTER_AVAILABLE:
        with col_file:
            st.write("")
            st.button(
                "📄 File",
                key=f"{key}_pick_file",
                use_container_width=True,
                on_click=_pick_and_set,
                args=(key, "file"),
            )
        with col_folder:
            st.write("")
            st.button(
                "📁 Folder",
                key=f"{key}_pick_folder",
                use_container_width=True,
                on_click=_pick_and_set,
                args=(key, "folder"),
            )
    else:
        with col_file:
            st.caption("Native selector not available: Enter the path manually.")

    return st.session_state.get(key, "")


tab_embed, tab_detect, tab_attack = st.tabs(["🔏 Embedding", "🔍 Detection", "⚔️ Attack"])

with tab_embed:
    st.subheader("Watermark embedding")
    st.caption("Create a copy named `_watermark` of the file or folder and apply the transformations")

    embed_path_str = path_input_with_picker("Path to .py file or folder", key="embed_path")

    if st.button("Execute watermark embedding", key="embed_btn", type="primary"):
        if not embed_path_str:
            st.warning("Enter a valid path")
        else:
            input_path = Path(embed_path_str)
            if not input_path.exists():
                st.error(f"The path does not exist: {input_path}")
            else:
                log_placeholder = st.container(height=220, border=True).empty()
                try:
                    with st.spinner("Watermark embedding in progress..."):
                        message = run_with_live_log(
                            embedding, str(input_path), placeholder=log_placeholder
                        )
                except Exception:
                    st.error("Error during embedding:")
                    st.code(traceback.format_exc())
                else:
                    if message and message.startswith("Error"):
                        st.error(message)
                    else:
                        if message: 
                            st.warning(message)
                        else:
                            st.success("Watermark embedding completed")

                        if input_path.is_dir():
                            watermark_path = input_path.parent / f"{input_path.name}_watermark"
                        else:
                            watermark_path = input_path.with_name(
                                f"{input_path.stem}_watermark{input_path.suffix}"
                            )

                        if watermark_path.exists():
                            st.write(f"Copy created at: `{watermark_path}`")
                            show_diff_section(input_path, watermark_path, "Original", "With watermark")


with tab_detect:
    st.subheader("Watermark detection")
    st.caption("Extract features from the code and classify each file using the chosen model")

    detect_path_str = path_input_with_picker("Path to the .py file or folder to be analyzed", key="detect_path")

    col1, col2 = st.columns(2)
    model_key = col1.selectbox(
        "Classification Model",
        options=list(MODEL_CONFIGS.keys()),
        format_func=lambda k: MODEL_CONFIGS[k]["label"],
        key="detect_model",
    )
    threshold_key = col2.selectbox(
        "Threshold",
        options=THRESHOLD_CHOICES,
        key="detect_threshold",
    )

    if st.button("Execute classification", key="detect_btn", type="primary"):
        if not detect_path_str:
            st.warning("Enter a valid path")
        elif not Path(detect_path_str).exists():
            st.error(f"The path does not exist: {detect_path_str}")
        else:
            log_placeholder = st.container(height=220, border=True).empty()
            try:
                with st.spinner("Analysis in progress..."):
                    results = run_with_live_log(
                        classify,
                        detect_path_str,
                        model_key,
                        threshold_key,
                        placeholder=log_placeholder,
                    )
            except Exception:
                st.error("Error during classification:")
                st.code(traceback.format_exc())
            else:
                if not results:
                    st.warning("No results: Check the path or the files found")
                else:
                    st.markdown(render_detection_table(results), unsafe_allow_html=True)

with tab_attack:
    st.subheader("Watermark Attack")
    st.caption("Create a copy named `_attacked` and apply the selected transformations (fixed order: rename → sourcery → ruff → formatting)")

    attack_path_str = path_input_with_picker("Path to the .py file or folder to be attacked", key="attack_path")

    c1, c2, c3, c4 = st.columns(4)
    do_rename = c1.checkbox("Rename LLM", key="attack_re", value=False)
    do_sourcery = c2.checkbox("Sourcery", key="attack_so", value=False)
    do_ruff = c3.checkbox("Ruff", key="attack_ru", value=False)
    do_formatting = c4.checkbox("Formatting", key="attack_fo", value=False)

    model_name = st.text_input(
        "LLM for renaming (used only if Rename is enabled)",
        value="Qwen/Qwen2.5-Coder-1.5B-Instruct",
        key="attack_model",
        disabled=not do_rename,
    )
    if do_rename:
        st.caption("⏳ Renaming via LLM may take several minutes (including model loading).")

    if st.button("Execute attack", key="attack_btn", type="primary"):
        if not attack_path_str:
            st.warning("Enter a valid path")
        elif not Path(attack_path_str).exists():
            st.error(f"The path does not exist: {attack_path_str}")
        elif not any([do_rename, do_sourcery, do_ruff, do_formatting]):
            st.warning("Select at least one transformation")
        else:
            input_path = Path(attack_path_str)
            log_placeholder = st.container(height=220, border=True).empty()
            try:
                with st.spinner("Attack in progress..."):
                    attacked_path = run_with_live_log(
                        run_pipeline,
                        input_path,
                        model_name,
                        do_rename,
                        do_sourcery,
                        do_ruff,
                        do_formatting,
                        placeholder=log_placeholder,
                    )
            except Exception:
                st.error("Error during the attack:")
                st.code(traceback.format_exc())
            else:
                st.success(f"Attack completed. Copy created at: `{attacked_path}`")
                show_diff_section(input_path, attacked_path, "Before the attack", "After the attack")