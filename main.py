import os
import re
import argparse
import ast
import curses
import subprocess
import shutil
import sys

# ANSI colors for terminal output
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
RESET = '\033[0m'

# Extended Key List
KEY_LIST = [
    "label", "description", "title", "label_empty", "subtitle", "tooltip_title", 
    "placeholder", "title_content", "alt", "learn_more_text", "button_label", 
    "btn_text", "text", "internal_link_label", "invalid_hint", "submit_label",
    "base_name", "monthly", "primary_button", "cells", "header", "options","error_name","error_message"
] 

# ==========================================
# Helper Functions
# ==========================================

def protect_spacing(text):
    """
    1. Escapes special XML characters BUT avoids double-escaping if they are already entities.
    2. Encodes leading/trailing spaces to &#160; (numeric nbsp).
    """
    if not text: return text
    
    # --- FIX: Prevent Double Escaping ---
    # Only replace '&' if it is NOT followed by a known entity pattern (like amp;, lt;, #160;, etc.)
    text = re.sub(r'&(?!(?:amp|lt|gt|quot|apos|#[0-9]+);)', '&amp;', text)
    
    # Escape brackets
    text = text.replace('<', '&lt;').replace('>', '&gt;')
    
    # Handle spacing preservation for Odoo QWeb (Non-breaking spaces)
    if text.startswith(' '):
        text = '&#160;' + text[1:]
    if text.endswith(' '):
        text = text[:-1] + '&#160;'
        
    return text

def generate_interpolated_xml(text):
    """
    Splits 'Hello #{name}!' into:
    'Hello <t t-esc="name"/>!'
    """
    # Split by the #{...} pattern, keeping the delimiters to identify them
    parts = re.split(r'(#\{[^}]+\})', text)
    xml_output = ""
    
    for part in parts:
        if part.startswith('#{') and part.endswith('}'):
            # Extract content between #{ and }
            variable = part[2:-1]
            xml_output += f'<t t-esc="{variable}"/>'
        else:
            if part:
                xml_output += protect_spacing(part)
                
    return xml_output

# ==========================================
# AST Transformation Logic
# ==========================================

class ExtractionTransformer(ast.NodeTransformer):
    """
    1. Extracts dict values for specific keys.
    2. Extracts string literals found in Concatenations.
    3. Detects Odoo #{...} interpolation.
    """
    def __init__(self, used_names):
        # extracted_vars stores: (var_name, text_content, is_interpolated)
        self.extracted_vars = [] 
        self.used_names = used_names

    def _extract_string(self, text, prefix="txt"):
        """
        Creates a readable variable name.
        """
        # Remove interpolation syntax for the variable name creation
        clean_text = re.sub(r'#\{[^}]+\}', '', text)
        clean = re.sub(r'[^a-zA-Z0-9\s]', '', clean_text)
        clean = re.sub(r'\s+', '_', clean.strip())
        
        if len(clean) > 35:
            clean = clean[:35].rstrip('_')
            
        if not clean: clean = "var"
        
        base_name = f"_{prefix}_{clean}"
        final_name = base_name
        index = 1
        
        while final_name in self.used_names:
            final_name = f"{base_name}_{index}"
            index += 1
            
        self.used_names.add(final_name)
        
        # Check for interpolation
        is_interpolated = '#{' in text and '}' in text
        
        self.extracted_vars.append((final_name, text, is_interpolated))
        return final_name

    def _is_translatable_text(self, text):
        if not text: return False
        if ' ' in text: return True
        if any(c.isupper() for c in text): return True
        if '#{' in text: return True 
        return False

    def visit_Dict(self, node):
        new_keys = []
        new_values = []
        for key, value in zip(node.keys, node.values):
            is_target_key = False
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if key.value in KEY_LIST:
                    is_target_key = True
            
            new_key = self.visit(key)
            new_keys.append(new_key)
            
            if is_target_key and isinstance(value, ast.Constant) and isinstance(value.value, str):
                var_name = self._extract_string(value.value)
                new_val = ast.Name(id=var_name, ctx=ast.Load())
                new_values.append(ast.copy_location(new_val, value))
            else:
                new_values.append(self.visit(value))
        
        node.keys = new_keys
        node.values = new_values
        return node

    def visit_List(self, node):
        self.generic_visit(node)
        return node

    def visit_Tuple(self, node):
        new_elts = []
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                if self._is_translatable_text(elt.value):
                    var_name = self._extract_string(elt.value)
                    new_val = ast.Name(id=var_name, ctx=ast.Load())
                    new_elts.append(ast.copy_location(new_val, elt))
                else:
                    new_elts.append(elt)
            else:
                new_elts.append(self.visit(elt))
        node.elts = new_elts
        return node

