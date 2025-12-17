import os
import re
import argparse
import ast
import curses
import subprocess
import shutil

# ANSI colors for terminal output
GREEN = '\033[92m'
YELLOW = '\033[93m'
RED = '\033[91m'
RESET = '\033[0m'

# Default list of keys to convert
KEY_LIST = ["label", "description", "title", "label_empty", "subtitle", "tooltip_title", 
            "placeholder", "title_content", "alt", "learn_more_text", "button_label", 
            "btn_text", "text", "internal_link_label", "invalid_hint", "submit_label","base_name","monthly"]

# ==========================================
# XML Generation Logic (Smart Translation)
# ==========================================

def clean_unparse(node):
    """Safely unparse an AST node to a string."""
    try:
        return ast.unparse(node)
    except:
        return ""

def ast_to_xml(node):
    """
    Converts AST nodes into Odoo XML. 
    Prioritizes creating XML text nodes for strings so Odoo can translate them.
    """
    
    # --- Case 1: Simple String Literal ---
    # t-value="'Hello'" -> Hello
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value

    # --- Case 2: Boolean OR with a default string ---
    # t-value="variable or 'Default'"
    # -> <t t-if="variable" t-esc="variable"/><t t-else="">Default</t>
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        # We handle the common case: (Complex Logic) OR 'String Literal'
        if isinstance(node.values[-1], ast.Constant) and isinstance(node.values[-1].value, str):
            string_val = node.values[-1].value
            
            # The "condition" is everything before the OR
            # If there are multiple values before the last one, we must re-join them logic-wise
            if len(node.values) == 2:
                condition_node = node.values[0]
            else:
                condition_node = ast.BoolOp(op=ast.Or(), values=node.values[:-1])
            
            condition_str = clean_unparse(condition_node)
            
            return (f'<t t-if="{condition_str}"><t t-esc="{condition_str}"/></t>'
                    f'<t t-else="">{string_val}</t>')

    # --- Case 3: If/Else Expression (Ternary Operator) ---
    # t-value="'A' if cond else 'B'" or "var if cond else 'B'"
    if isinstance(node, ast.IfExp):
        condition_str = clean_unparse(node.test)
        
        # Process Body (True case)
        if isinstance(node.body, ast.Constant) and isinstance(node.body.value, str):
            body_xml = node.body.value # Pure text
        else:
            body_xml = f'<t t-esc="{clean_unparse(node.body)}"/>'

        # Process Orelse (False case)
        if isinstance(node.orelse, ast.Constant) and isinstance(node.orelse.value, str):
            orelse_xml = node.orelse.value # Pure text
        else:
            orelse_xml = f'<t t-esc="{clean_unparse(node.orelse)}"/>'

        return (f'<t t-if="{condition_str}">{body_xml}</t>'
                f'<t t-else="">{orelse_xml}</t>')

    # --- Case 4: Translation Call _('String') ---
    # If explicit translation is used, just unwrap it to text if it's simple
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ['_', '_lt']:
         if len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
             return node.args[0].value

    # --- Case 5: Fallback to Python evaluation ---
    # For standard variables or math: t-value="x + 1" -> <t t-esc="x + 1"/>
    return f'<t t-esc="{clean_unparse(node)}"/>'

