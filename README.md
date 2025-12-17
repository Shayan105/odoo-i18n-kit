
# Odoo QWeb i18n Fixer

**A CLI tool to refactor Odoo QWeb `t-value` attributes into translatable XML nodes.**

This script automates the tedious process of finding and fixing "untranslatable" strings hidden inside Python expressions in Odoo XML views. It features an interactive TUI (Terminal User Interface) to review candidates and a batch mode to apply safe, AST-based transformations.

## 🛑 The Problem

In Odoo QWeb views, text inside Python attributes is often invisible to the translation system. Odoo's translation crawler looks for standard XML text nodes or explicit `_()` calls, but it struggles with strings mixed into logic inside `t-value`.

**Example:**

```xml
<t t-set="label" t-value="label or 'Proceed to checkout'"/>

```

Because the string is inside a Python expression (`t-value`), it remains hardcoded in English, frustrating international users.

## ✅ The Solution

This tool parses the Python AST (Abstract Syntax Tree) within your XML files. It detects string literals inside logic and refactors the XML structure so that strings become standard text nodes.

**Transformation Example:**

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

It handles:

* **Simple Strings:** `t-value="'Hello'"` → `Hello`
* **OR Conditions:** `t-value="x or 'Default'"` → `t-if` / `t-else`
* **Ternary Operators:** `t-value="'A' if x else 'B'"` → `t-if` / `t-else`

## 🚀 Features

* **Interactive TUI Mode:** Browse all occurrences in your project with a `htop`-like interface.
* **Live Preview:** See exactly how the XML line will be transformed.
* **Search & Filter:** Quickly find keys (e.g., `label`, `title`).
* **Open in Editor:** Press `ENTER` to jump straight to the code in VS Code, Nano, or Vim.


* **Safe AST Parsing:** Uses Python's native `ast` module to ensure it only modifies valid Python expressions, avoiding regex fragility.
* **Configurable Keys:** Targets specific attributes (e.g., `title`, `label`, `placeholder`, `description`) to avoid breaking logic variables.

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

### 2. Dry Run (Safe Check)

Run without flags to scan directories and print what *would* happen without modifying files.

```bash
python3 main.py --path /path/to/your/odoo/module

```

### 3. Fix Mode (Apply Changes)

Use the `--force` flag to write changes to your files.

```bash
python3 main.py --path /path/to/your/odoo/module --force

```

## ⚙️ Configuration

You can customize the `KEY_LIST` inside the script to define which `t-set` keys should be targeted. The default list includes common UI strings:

```python
KEY_LIST = [
    "label", "description", "title", "subtitle", 
    "placeholder", "alt", "button_label", "text", "name"
]

```
