# LLM Code Watermarking — Demo Interface
 
An interactive demo for the **LLM-code Detection via Robust Watermarking** system.
The app lets you embed a watermark into Python source code, detect whether a `.py` file was AI-generated, and test how well the watermark survives a range of adversarial attacks.
 
Built with [Streamlit](https://streamlit.io/).
 
---
 
## Features
 
The demo is organized into three tabs:
 
### Embedding
Takes a `.py` file or a folder of `.py` files, creates a copy with a `_watermark` suffix, and applies the watermark to the copy. A side-by-side diff shows exactly what changed in each file compared to the original.
 
### Detection
Analyzes a `.py` file or folder and classifies each file as **LLM-generated** or **Human-written**. You can choose:
- **Classification model** — Random Forest, Logistic Regression (L1/Lasso), or Logistic Regression (L2/Ridge)
- **Threshold** — `default` (0.5), or a threshold calibrated on a target false-positive rate (`fpr_5pct`, `fpr_1pct`)
Results are shown in a table with the predicted label and the estimated probability of the file being AI-generated.
 
### Attack
Simulates an adversary trying to remove or disrupt the watermark. Creates a copy with an `_attacked` suffix and applies, in a fixed order, any combination of:
 
1. **Rename** — semantic variable renaming via an LLM (default: `Qwen/Qwen2.5-Coder-1.5B-Instruct`)
2. **Sourcery** — automated refactoring
3. **Ruff** — linting with autofix
4. **Formatting** — a set of formatting-level perturbations (spacing, blank lines, indentation, etc.)
As with embedding, a diff view highlights what the attack changed.
 
---
 
## Selecting files
 
There's no bundled example dataset — you point the interface at **your own code**. Each tab has a path field that works in two ways:
 
- **Type a path manually**, or
- **Use the "File" / "Folder" buttons** to open a native file/folder picker (available only when the app runs locally with a display; it falls back to manual entry otherwise).
You can select either a single `.py` file or an entire folder — in the latter case, all `.py` files inside are processed recursively.
 
---
 
## Installation
 
```bash
pip install -r requirements.txt
```
 
**Additional requirements:**
- **Ruff** must be available on your system to run the Ruff attack step (configuration already provided in `demo/attacks/ruff.toml`).
- **Sourcery** must be installed and authenticated to run the Sourcery attack step — see the note below before running the attacks.  
**A note on Sourcery:** the Sourcery attack relies on the Sourcery library, which is a paid commercial service. Using this feature requires a paid authentication token obtained from the [official Sourcery platform](https://app.sourcery.ai/accounts/251466/ide-integration) (IDE extensions-only access is sufficient), which must be configured as an environment variable named `SOURCERY_TOKEN`.
- The **Rename** attack downloads a Hugging Face model on first use (`Qwen/Qwen2.5-Coder-1.5B-Instruct` by default) — this can take a few minutes and works on CPU, but a GPU is recommended for speed.  
---
 
## Usage
 
From the project root:
 
```bash
streamlit run demo/app.py
```
 
The app will open in your browser. Pick a tab, select a file or folder, and run the corresponding action.

 
## Notes
 
- Files created by the app are never overwritten silently: embedding produces a `<name>_watermark` copy, and attacks produce a `<name>_attacked` copy. Your original file is never modified.
- Classification models and thresholds are pre-trained and stored under `demo/saved_models/`; the demo does not include a training pipeline.
- The Attack pipeline always runs in the fixed order **Rename → Sourcery → Ruff → Formatting**, applying only the steps you enable.
 