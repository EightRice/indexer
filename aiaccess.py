#!/usr/bin/env python3
import os
import sys
from pathlib import Path
from pathspec import PathSpec
import argparse # Added for command-line argument parsing

def load_access(root_dir: Path):
    """
    Parse .aiaccess. Lines starting with '!' are include patterns; others ignore patterns.
    Always ignore all '*.yaml' files anywhere and any '__pycache__/' directories anywhere.
    Directory patterns (ending with '/') match nested contents; bare directory names
    are treated as directories when included.
    Returns:
      ignore_spec: PathSpec for ignore patterns
      include_spec: PathSpec for include patterns
      include_raw: list of raw include patterns (pre-processed)
      has_includes: bool
    """
    file = root_dir / '.aiaccess'
    ignore_lines, include_lines = [], []

    # default global ignores
    ignore_lines.extend(["**/*.yaml", "**/__pycache__/"])

    if file.exists():
        for line in file.read_text(encoding='utf-8').splitlines():
            raw = line.strip()
            if not raw or raw.startswith('#'):
                continue
            if raw.startswith('!'):
                include_lines.append(raw[1:].strip())
            else:
                ignore_lines.append(raw)

    # detect literal conflicts
    conflicts = set(ignore_lines) & set(include_lines)
    if conflicts:
        print(f"Error: conflicting patterns in .aiaccess: {conflicts}", file=sys.stderr)
        sys.exit(1)

    # expand directory ignores
    def expand(lines):
        out = []
        for p in lines:
            if p.endswith('/'):
                out.append(p)
                out.append(p + '**')
            else:
                out.append(p)
        return out

    ignore_patterns = expand(ignore_lines)
    include_patterns = expand(include_lines)

    ignore_spec = PathSpec.from_lines('gitwildmatch', ignore_patterns)
    include_spec = PathSpec.from_lines('gitwildmatch', include_patterns)

    include_raw = include_lines.copy()
    has_includes = bool(include_raw)
    return ignore_spec, include_spec, include_raw, has_includes

def compute_include_dirs(include_raw: list) -> list:
    """
    From raw include patterns, derive a list of directories to preserve.
    For each pattern:
      - If it ends with '/', take that dir.
      - Else if it's a path, take its parent folder.
      - Else ignore (file in root).
    Normalize with trailing '/'.
    """
    include_dirs = set()
    for p in include_raw:
        if p.endswith('/'):
            dirp = p.rstrip('/')
        else:
            parent = Path(p).parent
            if str(parent) == '.': # Check against string representation of CWD
                continue
            dirp = parent.as_posix()
        if dirp: # Ensure dirp is not an empty string
            include_dirs.add(dirp.rstrip('/') + '/')
    return sorted(include_dirs)

def is_excluded(path: Path, root_dir: Path,
                ignore_spec: PathSpec, include_spec: PathSpec,
                include_dirs: list, has_includes: bool) -> bool:
    """
    Exclusion logic:
      - root_dir never excluded
      - always drop paths matching ignore_spec
      - when whitelisting:
          * preserve any directory whose path is a prefix of an include_dir
          * drop paths not matching include_spec
    """
    if path == root_dir:
        return False
    try:
        rel = path.relative_to(root_dir)
    except ValueError:
        # This can happen if path is not under root_dir, should not occur with os.walk
        return True # Exclude if not relative (safety)
    
    rel_posix = rel.as_posix()
    if path.is_dir() and not rel_posix.endswith('/'):
        rel_posix += '/'

    # global ignores
    if ignore_spec.match_file(rel_posix):
        return True

    if has_includes:
        # preserve parent dirs of included patterns
        # This ensures that if '!foo/bar/baz.txt' is included, 'foo/' and 'foo/bar/' are kept
        if path.is_dir():
            for idir_prefix in include_dirs: # include_dirs are like 'foo/', 'foo/bar/'
                if idir_prefix.startswith(rel_posix): # if 'foo/' starts with 'foo/' (match) OR 'foo/bar/' starts with 'foo/' (match)
                    return False # Keep this directory as it's a parent of or is an explicitly included directory path

        # drop non-whitelisted
        # This checks if the current item itself (file or dir) matches an include pattern
        if not include_spec.match_file(rel_posix):
            # If it's a directory, and it's not a prefix to any whitelisted item (checked above),
            # and it itself doesn't match an include pattern, then exclude.
            # If it's a file, and it doesn't match an include pattern, then exclude.
            return True
            
    return False # Default to not excluded if no rules apply or no whitelisting is active

