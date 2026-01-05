

---

# Odoo QWeb i18n Fixer

**A CLI tool to refactor Odoo QWeb `t-value` attributes into translatable XML nodes.**

This script automates the tedious process of finding and fixing "untranslatable" strings hidden inside Python expressions in Odoo XML views. It features an interactive TUI (Terminal User Interface) to review candidates, a report generator, and a batch mode to apply safe, AST-based transformations.

## 🛑 The Problem

In Odoo QWeb views, text inside Python attributes (`t-value`, `t-attf-value`) is often invisible to the translation system. Odoo's translation crawler looks for standard XML text nodes or explicit `_()` calls, but it struggles with strings mixed into logic, dictionaries, or lists inside `t-value`.

**Common Untranslatable Scenarios:**

1. **Logic:** `t-value="name or 'New Record'"`
2. **Dictionaries:** `t-value="{'label': 'Submit', 'icon': 'fa-check'}"`
3. **Lists/Tuples:** `t-value="[('gift', 'Send a Gift'), ('fund', 'Donate to Fund')]"`
4. **Concatenation:** `t-value="user.name + ' Profile'"`

In all these cases, the strings remain hardcoded in English, creating a poor experience for international users.

## ✅ The Solution

This tool parses the Python AST (Abstract Syntax Tree) within your XML files. It intelligently decides between two strategies to make strings translatable without breaking your code logic.

### Strategy 1: XML Body Replacement (for Logic)

For simple strings or boolean logic, it moves the logic into the XML body using `t-if` and `t-else`.

**Before:**

```xml
<t t-set="title" t-value="name or 'New Record'"/>

```

**After (Standard XML):**

```xml
<t t-set="title">
    <t t-if="name"><t t-esc="name"/></t>
    <t t-else="">New Record</t>
</t>

```

### Strategy 2: Deep Variable Extraction (for Data Structures)

For Dictionaries, Lists, and Concatenations, the tool cannot move the content to the XML body because `t-value` must return a specific Python type (like a `dict` or `list`).

Instead, the tool **extracts the string to a unique XML variable** (creating a text node the scrapper *can* see) and references it in the Python structure.

#### Example: Lists & Tuples

**Before:**

```xml
<t t-set="options" t-value="[('a', 'Option A'), ('b', 'Option B')]"/>

```

**After:**

```xml
<t t-set="_txt_Option_A_0">Option A</t>
<t t-set="_txt_Option_B_1">Option B</t>

<t t-set="options" t-value="[('a', _txt_Option_A_0), ('b', _txt_Option_B_1)]"/>

```

#### Example: String Concatenation

**Before:**

```xml
<t t-set="full_title" t-value="type + ' Gift'"/>

```

**After:**

```xml
<t t-set="_txt_Gift_0"> Gift</t>
<t t-set="full_title" t-value="type + _txt_Gift_0"/>

```

## 🚀 Features

* **Interactive TUI Mode:** Browse all occurrences with an `htop`-like interface.
* **Live Preview:** See exactly how the XML line will be refactored (Extraction vs. Body).
* **Smart Filtering:** Filters by key (e.g., `label`, `title`, `options`) and uses heuristics to detect human text (ignoring internal IDs).
* **Open in Editor:** Press `ENTER` to jump straight to the file/line in VS Code, Nano, or Vim.


* **Deep AST Inspection:** Recursively searches Python Dictionaries, Lists, and BinOps to find hidden strings.
* **Smart Variable Naming:** Generates semantic variable names (e.g., `_txt_Save_Changes`) and handles collisions automatically to ensure valid XML.
* **Report Generation:** Supports piping to files (`> report.txt`) for full project audits. The script detects non-interactive terminals and switches to text-dump mode automatically.

## 📦 Installation

This is a standalone Python script. No heavy dependencies are required.

1. **Clone the repository:**
```bash
git clone https://github.com/yourusername/odoo-qweb-i18n-fixer.git
cd odoo-qweb-i18n-fixer

```


2. **Requirements:**
* Python 3.6+
* (Optional) `curses` library (pre-installed on Linux/macOS; for Windows, you may need `windows-curses`).



## 🛠️ Usage

### 1. Interactive Mode (Recommended)

Use this mode to audit your code, preview changes, and open files.

```bash
python3 main.py --list --path /path/to/your/odoo/module

```

**Controls:**

* `↑` / `↓`: Navigate list
* `/`: **Search** (Filter by key)
* `SPACE`: **Expand** row to see full XML preview
* `ENTER`: **Open file** in default editor at specific line
* `x`: Exclude key from view
* `r`: Reset filters
* `q`: Quit

### 2. Report Mode (File Dump)

If you pipe the output, the tool automatically detects non-interactive mode and dumps a clean table of all candidates.

```bash
# Save a full audit report to a text file
python3 main.py --list --path . > report.txt

```

### 3. Dry Run (Safe Check)

Run without flags to scan directories and print what *would* happen to the files without modifying them.

```bash
python3 main.py --path /path/to/your/odoo/module

```

### 4. Fix Mode (Apply Changes)

Use the `--force` flag to actually write changes to your files.

```bash
python3 main.py --path /path/to/your/odoo/module --force

```

## ⚙️ Configuration

You can customize the `KEY_LIST` inside the script to define which `t-set` keys should be targeted. The default list includes common UI attributes:

```python
KEY_LIST = [
    "label", "description", "title", "label_empty", "subtitle", "tooltip_title", 
    "placeholder", "title_content", "alt", "learn_more_text", "button_label", 
    "btn_text", "text", "internal_link_label", "invalid_hint", "submit_label",
    "base_name", "monthly", "name", "primary_button", "cells", "header", "options"
] 

```
