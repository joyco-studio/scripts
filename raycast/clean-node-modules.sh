#!/bin/bash

# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Clean Node Modules
# @raycast.mode fullOutput

# Optional parameters:
# @raycast.icon 🧹
# @raycast.packageName JOYCO Developer Tools
# @raycast.argument1 { "type": "text", "placeholder": "directory (e.g. ~/Documents/old)" }
# @raycast.argument2 { "type": "text", "placeholder": "older than (e.g. 1m, 2w, 90d)", "optional": true }
# @raycast.argument3 { "type": "dropdown", "placeholder": "mode", "optional": true, "data": [{ "title": "Preview", "value": "preview" }, { "title": "Delete", "value": "delete" }] }
# Recursively finds node_modules, .next, and .turbo directories under the given
# directory and removes them. Raycast script commands have no stdin, so there is
# no interactive [y/N] prompt: "Preview" (the default) only lists candidates and
# "Delete" removes them. Always run a Preview first to review the exact scope.

set -u
unset CDPATH

path_arg=${1:-}
age=${2:-}
mode=${3:-preview}

case "$mode" in
  preview|delete) ;;
  *)
    printf 'Unknown mode: %s. Use preview or delete.\n' "$mode" >&2
    exit 2
    ;;
esac

case "$path_arg" in
  "~") path_arg=$HOME ;;
  "~/"*) path_arg="$HOME/${path_arg#\~/}" ;;
esac

age_days=
if [[ -n "$age" ]]; then
  if [[ ! "$age" =~ ^[1-9][0-9]*(d|w|m)$ ]]; then
    printf 'Invalid age: %s. Use a positive number followed by d, w, or m (e.g. 1m).\n' "$age" >&2
    exit 2
  fi
  age_value=${age%[dwm]}
  if ((${#age_value} > 6)); then
    printf 'Age is too large; maximum is 365000 days.\n' >&2
    exit 2
  fi
  case "${age: -1}" in
    d) age_days=$age_value ;;
    w) age_days=$((age_value * 7)) ;;
    m) age_days=$((age_value * 30)) ;;
  esac
  if ((age_days > 365000)); then
    printf 'Age is too large; maximum is 365000 days.\n' >&2
    exit 2
  fi
fi

if [[ -z "$path_arg" || ! -d "$path_arg" ]]; then
  printf 'Please provide an existing directory.\n' >&2
  exit 2
fi

resolve_directory() (
  cd -P -- "$1" 2>/dev/null && printf '%s/.' "$PWD"
)

root=$(resolve_directory "$path_arg") || {
  printf 'Could not resolve directory: %q\n' "$path_arg" >&2
  exit 2
}
root=${root%/.}

home_root=$(resolve_directory "${HOME:-}") || {
  printf 'Could not resolve HOME; refusing to delete directories.\n' >&2
  exit 2
}
home_root=${home_root%/.}

if [[ "$root" == / ]]; then
  printf 'Refusing to clean from /. Choose a narrower project directory.\n' >&2
  exit 2
fi

case "$home_root/" in
  "$root/"*)
    printf 'Refusing to clean from %q. Choose a narrower project directory.\n' "$root" >&2
    exit 2
    ;;
esac

case "${root##*/}" in
  node_modules|.next|.turbo)
    printf 'Choose the project directory containing %q as the directory.\n' "$root" >&2
    exit 2
    ;;
esac

if [[ "$(uname -s)" != Darwin ]]; then
  printf 'Unsupported operating system; refusing to delete directories.\n' >&2
  exit 2
fi

directory_identity() {
  [[ -d "$1" && ! -L "$1" ]] && command stat -f '%d:%i' -- "$1"
}

root_identity=$(directory_identity "$root") || exit 1
scan_file=$(mktemp "${TMPDIR:-/tmp}/clean-node-modules.XXXXXXXX") || exit 1
trap 'command rm -f -- "$scan_file"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

find_args=(-P "$root" -xdev -mindepth 1)
find_args+=(-type d '(' -name node_modules -o -name .next -o -name .turbo ')' -prune)
if [[ -n "$age_days" ]]; then
  find_args+=(-mtime "+${age_days}d")
fi
if ! command find "${find_args[@]}" -print0 > "$scan_file"; then
  printf 'Directory scan failed; nothing was deleted.\n' >&2
  exit 1
fi

targets=()
target_identities=()
parent_identities=()
while IFS= read -r -d '' target; do
  case "$target" in
    "$root/"*) ;;
    *) printf 'Scan returned a path outside the selected root; aborting.\n' >&2; exit 1 ;;
  esac
  target_identity=$(directory_identity "$target") || exit 1
  parent_identity=$(directory_identity "${target%/*}") || exit 1
  if [[ "${target_identity%%:*}" != "${root_identity%%:*}" ]]; then
    continue
  fi
  targets+=("$target")
  target_identities+=("$target_identity")
  parent_identities+=("$parent_identity")
done < "$scan_file"

if ((${#targets[@]} == 0)); then
  printf 'No matching generated directories found under %q.\n' "$root"
  exit 0
fi

printf 'Selected directories (shell-escaped paths):\n'
printf '  %q\n' "${targets[@]}"
if [[ -n "$age_days" ]]; then
  printf 'Age is based only on each directory timestamp; files inside may be newer.\n'
fi
if [[ "$mode" == preview ]]; then
  printf 'Preview: nothing was deleted. Run again with mode "Delete" to remove %d director%s.\n' \
    "${#targets[@]}" "$([[ ${#targets[@]} -eq 1 ]] && printf 'y' || printf 'ies')"
  exit 0
fi

validate_target() (
  index=$1
  target=${targets[$index]}
  parent=${target%/*}
  [[ "$(directory_identity "$root")" == "$root_identity" ]] || exit 1
  cd -P -- "$parent" || exit 1
  [[ "$PWD" == "$parent" ]] || exit 1
  [[ "$(directory_identity .)" == "${parent_identities[$index]}" ]] || exit 1
  [[ "$(directory_identity "./${target##*/}")" == "${target_identities[$index]}" ]] || exit 1
  case "${2:-}" in
    delete)
      command rm -rfx -- "./${target##*/}" || exit $?
      ;;
    metadata-only)
      command find -P "./${target##*/}" -xdev ! -type d \
        ! \( -type f -name .DS_Store \) -print0 > "$scan_file" || exit 1
      [[ ! -s "$scan_file" ]] || exit 1
      ;;
  esac
)

for index in "${!targets[@]}"; do
  if ! validate_target "$index"; then
    printf 'Directory changed since the scan; nothing was deleted: %q\n' "${targets[$index]}" >&2
    exit 1
  fi
done
removed_count=0
for index in "${!targets[@]}"; do
  attempt=1
  while ! validate_target "$index" delete; do
    if ((attempt >= 3)) || ! validate_target "$index" metadata-only; then
      printf 'Deletion stopped at %q after removing %d directories. The failed directory may be partially deleted.\n' \
        "${targets[$index]}" "$removed_count" >&2
      printf 'Close Finder windows for this project and stop any builds or installs before retrying.\n' >&2
      exit 1
    fi
    attempt=$((attempt + 1))
    printf 'Only directories and Finder metadata remain; retrying %q (attempt %d/3).\n' \
      "${targets[$index]}" "$attempt" >&2
    command sleep 0.2 || exit 1
  done
  removed_count=$((removed_count + 1))
done

printf 'Removed %d generated director%s.\n' "${#targets[@]}" "$([[ ${#targets[@]} -eq 1 ]] && printf 'y' || printf 'ies')"