def clean_unparse(node):
    try:
        return ast.unparse(node)
    except:
        return ""

def process_python_ast(val_str, used_names_set):
    """
    Returns tuple: (result_data, strategy_type)
    strategy_type: 0 (No), 1 (XML Body), 2 (Extraction)
    """
    val_for_ast = val_str
    
    if '\n' in val_str:
        s_strip = val_str.strip()
        if (s_strip.startswith("'") and s_strip.endswith("'")) or \
           (s_strip.startswith('"') and s_strip.endswith('"')):
            quote = s_strip[0]
            if len(s_strip) >= 2 and s_strip[-1] == quote:
                 inner = s_strip[1:-1]
                 val_for_ast = f'"""{inner}"""'
        elif s_strip.startswith('{') or s_strip.startswith('['):
            pass 
        else:
            val_for_ast = val_str.replace('\n', ' ')

    try:
        tree = ast.parse(val_for_ast, mode='eval')
        body = tree.body
        
        # --- Strategy 2: Extraction ---
        transformer = ExtractionTransformer(used_names=used_names_set)
        new_tree = transformer.visit(body)
        ast.fix_missing_locations(new_tree)
        
        if transformer.extracted_vars:
            lines = []
            for var_name, text, is_interpolated in transformer.extracted_vars:
                if is_interpolated:
                    # Logic: Split #{...} into <t t-esc=""/> nodes inside the t-set body
                    xml_content = generate_interpolated_xml(text)
                    lines.append(f'<t t-set="{var_name}">{xml_content}</t>')
                else:
                    text_prot = protect_spacing(text)
                    lines.append(f'<t t-set="{var_name}">{text_prot}</t>')
            
            return (clean_unparse(new_tree), lines), 2

        # --- Strategy 1: Standard XML Body ---
        if isinstance(body, ast.Constant) and isinstance(body.value, str):
            # If the body itself is a string with #{...}, apply the interpolation logic
            if '#{' in body.value:
                return generate_interpolated_xml(body.value), 1
            
            protected_val = protect_spacing(body.value)
            if '\n' in body.value:
                return f"\n    {protected_val}\n", 1
            return protected_val, 1

        # Handle Conditionals (if/else logic in Python to QWeb)
        if isinstance(body, ast.IfExp):
            condition_str = clean_unparse(body.test)
            
            # Handle True block
            if isinstance(body.body, ast.Constant) and isinstance(body.body.value, str):
                if '#{' in body.body.value:
                    body_xml = generate_interpolated_xml(body.body.value)
                else:
                    body_xml = protect_spacing(body.body.value)
            else:
                body_xml = f'<t t-esc="{clean_unparse(body.body)}"/>'
            
            # Handle Else block
            if isinstance(body.orelse, ast.Constant) and isinstance(body.orelse.value, str):
                if '#{' in body.orelse.value:
                    orelse_xml = generate_interpolated_xml(body.orelse.value)
                else:
                    orelse_xml = protect_spacing(body.orelse.value)
            else:
                orelse_xml = f'<t t-esc="{clean_unparse(body.orelse)}"/>'
            
            xml = (f'<t t-if="{condition_str}">{body_xml}</t>'
                   f'<t t-else="">{orelse_xml}</t>')
            return xml, 1

        return None, 0

    except Exception:
        return None, 0

# ==========================================
# File Scanning & Processing
# ==========================================

def get_files(directory):
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith(".xml"):
                yield os.path.join(root, file)

def clean_str(s):
    return " ".join(s.split())