def contains_string_literal(node):
    """
    Checks if the expression involves a string literal that we want to expose.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.BoolOp):
        return any(contains_string_literal(v) for v in node.values)
    if isinstance(node, ast.IfExp):
        return contains_string_literal(node.body) or contains_string_literal(node.orelse)
    return False

def is_candidate_for_conversion(node):
    """
    Filter to ensure we only convert lines that actually contain a string.
    """
    # Direct string
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    # Explicit translation call
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ['_', '_lt']:
        return True
    # Logic containing string (Or, IfExp)
    if isinstance(node, (ast.BoolOp, ast.IfExp)):
        return contains_string_literal(node)
    return False

def convert_python_expression(val_str):
    # Fix for multi-line strings in attributes
    if '\n' in val_str:
        if val_str.strip().startswith("'") or val_str.strip().startswith('"'):
            val_str = val_str.replace('\n', ' ')
    try:
        tree = ast.parse(val_str, mode='eval')
        
        if not is_candidate_for_conversion(tree.body):
            return None

        return ast_to_xml(tree.body)
    except Exception:
        return None

# ==========================================
# File Scanning Logic
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
        
        prefix = clean_str(m.group(1)) + ' t-value="'
        val = clean_str(m.group(3))
        suffix = '"' + clean_str(m.group(4)) + '/>'
        
        items.append({
            'file_path': file_path,
            'line_no': line_no,
            'match_obj': m,
            'key': m.group(2),
            'val_raw': m.group(3),
            'parts': (prefix, val, suffix),
            'expanded': False 
        })
    return items

# ==========================================
# Editor Logic
# ==========================================

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
# TUI (Text User Interface)
# ==========================================

def draw_table(stdscr, filtered_items, current_row, scroll_offset, excluded_count, search_query, is_typing_search):
    height, width = stdscr.getmaxyx()
    col_key_w = 12   
    col_xml_w = 105  
    
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

    max_display_rows = height - 2
    screen_y = 1 
    
    for i in range(scroll_offset, len(filtered_items)):
        if screen_y >= height - 1:
            break

        item = filtered_items[i]
        prev_item = filtered_items[i - 1] if i > 0 else None
        
        is_new_group = (prev_item is None) or (item['key'] != prev_item['key'])
        is_selected = (i == current_row)
        
        base_attr = curses.color_pair(2) if is_selected else curses.A_NORMAL
        
        stdscr.attron(base_attr)
        stdscr.move(screen_y, 0)
        stdscr.clrtoeol()
        stdscr.attroff(base_attr)

        key_text = item['key'][:col_key_w]
        if is_selected:
            key_attr = base_attr
        elif is_new_group:
            key_attr = curses.color_pair(5) | curses.A_BOLD
        else:
            key_attr = curses.A_DIM
            if i == scroll_offset: key_attr = curses.color_pair(5)

        stdscr.addstr(screen_y, 0, f" {key_text:<{col_key_w}} ", key_attr)
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
            if screen_y >= height - 1:
                break
            
            stdscr.attron(base_attr)
            stdscr.move(screen_y, 0)
            stdscr.clrtoeol()
            
            new_val = convert_python_expression(item['val_raw'])
            if new_val:
                # Basic approximation for preview purposes
                full_text = f"{item['parts'][0]}{item['parts'][2].replace('/>', '>')}{new_val}</t>"
            else:
                full_text = "Conversion Failed or Not Needed"

            indent = col_key_w + 4
            max_len = width - indent - 1
            if max_len > 0:
                stdscr.addstr(screen_y, indent, full_text[:max_len], curses.color_pair(6)) 
            
            stdscr.attroff(base_attr)
            screen_y += 1

def tui_mode(directory, pattern):
    all_items = []
    print("Scanning files... please wait.")
    files = list(get_files(directory))
    for f in files:
        all_items.extend(scan_file_for_items(f, pattern))
    
    if not all_items:
        print("No matches found.")
        return

    all_items.sort(key=lambda x: (x['key'], x['file_path'], x['line_no']))

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
            
            filtered_items = [
                i for i in all_items 
                if i['key'] not in excluded_keys 
                and (not search_query or search_query.lower() in i['key'].lower())
            ]
            
            if filtered_items:
                if current_row >= len(filtered_items):
                    current_row = len(filtered_items) - 1
                if current_row < 0: current_row = 0
            
            draw_table(stdscr, filtered_items, current_row, scroll_offset, len(excluded_keys), search_query, is_typing_search)
            
            key = stdscr.getch()

            if is_typing_search:
                if key in [10, 13]: is_typing_search = False
                elif key == 27: 
                    is_typing_search = False; search_query = ""
                elif key in [curses.KEY_BACKSPACE, 127, 8]:
                    search_query = search_query[:-1]; current_row = 0
                elif 32 <= key <= 126:
                    search_query += chr(key); current_row = 0
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
                    if filtered_items:
                        excluded_keys.add(filtered_items[current_row]['key'])
                        current_row = 0; scroll_offset = 0
                elif key == ord('r'):
                    excluded_keys.clear(); current_row = 0; scroll_offset = 0

    curses.wrapper(run_curses)

# ==========================================
# Fix Logic (Applying changes)
# ==========================================

def process_file_fix(file_path, pattern, dry_run):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        matches = list(pattern.finditer(content))
        if not matches: return

        matches_to_replace = list(matches)
        matches_to_replace.sort(key=lambda x: x.start(), reverse=True)
        
        files_modified = False
        new_content = content
        
        for m in matches_to_replace:
            prefix_attrs = m.group(1) 
            key = m.group(2)          
            val_raw = m.group(3)      
            suffix_attrs = m.group(4) 

            if key not in KEY_LIST: continue

            new_val = convert_python_expression(val_raw)
            if new_val is None: continue
            
            replacement = f'{prefix_attrs}{suffix_attrs}>{new_val}</t>'
            
            if not files_modified:
                print(f"{GREEN}Processing: {file_path}{RESET}")
                files_modified = True

            print(f"  - Transforming '{key}'")
            print(f"    Old: {val_raw}")
            print(f"    New: {new_val}")
            
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
    for f in files:
        process_file_fix(f, pattern, dry_run)

# ==========================================
# Main Entry Point
# ==========================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Odoo t-value Tool")
    parser.add_argument("--path", default="my_compasion", help="Root directory to scan (default: my_compasion)")
    parser.add_argument("--force", action="store_true", help="Apply changes (disable dry-run in fix mode)")
    parser.add_argument("--list", action="store_true", help="Open dynamic CLI view to list all t-values")
    
    args = parser.parse_args()
    pattern = re.compile(r'(<t\s+[^>]*\bt-set="([^"]+)"[^>]*)\s+t-value="([^"]+)"([^>]*?)\s*/>', re.DOTALL)

    if args.list:
        try: tui_mode(args.path, pattern)
        except ImportError: print("Error: 'curses' library not found.")
    else:
        run_fix_mode(args.path, pattern, dry_run=not args.force)