def generate_tree_structure(root_dir: Path,
                            ignore_spec: PathSpec, include_spec: PathSpec,
                            include_dirs: list, has_includes: bool) -> str:
    tree_lines = []
    
    # Sort function for directory and file names
    def sort_key(item_name):
        return item_name.lower()

    # We need a way to explore paths and decide if they should be pruned
    # os.walk is good, but we need to prune its `dirs` list carefully.

    # First, collect all items that *should* be included to build a picture
    # of the structure. This can be complex.
    # A simpler os.walk approach with careful pruning:

    paths_to_render = {} # Store levels and names

    for current_root_str, dir_names, file_names in os.walk(root_dir, topdown=True):
        current_root_path = Path(current_root_str)

        # Prune directories based on exclusion rules BEFORE recursing into them
        # A directory is kept if:
        # 1. It's not explicitly ignored.
        # 2. If whitelisting is active:
        #    a. It matches an include pattern OR
        #    b. It's a necessary parent directory for an included item.
        
        original_dir_names = list(dir_names) # Keep a copy for iteration
        dir_names[:] = [] # Clear and rebuild

        for d_name in sorted(original_dir_names, key=sort_key):
            dir_path = current_root_path / d_name
            if not is_excluded(dir_path, root_dir, ignore_spec, include_spec, include_dirs, has_includes):
                dir_names.append(d_name)

        # If current_root_path itself is excluded, we shouldn't process it or its files/dirs further.
        # However, os.walk might have already entered it if a parent wasn't excluded.
        # is_excluded check on current_root_path ensures its own listing is skipped if needed.
        if is_excluded(current_root_path, root_dir, ignore_spec, include_spec, include_dirs, has_includes):
            # If current_root is excluded, os.walk's dir_names modification won't stop it
            # from yielding this root, so we must 'continue' to skip processing its files/listing.
            # This primarily handles cases where a directory might be ignored but its parent isn't.
            dir_names[:] = [] # Make sure we don't process subdirs of an excluded dir
            continue

        rel_path = current_root_path.relative_to(root_dir)
        level = len(rel_path.parts)
        
        # Add current directory to tree
        # The root directory itself (level 0) is handled slightly differently for name
        dir_display_name = current_root_path.name if current_root_path != root_dir else root_dir.name
        tree_lines.append(f"{'  ' * level}- {dir_display_name}/")

        # Add files in the current directory
        for f_name in sorted(file_names, key=sort_key):
            file_path = current_root_path / f_name
            if not is_excluded(file_path, root_dir, ignore_spec, include_spec, include_dirs, has_includes):
                tree_lines.append(f"{'  ' * (level + 1)}- {f_name}")
                
    return "\n".join(tree_lines) + "\n" if tree_lines else ""


def write_file_contents_markdown(root_dir: Path,
                                 ignore_spec: PathSpec, include_spec: PathSpec,
                                 include_dirs: list, has_includes: bool) -> str:
    md_parts = []
    
    # Sort function for directory and file names
    def sort_key(item_name):
        return item_name.lower()

    for current_root_str, dir_names, file_names in os.walk(root_dir, topdown=True):
        current_root_path = Path(current_root_str)

        # Prune dir_names similar to generate_tree_structure
        original_dir_names = list(dir_names)
        dir_names[:] = [] 
        for d_name in sorted(original_dir_names, key=sort_key):
            dir_path = current_root_path / d_name
            if not is_excluded(dir_path, root_dir, ignore_spec, include_spec, include_dirs, has_includes):
                dir_names.append(d_name)
        
        # If current_root_path itself is excluded, skip processing its files
        if is_excluded(current_root_path, root_dir, ignore_spec, include_spec, include_dirs, has_includes):
            dir_names[:] = [] # Prevent further traversal into subdirs of an excluded dir
            continue

        for filename in sorted(file_names, key=sort_key):
            file_path = current_root_path / filename
            if not is_excluded(file_path, root_dir, ignore_spec, include_spec, include_dirs, has_includes):
                rel_path_posix = file_path.relative_to(root_dir).as_posix()
                ext = file_path.suffix.lstrip('.') or 'text' # Use 'text' for no extension
                
                md_parts.append(f"\n### `{rel_path_posix}`\n```{ext}\n")
                try:
                    content = file_path.read_text(encoding='utf-8')
                    # Basic sanitization for triple backticks in content
                    if "```" in content:
                        content = content.replace("```", "` ``") 
                    md_parts.append(content)
                except Exception as e:
                    md_parts.append(f"# Error: Could not read file: {e}\n")
                md_parts.append("\n```\n")
                
    return "".join(md_parts)

def main():
    parser = argparse.ArgumentParser(
        description="Generate Markdown documentation for a project directory based on .aiaccess rules.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter # Show default values in help
    )
    parser.add_argument(
        "target_directory", 
        nargs="?", 
        default=".", 
        help="The root directory of the project to scan. '.aiaccess' should be in this directory."
    )
    args = parser.parse_args()

    # Resolve to an absolute path and ensure it's a directory
    try:
        root_dir = Path(args.target_directory).resolve(strict=True)
    except FileNotFoundError:
        print(f"Error: Target directory '{args.target_directory}' not found.", file=sys.stderr)
        sys.exit(1)
        
    if not root_dir.is_dir():
        print(f"Error: Target '{root_dir}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    print(f"Processing project in: {root_dir}")

    ignore_spec, include_spec, include_raw, has_includes = load_access(root_dir)
    
    # Recalculate include_dirs based on the possibly different root_dir context
    # compute_include_dirs uses Path objects, so patterns should be relative to root_dir
    include_dirs = compute_include_dirs(include_raw) if has_includes else []
    
    # Debugging prints (optional, can be removed after testing)
    # print(f"Ignore patterns: {ignore_spec.patterns if ignore_spec else 'None'}")
    # print(f"Include patterns (raw): {include_raw}")
    # print(f"Include patterns (spec): {include_spec.patterns if include_spec else 'None'}")
    # print(f"Has includes: {has_includes}")
    # print(f"Computed include_dirs: {include_dirs}")


    output_filename = f"{root_dir.name}.md"
    # Output the .md file in the directory where the script is run, or in root_dir?
    # For simplicity, let's output in the current working directory or specify path
    # Or, consistently put it inside the root_dir
    output_path = root_dir / output_filename 

    try:
        with open(output_path, 'w', encoding='utf-8') as out_file:
            out_file.write(f"# Project: {root_dir.name}\n\n")
            out_file.write("## Folder Structure\n\n")
            tree_content = generate_tree_structure(root_dir,
                                                ignore_spec, include_spec,
                                                include_dirs, has_includes)
            out_file.write(tree_content)
            
            out_file.write("\n## File Contents\n")
            files_content = write_file_contents_markdown(root_dir,
                                                        ignore_spec, include_spec,
                                                        include_dirs, has_includes)
            out_file.write(files_content)

        print(f"Markdown file created: {output_path}")

    except Exception as e:
        print(f"An error occurred during processing: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()