def scan_file_for_items(file_path, pattern):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return []

    items = []
    matches = list(pattern.finditer(content))
    
    for m in matches:
        start_index = m.start()
        line_no = content[:start_index].count('\n') + 1
        
        attr_name = m.group(3)
        prefix = clean_str(m.group(1)) + f' {attr_name}="'
        val = clean_str(m.group(4))
        suffix = '"' + clean_str(m.group(5)) + '/>'
        
        items.append({
            'file_path': file_path,
            'line_no': line_no,
            'match_obj': m,
            'key': m.group(2),
            'attr_name': attr_name, 
            'val_raw': m.group(4),
            'parts': (prefix, val, suffix),
            'expanded': False 
        })
    return items

def open_in_editor(file_path, line_no):
    editor = os.environ.get('EDITOR')
    if not editor:
        if shutil.which('code'): editor = 'code'
        elif shutil.which('nano'): editor = 'nano'
        else: editor = 'vi'
    try:
        if 'code' in editor:
            subprocess.call([editor, '-g', f"{file_path}:{line_no}"])
        else:
            subprocess.call([editor, f"+{line_no}", file_path])
    except Exception:
        pass

# ==========================================
# TUI & List Mode
# ==========================================

def dump_to_stdout(all_items):
    header = f"{'KEY':<20} | {'LOCATION':<50} | {'XML LINE'}"
    print(header)
    print("-" * 150)
    for i in all_items:
        full_line = f"{i['parts'][0]}{i['val_raw']}{i['parts'][2]}"
        clean_line = " ".join(full_line.split())
        loc = f"{os.path.basename(i['file_path'])}:{i['line_no']}"
        print(f"{i['key']:<20} | {loc:<50} | {clean_line}")

def draw_table(stdscr, filtered_items, current_row, scroll_offset, excluded_count, search_query, is_typing_search):
    height, width = stdscr.getmaxyx()
    col_key_w = 15   
    col_xml_w = 100  
    
    header_str = f" {'KEY':<{col_key_w}} | {'XML LINE':<{col_xml_w}} | {'LOCATION'}"
    stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
    stdscr.addstr(0, 0, header_str[:width])
    stdscr.addstr(0, len(header_str), " " * (width - len(header_str)))
    stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)
    
    if is_typing_search:
        status_bar = f" SEARCH: {search_query}_ "
        bar_attr = curses.color_pair(6) | curses.A_BOLD 
    else:
        search_status = f" [Filter: '{search_query}']" if search_query else ""
        status_bar = f" Total: {len(filtered_items)} | Excluded: {excluded_count}{search_status} | [/] Search | [SPACE] Expand | [ENTER] Edit | [x] Exclude | [r] Reset | [q] Quit"
        bar_attr = curses.A_REVERSE

    try:
        stdscr.move(height - 1, 0)
        stdscr.clrtoeol()
        stdscr.addstr(height - 1, 0, status_bar[:width - 1], bar_attr)
    except curses.error:
        pass

    screen_y = 1 
    
    for i in range(scroll_offset, len(filtered_items)):
        if screen_y >= height - 1:
            break

        item = filtered_items[i]
        is_selected = (i == current_row)
        base_attr = curses.color_pair(2) if is_selected else curses.A_NORMAL
        
        stdscr.attron(base_attr)
        stdscr.move(screen_y, 0)
        stdscr.clrtoeol()
        stdscr.attroff(base_attr)

        key_text = item['key'][:col_key_w]
        stdscr.addstr(screen_y, 0, f" {key_text:<{col_key_w}} ", base_attr if is_selected else curses.A_DIM)
        stdscr.addstr(screen_y, col_key_w + 2, "| ", base_attr)

        current_x = col_key_w + 4
        xml_x_limit = current_x + col_xml_w
        prefix, val, suffix = item['parts']
        
        if current_x < xml_x_limit:
            avail = xml_x_limit - current_x
            stdscr.addstr(screen_y, current_x, prefix[:avail], base_attr)
            current_x += len(prefix[:avail])
        
        val_attr = curses.color_pair(4) | curses.A_BOLD if is_selected else curses.color_pair(3)
        if current_x < xml_x_limit:
            avail = xml_x_limit - current_x
            stdscr.addstr(screen_y, current_x, val[:avail], val_attr)
            current_x += len(val[:avail])
            
        if current_x < xml_x_limit:
            avail = xml_x_limit - current_x
            stdscr.addstr(screen_y, current_x, suffix[:avail], base_attr)
            
        sep_x = col_key_w + 4 + col_xml_w
        if sep_x < width:
            marker = " v " if item['expanded'] else " | "
            stdscr.addstr(screen_y, sep_x, marker, base_attr)
        
        loc_x = sep_x + 3
        if loc_x < width:
            loc_str = f"{os.path.basename(item['file_path'])}:{item['line_no']}"
            stdscr.addstr(screen_y, loc_x, loc_str[:width - loc_x - 1], base_attr)

        screen_y += 1
        
        if item['expanded']:
            if screen_y >= height - 1: break
            stdscr.attron(base_attr)
            stdscr.move(screen_y, 0)
            stdscr.clrtoeol()
            
            val_to_test = item['val_raw']
            # Treat t-valuef as quoted string for AST parse logic check
            if item['attr_name'] == 't-valuef':
                val_to_test = f'"{val_to_test}"'

            res_data, strategy = process_python_ast(val_to_test, used_names_set=set())
            full_text = "Preview Error"
            
            if strategy > 0:
                if strategy == 2:
                    new_dict, xml_lines = res_data
                    prefix_lines = "\n   ".join(xml_lines)
                    full_text = f"{prefix_lines}\n   {item['parts'][0]}{new_dict}{item['parts'][2]}"
                else:
                    p_clean = re.sub(r'\s+t-valuef?="$', '', item['parts'][0])
                    s_clean = item['parts'][2].replace('/>', '>')
                    full_text = f"{p_clean}{s_clean}{res_data}</t>"
            else:
                full_text = "No conversion needed or failed"

            indent = col_key_w + 4
            available_w = width - indent - 1
            if available_w > 10:
                lines = [full_text[i:i+available_w] for i in range(0, len(full_text), available_w)]
                for line in lines:
                    if screen_y >= height - 1: break
                    stdscr.addstr(screen_y, indent, line, curses.color_pair(6))
                    screen_y += 1
            stdscr.attroff(base_attr)

def tui_mode(directory, pattern):
    all_items = []
    if sys.stdout.isatty(): print("Scanning files... please wait.")
    files = list(get_files(directory))
    for f in files:
        all_items.extend(scan_file_for_items(f, pattern))
    if not all_items:
        if sys.stdout.isatty(): print("No matches found.")
        return
    all_items.sort(key=lambda x: (x['key'], x['file_path'], x['line_no']))

    if not sys.stdout.isatty():
        dump_to_stdout(all_items)
        return

    def run_curses(stdscr):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)   
        curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE) 
        curses.init_pair(3, curses.COLOR_GREEN, -1) 
        curses.init_pair(4, curses.COLOR_GREEN, curses.COLOR_WHITE)
        curses.init_pair(5, curses.COLOR_CYAN, -1)
        curses.init_pair(6, curses.COLOR_YELLOW, -1)
        curses.curs_set(0) 
        stdscr.timeout(100)
        current_row = 0
        scroll_offset = 0
        excluded_keys = set()
        search_query = ""
        is_typing_search = False
        
        while True:
            stdscr.erase()
            filtered_items = [i for i in all_items if i['key'] not in excluded_keys and (not search_query or search_query.lower() in i['key'].lower())]
            
            if filtered_items:
                if current_row >= len(filtered_items): current_row = len(filtered_items) - 1
                if current_row < 0: current_row = 0
            
            draw_table(stdscr, filtered_items, current_row, scroll_offset, len(excluded_keys), search_query, is_typing_search)
            key = stdscr.getch()

            if is_typing_search:
                if key in [10, 13]: is_typing_search = False
                elif key == 27: is_typing_search = False; search_query = ""
                elif key in [curses.KEY_BACKSPACE, 127, 8]: search_query = search_query[:-1]; current_row = 0
                elif 32 <= key <= 126: search_query += chr(key); current_row = 0
            else:
                if key == ord('q'): break
                elif key == ord('/'): is_typing_search = True
                elif key == curses.KEY_DOWN:
                    if filtered_items and current_row < len(filtered_items) - 1:
                        current_row += 1
                        if current_row > scroll_offset + 15: scroll_offset += 1
                elif key == curses.KEY_UP:
                    if current_row > 0:
                        current_row -= 1
                        if current_row < scroll_offset: scroll_offset = current_row
                elif key == ord(' '): 
                    if filtered_items: filtered_items[current_row]['expanded'] = not filtered_items[current_row]['expanded']
                elif key == 10: 
                     if filtered_items:
                        curses.endwin()
                        open_in_editor(filtered_items[current_row]['file_path'], filtered_items[current_row]['line_no'])
                        stdscr.refresh()
                elif key == ord('x'):
                    if filtered_items: excluded_keys.add(filtered_items[current_row]['key']); current_row = 0; scroll_offset = 0
                elif key == ord('r'): excluded_keys.clear(); current_row = 0; scroll_offset = 0

    curses.wrapper(run_curses)

# ==========================================
# Fix Logic
# ==========================================

def process_file_fix(file_path, pattern, dry_run):
    try:
        with open(file_path, 'r', encoding='utf-8') as f: content = f.read()
        matches = list(pattern.finditer(content))
        if not matches: return

        matches_to_replace = list(matches)
        matches_to_replace.sort(key=lambda x: x.start(), reverse=True)
        files_modified = False
        new_content = content
        file_used_names = set()
        
        for m in matches_to_replace:
            prefix_attrs = m.group(1) 
            key = m.group(2)
            attr_name = m.group(3)          
            val_raw = m.group(4)      
            suffix_attrs = m.group(5) 

            if key not in KEY_LIST: continue

            val_for_ast = val_raw
            if attr_name == 't-valuef': val_for_ast = f'"{val_raw}"'

            res_data, strategy = process_python_ast(val_for_ast, file_used_names)
            if strategy == 0: continue
            if attr_name == 't-value' and strategy == 1 and res_data == val_raw: continue

            replacement = ""
            if strategy == 2:
                # STRATEGY: Extraction (Dicts/Lists/Interpolation)
                new_dict, xml_lines = res_data
                extracted_block = "\n".join(xml_lines)
                main_tag = f'{prefix_attrs} t-value="{new_dict}"{suffix_attrs}/>'
                replacement = f'{extracted_block}\n{main_tag}'
            else:
                # STRATEGY: XML Body
                replacement = f'{prefix_attrs}{suffix_attrs}>{res_data}</t>'

            if not files_modified:
                print(f"{GREEN}Processing: {file_path}{RESET}")
                files_modified = True

            print(f"  - Transforming '{key}' ({attr_name})")
            disp_old = (val_raw[:50] + '..') if len(val_raw) > 50 else val_raw
            print(f"    Old: {disp_old}")
            print(f"    New:\n{GREEN}{replacement}{RESET}")
            
            start = m.start()
            end = m.end()
            new_content = new_content[:start] + replacement + new_content[end:]

        if files_modified:
            if not dry_run:
                with open(file_path, 'w', encoding='utf-8') as f: f.write(new_content)
                print(f"  {RED}[SAVED]{RESET}")
            else:
                print(f"  {YELLOW}[SKIPPED - Dry Run]{RESET}")
            print("-" * 40)

    except Exception as e:
        print(f"{RED}Error reading {file_path}: {e}{RESET}")

def run_fix_mode(directory, pattern, dry_run):
    print(f"{YELLOW}Scanning directory: {directory}{RESET}")
    print(f"{YELLOW}Mode: {'DRY RUN' if dry_run else 'LIVE'}{RESET}\n")
    files = list(get_files(directory))
    for f in files: process_file_fix(f, pattern, dry_run)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Odoo t-value Tool")
    parser.add_argument("--path", default="my_compasion", help="Root directory to scan (default: my_compasion)")
    parser.add_argument("--force", action="store_true", help="Apply changes (disable dry-run in fix mode)")
    parser.add_argument("--list", action="store_true", help="Open dynamic CLI view to list all t-values")
    
    args = parser.parse_args()
    pattern = re.compile(r'(<t\s+[^>]*\bt-set="([^"]+)"[^>]*)\s+(t-valuef?)="([^"]+)"([^>]*?)\s*/>', re.DOTALL)

    if args.list:
        try: tui_mode(args.path, pattern)
        except ImportError: print("Error: 'curses' library not found.")
    else:
        run_fix_mode(args.path, pattern, dry_run=not args.force)